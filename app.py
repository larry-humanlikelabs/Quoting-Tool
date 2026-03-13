import streamlit as st
import pandas as pd
import numpy as np
import os
import io
import base64
from datetime import datetime
from fpdf import FPDF
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode
from utils.audit_logger import log_quote_locked_in, generate_quote_id

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BV Fulfillment Quoting Tool",
    page_icon="📦",
    layout="wide",
)

# ─── Load DHL rates from CSV ─────────────────────────────────────────────────
@st.cache_data
def load_dhl_rates() -> tuple[dict[int, float], dict[int, float]]:
    """Load DHL Zone 6 rates from dhl_rates.csv.
    Returns (oz_rates, lb_rates) dicts mapping weight -> US 06 price.
    """
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dhl_rates.csv")
    df = pd.read_csv(csv_path)
    oz_rates = df[df["weight_type"] == "OZ"].set_index("weight")["US 06"].to_dict()
    lb_rates = df[df["weight_type"] == "LB"].set_index("weight")["US 06"].to_dict()
    return oz_rates, lb_rates


DHL_OZ_RATES, DHL_LB_RATES = load_dhl_rates()

# FedEx Ground Economy Zone 6 rates (25–70 lbs)
FEDEX_RATES = {
    25: 41.91, 26: 43.43, 27: 45.19, 28: 47.39, 29: 48.75,
    30: 48.45, 31: 49.85, 32: 49.87, 33: 53.00, 34: 53.01,
    35: 53.92, 36: 56.19, 37: 56.43, 38: 57.71, 39: 60.33,
    40: 60.42, 41: 63.20, 42: 63.22, 43: 67.02, 44: 67.03,
    45: 67.05, 46: 68.81, 47: 69.80, 48: 70.79, 49: 71.86,
    50: 71.94, 51: 72.50, 52: 72.51, 53: 72.53, 54: 72.53,
    55: 72.54, 56: 72.57, 57: 72.60, 58: 72.61, 59: 73.11,
    60: 74.48, 61: 74.48, 62: 75.63, 63: 76.16, 64: 76.68,
    65: 77.37, 66: 77.41, 67: 78.02, 68: 79.17, 69: 80.34,
    70: 81.18,
}

# ─── Build numpy lookup arrays for vectorized rate lookups ────────────────────
_DHL_LB_MAX = max(DHL_LB_RATES.keys())
_dhl_lb_arr = np.zeros(_DHL_LB_MAX + 1)
for _w, _r in DHL_LB_RATES.items():
    _dhl_lb_arr[_w] = _r

_FEDEX_MIN = 25
_FEDEX_MAX = 70
_fedex_arr = np.zeros(_FEDEX_MAX + 1)
for _w, _r in FEDEX_RATES.items():
    _fedex_arr[_w] = _r


# ─── Vectorized calculation on dataframe ─────────────────────────────────────
def compute_quotes(df: pd.DataFrame, margin_pct: float,
                   base_fee: float, dhl_nqd_rate: float = 2.50) -> pd.DataFrame:
    """Apply all calculations vectorized with numpy/pandas."""
    out = df.copy()

    for col in ["Units", "Length", "Width", "Height", "Actual Weight"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["Units"] = out["Units"].astype(int)

    L = out["Length"].values.astype(float)
    W = out["Width"].values.astype(float)
    H = out["Height"].values.astype(float)
    AW = out["Actual Weight"].values.astype(float)

    # DIM weight = ceil(L*W*H / 166)
    volume = L * W * H
    dim_raw = volume / 166.0
    dim_weight = np.where(dim_raw > 0, np.ceil(dim_raw), 0).astype(int)
    out["DIM Weight"] = dim_weight

    # Billable weight = ceil(max(actual, dim))
    max_weight = np.maximum(AW, dim_weight.astype(float))
    billable = np.where(max_weight > 0, np.ceil(max_weight), 0).astype(int)
    out["Billable Weight"] = billable

    # Carrier routing
    is_fedex = billable >= 25
    out["Carrier"] = np.where(is_fedex, "FedEx", "DHL")

    # Base shipping cost — vectorized via numpy array indexing
    dhl_idx = np.clip(billable, 0, _DHL_LB_MAX)
    dhl_cost = _dhl_lb_arr[dhl_idx]
    fedex_idx = np.clip(billable, _FEDEX_MIN, _FEDEX_MAX)
    fedex_cost = _fedex_arr[fedex_idx]
    shipping_cost = np.where(is_fedex, fedex_cost, dhl_cost)
    out["Base Shipping Cost"] = shipping_cost

    # Surcharges — fully vectorized
    girth = (2 * W) + (2 * H)
    longest = np.maximum(np.maximum(L, W), H)

    # DHL NQD (using dynamic rate)
    dhl_nqd = (L + girth > 50) | (longest > 27) | (volume > 1728)
    dhl_surcharge = np.where(dhl_nqd, billable * dhl_nqd_rate, 0.0)

    # FedEx cascading — sort dims for second longest
    dims_stack = np.stack([L, W, H], axis=1)
    dims_sorted = np.sort(dims_stack, axis=1)[:, ::-1]
    second_longest = dims_sorted[:, 1]

    fedex_oversize = (L > 96) | (L + girth > 130) | (AW > 110)
    fedex_ahs_wt = (~fedex_oversize) & (AW > 50)
    fedex_ahs_dim = (~fedex_oversize) & (~fedex_ahs_wt) & (
        (L > 48) | (second_longest > 30) | (L + girth > 105)
    )
    fedex_surcharge = np.where(
        fedex_oversize, 255.0,
        np.where(fedex_ahs_wt, 56.25,
                 np.where(fedex_ahs_dim, 38.50, 0.0))
    )

    surcharges = np.where(is_fedex, fedex_surcharge, dhl_surcharge)
    out["Surcharges"] = surcharges

    # Unit cost
    unit_cost = base_fee + shipping_cost + surcharges
    out["Unit Cost"] = unit_cost

    # Recommended unit price (cap margin at 99.9% to prevent infinity)
    capped_margin = min(margin_pct, 99.9)
    out["Unit Price"] = unit_cost / (1 - capped_margin / 100)

    # Extended total
    out["Extended Total"] = out["Unit Price"] * out["Units"]

    # Flag for FedEx >70 lbs
    out["_over_70"] = is_fedex & (billable > 70)

    return out


# ─── PDF generation ──────────────────────────────────────────────────────────
class QuotePDF(FPDF):
    def header(self):
        # Title on the left
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 10, "Fulfillment Pricing Quote", ln=False, align="L")

        # Logo in the right corner (smaller size)
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.exists(logo_path):
            # Position logo in top right corner (x, y, width)
            self.image(logo_path, x=240, y=8, w=30)

        self.ln(15)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def sanitize_for_pdf(text: str) -> str:
    """Remove or replace characters that FPDF can't handle."""
    if not text:
        return ""
    # Convert to string and replace problematic characters
    text = str(text)
    # Replace smart quotes and other Unicode with ASCII equivalents
    replacements = {
        '\u201c': '"',  # Left double quote
        '\u201d': '"',  # Right double quote
        '\u2018': "'",  # Left single quote
        '\u2019': "'",  # Right single quote
        '\u2013': '-',  # En dash
        '\u2014': '--', # Em dash
        '\u2026': '...',# Ellipsis
        '\u00ae': '(R)',# Registered trademark
        '\u2122': '(TM)',# Trademark
        '\u00a9': '(C)',# Copyright
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Remove any remaining non-ASCII characters
    text = text.encode('ascii', 'ignore').decode('ascii')
    return text


def generate_pdf(rows: pd.DataFrame, first_name: str, last_name: str,
                 email: str, margin_pct: float, base_fee: float, discount_pct: float = 0.0,
                 client_account: str = "", product_type: str = "", quote_id: str = "",
                 dhl_nqd_rate: float = 2.50) -> bytes:
    pdf = QuotePDF(orientation="L", format="letter")
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    # Draw decorative border around the page
    pdf.set_draw_color(44, 62, 80)  # Dark blue-gray
    pdf.set_line_width(0.5)
    pdf.rect(5, 5, 269, 206)  # x, y, width, height for landscape letter

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, f"Prepared By: {first_name} {last_name}", ln=True)
    pdf.cell(0, 7, f"Email: {email}", ln=True)
    if client_account:
        pdf.cell(0, 7, f"Client/Account: {client_account}", ln=True)
    if product_type:
        pdf.cell(0, 7, f"Product Type: {product_type}", ln=True)
    pdf.cell(0, 7, f"Date: {datetime.now().strftime('%m/%d/%Y')}", ln=True)
    if quote_id:
        pdf.cell(0, 7, f"Quote ID: {quote_id}", ln=True)
    pdf.cell(0, 7, f"Target Margin: {margin_pct:.0f}%    |    Base Fulfillment Fee: ${base_fee:,.2f}", ln=True)
    pdf.ln(5)

    # Column widths optimized to fit landscape letter (279mm - 20mm margins = 259mm usable)
    cols = ["SKU", "Units", "Actual Wt", "DIM Wt", "Bill Wt", "Carrier", "Ship", "Surch", "Cost", "Price", "Total"]
    widths = [28, 13, 20, 18, 18, 20, 22, 22, 22, 24, 28]  # Total: 235mm (fits with margins)

    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255, 255, 255)
    for c, w in zip(cols, widths):
        pdf.cell(w, 8, c, border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    pdf.set_text_color(0, 0, 0)
    fill = False
    grand_total = 0.0

    for _, row in rows.iterrows():
        if fill:
            pdf.set_fill_color(235, 245, 251)
        else:
            pdf.set_fill_color(255, 255, 255)

        ext = row["Extended Total"]
        grand_total += ext

        # Sanitize and truncate long SKU names to fit
        sku_text = sanitize_for_pdf(str(row["SKU"]))[:15]

        vals = [
            sku_text,
            str(int(row["Units"])),
            f"{row['Actual Weight']:.1f}",
            f"{row['DIM Weight']:.0f}",
            f"{row['Billable Weight']:.0f}",
            str(row["Carrier"])[:4],  # Truncate carrier name
            f"${row['Base Shipping Cost']:.2f}",
            f"${row['Surcharges']:.2f}",
            f"${row['Unit Cost']:.2f}",
            f"${row['Unit Price']:.2f}",
            f"${ext:.2f}",
        ]
        for v, w in zip(vals, widths):
            pdf.cell(w, 6, v, border=1, align="C", fill=True)
        pdf.ln()
        fill = not fill

    # Add surcharge details section if applicable
    surcharge_details = []
    for idx, row in rows.iterrows():
        if row["Surcharges"] > 0:
            L, W, H = row["Length"], row["Width"], row["Height"]
            AW = row["Actual Weight"]
            carrier = row["Carrier"]

            if carrier == "DHL":
                girth = (2 * W) + (2 * H)
                longest = max(L, W, H)
                volume = L * W * H

                reasons = []
                if L + girth > 50:
                    reasons.append(f"Length + Girth ({L + girth:.1f}\") > 50\"")
                if longest > 27:
                    reasons.append(f"Longest side ({longest:.1f}\") > 27\"")
                if volume > 1728:
                    reasons.append(f"Volume ({volume:.0f} cu.in) > 1728 cu.in")

                surcharge_details.append({
                    "SKU": row["SKU"],
                    "Type": "DHL NQD (Non-Qualified Dimension)",
                    "Reason": " OR ".join(reasons),
                    "Amount": row["Surcharges"],
                    "Calculation": f"{row['Billable Weight']:.0f} lbs × ${dhl_nqd_rate:.2f}/lb"
                })

            elif carrier == "FedEx":
                girth = (2 * W) + (2 * H)
                dims = sorted([L, W, H], reverse=True)
                second_longest = dims[1]

                # Determine which surcharge applies
                if L > 96 or (L + girth > 130) or AW > 110:
                    reasons = []
                    if L > 96:
                        reasons.append(f"Length ({L:.1f}\") > 96\"")
                    if L + girth > 130:
                        reasons.append(f"Length + Girth ({L + girth:.1f}\") > 130\"")
                    if AW > 110:
                        reasons.append(f"Actual Weight ({AW:.1f} lbs) > 110 lbs")

                    surcharge_details.append({
                        "SKU": row["SKU"],
                        "Type": "FedEx Oversize",
                        "Reason": " OR ".join(reasons),
                        "Amount": row["Surcharges"],
                        "Calculation": "Flat $255.00"
                    })
                elif AW > 50:
                    surcharge_details.append({
                        "SKU": row["SKU"],
                        "Type": "FedEx AHS (Additional Handling - Weight)",
                        "Reason": f"Actual Weight ({AW:.1f} lbs) > 50 lbs",
                        "Amount": row["Surcharges"],
                        "Calculation": "Flat $56.25"
                    })
                else:
                    reasons = []
                    if L > 48:
                        reasons.append(f"Length ({L:.1f}\") > 48\"")
                    if second_longest > 30:
                        reasons.append(f"Second longest side ({second_longest:.1f}\") > 30\"")
                    if L + girth > 105:
                        reasons.append(f"Length + Girth ({L + girth:.1f}\") > 105\"")

                    surcharge_details.append({
                        "SKU": row["SKU"],
                        "Type": "FedEx AHS (Additional Handling - Dims)",
                        "Reason": " OR ".join(reasons),
                        "Amount": row["Surcharges"],
                        "Calculation": "Flat $38.50"
                    })

    # Display surcharge details if any exist
    if surcharge_details:
        pdf.ln(5)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 7, "Surcharge Details:", ln=True)
        pdf.set_font("Helvetica", "", 8)

        for detail in surcharge_details:
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(0, 5, f"- {sanitize_for_pdf(detail['SKU'])} - {detail['Type']} - ${detail['Amount']:,.2f}", ln=True)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(10, 5, "", ln=False)  # Indent
            pdf.cell(0, 5, f"Trigger: {detail['Reason']}", ln=True)
            pdf.cell(10, 5, "", ln=False)  # Indent
            pdf.cell(0, 5, f"Calculation: {detail['Calculation']}", ln=True)
            pdf.ln(2)

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)

    # Show subtotal, discount, and final total (positioned from right edge)
    subtotal = grand_total
    if discount_pct > 0:
        pdf.cell(150, 8, "", ln=False)  # Left padding
        pdf.cell(0, 8, f"Subtotal: ${subtotal:,.2f}", ln=True, align="R")
        discount_amount = subtotal * (discount_pct / 100)
        pdf.cell(150, 8, "", ln=False)  # Left padding
        pdf.cell(0, 8, f"Discount ({discount_pct:.0f}%): -${discount_amount:,.2f}", ln=True, align="R")
        pdf.set_font("Helvetica", "B", 13)
        final_total = subtotal - discount_amount
        pdf.cell(150, 10, "", ln=False)  # Left padding
        pdf.cell(0, 10, f"Grand Total Estimate: ${final_total:,.2f}", ln=True, align="R")
    else:
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(150, 10, "", ln=False)  # Left padding
        pdf.cell(0, 10, f"Grand Total Estimate: ${grand_total:,.2f}", ln=True, align="R")

    # Add important notice at the bottom
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(255, 243, 205)  # Light yellow background
    pdf.set_draw_color(44, 62, 80)  # Dark border
    pdf.set_line_width(0.3)

    # Notice box
    pdf.multi_cell(
        w=0,
        h=5,
        txt="IMPORTANT NOTICE: Accurate SKU dimensions and weight reporting by clients is critical to maintaining the "
            "integrity of our margins on deals. If SKUs are received by our 3PL partners and they greatly exceed the "
            "communicated SKU details, BV may hold the campaign prior to shipment to our members and seek additional "
            "fulfillment charges.",
        border=1,
        align="L",
        fill=True
    )

    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


# ─── Helper function for non-expandable logo ─────────────────────────────────
def get_base64_image(image_path: str) -> str:
    """Convert image to base64 string for inline HTML rendering."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ─── Helper function for data normalization ──────────────────────────────────
def normalize_quote_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize quote data types to ensure consistent comparison and storage.
    Prevents data loss from type mismatches between AgGrid and session state.
    Also enforces validation rules: no negative numbers, minimum Units = 1.
    """
    normalized = df.copy()

    # Ensure SKU is string
    normalized["SKU"] = normalized["SKU"].astype(str).replace("nan", "")

    # Ensure numeric columns are proper types with no NaN
    for col in ["Units", "Length", "Width", "Height", "Actual Weight"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce").fillna(0)

    # Prevent negative values - replace with 0
    for col in ["Length", "Width", "Height", "Actual Weight"]:
        normalized.loc[normalized[col] < 0, col] = 0.0

    # Ensure Units is integer
    normalized["Units"] = normalized["Units"].astype(int)
    # Ensure Units is at least 1 (prevent 0 or negative units)
    normalized.loc[normalized["Units"] < 1, "Units"] = 1

    return normalized


# ─── Streamlit UI ────────────────────────────────────────────────────────────
def main():
    # Display logo and title (logo is non-expandable)
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
    if os.path.exists(logo_path):
        col1, col2 = st.columns([1, 5])
        with col1:
            st.markdown(
                f'<img src="data:image/png;base64,{get_base64_image(logo_path)}" width="120" style="pointer-events: none;">',
                unsafe_allow_html=True
            )
        with col2:
            st.title("BV Fulfillment Quoting Tool")
    else:
        st.title("📦 BV Fulfillment Quoting Tool")

    with st.sidebar:
        st.header("Submitter Info")
        first_name = st.text_input("First Name")
        last_name = st.text_input("Last Name")
        email = st.text_input("Email Address")
        client_account = st.text_input("Client/Account Name (optional)")

        st.divider()
        product_type = st.selectbox(
            "Product Type *",
            options=[
                "",  # Empty default to force selection
                "Beauty & Personal Care",
                "Household & Cleaning",
                "Food & Beverage",
                "Health & Wellness",
                "Baby & Childcare",
                "Pet Products",
                "Electronics & Tech",
                "Home & Garden",
                "Kitchen Appliances",
                "Power Tools",
                "DIY",
                "Automotive",
                "Sporting Goods",
                "Apparel & Accessories",
                "Other"
            ],
            help="Select the primary product category for this quote (required)"
        )

        st.divider()
        st.header("Project Settings")
        margin_pct = st.slider("Target Margin %", 0, 100, 60)
        if margin_pct == 100:
            st.warning("100% margin results in infinite pricing. Capped at 99% for calculations.")
        base_fee = st.number_input("Base Fulfillment Fee ($)", min_value=0.0,
                                   value=2.50, step=0.25, format="%.2f")

        dhl_nqd_rate = st.number_input(
            "DHL NQD Surcharge Rate ($/lb)",
            min_value=0.0,
            value=2.50,
            step=0.25,
            format="%.2f",
            help="DHL Non-Qualified Dimension surcharge rate per pound of billable weight. Increases to $2.50/lb during busy season."
        )

        st.divider()
        st.header("Quote Discount")
        discount_pct = st.slider("Overall Quote Discount %", 0, 100, 0,
                                 help="Percentage discount applied to the grand total")

    # ── Initialize session state ──
    input_cols = ["SKU", "Units", "Length", "Width", "Height", "Actual Weight"]
    if "quote_data" not in st.session_state:
        st.session_state.quote_data = pd.DataFrame(
            {
                "SKU": [""] * 10,
                "Units": [1] * 10,
                "Length": [0.0] * 10,
                "Width": [0.0] * 10,
                "Height": [0.0] * 10,
                "Actual Weight": [0.0] * 10,
            }
        )

    # ── Import/Export Section ──
    st.subheader("SKU Data & Calculated Quote")

    col_import, col_export, col_clear = st.columns([2, 2, 1])

    with col_import:
        st.markdown("**📥 Import SKUs from CSV**")
        # Initialize uploader key in session state
        if 'uploader_key' not in st.session_state:
            st.session_state.uploader_key = 0

        uploaded_file = st.file_uploader(
            "Upload CSV file",
            type=['csv'],
            help="Upload a CSV file with columns: SKU, Units, Length, Width, Height, Actual Weight",
            label_visibility="collapsed",
            key=f"csv_uploader_{st.session_state.uploader_key}"
        )

        if uploaded_file is not None:
            try:
                # Security: Read CSV with UTF-8 encoding (handles BOM)
                import_df = pd.read_csv(uploaded_file, encoding='utf-8-sig')

                # Security: Check row count limit (prevent resource exhaustion)
                if len(import_df) > 10000:
                    st.error("❌ CSV exceeds 10,000 row limit. Please split into smaller files.")
                    return

                # UX: Check if CSV is empty
                if len(import_df) == 0:
                    st.warning("⚠️ CSV file is empty. No data to import.")
                    return

                # Normalize column names to handle both formats
                column_mapping = {
                    "Length (in)": "Length",
                    "Width (in)": "Width",
                    "Height (in)": "Height",
                    "Actual Weight (lbs)": "Actual Weight"
                }
                import_df.rename(columns=column_mapping, inplace=True)

                # Validate required columns
                required_cols = ["SKU", "Units", "Length", "Width", "Height", "Actual Weight"]
                missing_cols = [col for col in required_cols if col not in import_df.columns]

                if missing_cols:
                    st.error(f"❌ Missing required columns: {', '.join(missing_cols)}")
                    st.info("💡 Download the template below for the correct format.")
                    return

                # UX: Show info about extra columns being ignored
                extra_cols = set(import_df.columns) - set(required_cols)
                if extra_cols:
                    st.info(f"ℹ️ Extra columns will be ignored: {', '.join(extra_cols)}")

                # Ensure columns are in the right order
                import_df = import_df[required_cols].copy()

                # Security: Trim whitespace from SKUs
                import_df['SKU'] = import_df['SKU'].astype(str).str.strip()

                # Security: Detect and warn about formula characters in SKU
                formula_chars = import_df['SKU'].str.startswith(('=', '+', '-', '@', '|', '%'), na=False)
                if formula_chars.any():
                    st.warning("⚠️ Formula characters detected in SKU column. These may cause issues when exported to Excel.")

                # Store original data for comparison
                original_df = import_df.copy()

                # Normalize data types using helper function
                import_df = normalize_quote_data(import_df)

                # UX: Show validation summary of data modifications
                modifications = []
                for idx in range(len(import_df)):
                    for col in ["Units", "Length", "Width", "Height", "Actual Weight"]:
                        orig_val = original_df.iloc[idx][col]
                        new_val = import_df.iloc[idx][col]

                        # Check for changes
                        if pd.notna(orig_val) and orig_val != new_val:
                            if orig_val < 0:
                                modifications.append(f"Row {idx + 1}: {col} ({orig_val}) → 0 (negative values not allowed)")
                            elif col == "Units" and isinstance(orig_val, (int, float)) and orig_val != int(orig_val):
                                modifications.append(f"Row {idx + 1}: {col} ({orig_val}) → {new_val} (rounded)")
                        elif pd.isna(orig_val) or (isinstance(orig_val, str) and not orig_val.replace('.', '').replace('-', '').isdigit()):
                            if col == "Units" and new_val == 1:
                                modifications.append(f"Row {idx + 1}: {col} ('{orig_val}') → 1 (invalid value)")
                            elif new_val == 0:
                                modifications.append(f"Row {idx + 1}: {col} ('{orig_val}') → 0 (invalid value)")

                if modifications:
                    with st.expander(f"⚠️ {len(modifications)} data modification(s) detected - click to review"):
                        for mod in modifications[:20]:  # Show first 20
                            st.text(mod)
                        if len(modifications) > 20:
                            st.text(f"... and {len(modifications) - 20} more modifications")

                # UX: Warn if overwriting existing data
                if not st.session_state.quote_data.empty and st.session_state.quote_data['SKU'].ne('').any():
                    st.warning("⚠️ This will replace all existing data in the grid.")

                # Update session state
                st.session_state.quote_data = import_df

                # Set flags for post-import success message
                st.session_state.import_success = True
                st.session_state.import_count = len(import_df)

                # Increment grid key to force grid re-render with new data
                st.session_state.grid_key = st.session_state.get('grid_key', 0) + 1

                # Clear the file uploader by incrementing its key
                st.session_state.uploader_key += 1

                # Rerun to refresh page and show imported data in grid
                st.rerun()

            except pd.errors.ParserError as e:
                st.error(f"❌ CSV file is malformed: {str(e)}")
                st.info("💡 Check for unmatched quotes or incorrect delimiters. Download the template for reference.")
            except pd.errors.EmptyDataError:
                st.warning("⚠️ CSV file is empty. No data to import.")
            except UnicodeDecodeError:
                st.error("❌ Encoding error. Please save your CSV file as UTF-8 format.")
                st.info("💡 In Excel: File → Save As → CSV UTF-8 (Comma delimited)")
            except KeyError as e:
                st.error(f"❌ Column error: {str(e)}")
                st.info("💡 Download the template below for the correct format.")
            except Exception as e:
                st.error(f"❌ Error importing CSV: {str(e)}")
                st.info("💡 If this problem persists, download the template and ensure your file matches the format.")

    with col_export:
        st.markdown("**📤 Download Template**")
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SKU_Import_Template.csv")

        if os.path.exists(template_path):
            with open(template_path, 'r') as f:
                template_csv = f.read()

            st.download_button(
                label="Download CSV Template",
                data=template_csv,
                file_name="SKU_Import_Template.csv",
                mime="text/csv",
                help="Download a template CSV file to fill out your SKU data"
            )
        else:
            # Create template on-the-fly if file doesn't exist
            template_df = pd.DataFrame({
                "SKU": ["SAMPLE-001", "SAMPLE-002", "SAMPLE-003"],
                "Units": [100, 50, 25],
                "Length (in)": [12.0, 20.0, 8.0],
                "Width (in)": [10.0, 15.0, 6.0],
                "Height (in)": [8.0, 10.0, 4.0],
                "Actual Weight (lbs)": [5.0, 15.0, 2.5]
            })
            template_csv = template_df.to_csv(index=False)

            st.download_button(
                label="Download CSV Template",
                data=template_csv,
                file_name="SKU_Import_Template.csv",
                mime="text/csv",
                help="Download a template CSV file to fill out your SKU data"
            )

    with col_clear:
        st.markdown("**🗑️ Clear**")
        if st.button("Clear All", help="Clear all SKU data and reset to blank rows"):
            st.session_state.quote_data = pd.DataFrame({
                "SKU": [""] * 10,
                "Units": [1] * 10,
                "Length": [0.0] * 10,
                "Width": [0.0] * 10,
                "Height": [0.0] * 10,
                "Actual Weight": [0.0] * 10,
            })
            st.rerun()

    st.divider()

    # Show success message after CSV import (persists after rerun)
    if st.session_state.get("import_success", False):
        st.success(f"✓ Successfully imported {st.session_state.import_count} SKUs! Data is now loaded in the grid below.")
        # Clear the flag so message doesn't persist forever
        st.session_state.import_success = False

    # ── Edit user input data with AgGrid (Excel-like navigation) ──
    gb = GridOptionsBuilder.from_dataframe(st.session_state.quote_data)

    # Enable Excel-like features
    gb.configure_default_column(editable=True, groupable=False)
    gb.configure_column("SKU", editable=True, type=["textColumn"])
    gb.configure_column("Units", editable=True, type=["numericColumn", "numberColumnFilter"], min_value=1, value=1)
    gb.configure_column("Length", editable=True, type=["numericColumn", "numberColumnFilter"], min_value=0.0, header_name="Length (in)")
    gb.configure_column("Width", editable=True, type=["numericColumn", "numberColumnFilter"], min_value=0.0, header_name="Width (in)")
    gb.configure_column("Height", editable=True, type=["numericColumn", "numberColumnFilter"], min_value=0.0, header_name="Height (in)")
    gb.configure_column("Actual Weight", editable=True, type=["numericColumn", "numberColumnFilter"], min_value=0.0, header_name="Weight (lbs)")

    # Enable Excel-like keyboard navigation with cell focus
    gb.configure_grid_options(
        enableRangeSelection=True,
        enableCellTextSelection=True,
        ensureDomOrder=True,
        enterMovesDown=True,
        enterMovesDownAfterEdit=True,
        singleClickEdit=False,
        stopEditingWhenCellsLoseFocus=True,
        suppressRowClickSelection=True,
        suppressCellFocus=False,
    )

    grid_options = gb.build()

    # Custom CSS for grid lines and cell focus highlighting
    custom_css = {
        ".ag-cell": {
            "border": "1px solid #e0e0e0 !important",
        },
        ".ag-header-cell": {
            "border": "1px solid #d0d0d0 !important",
            "background-color": "#f5f5f5 !important",
        },
        ".ag-row": {
            "border-bottom": "1px solid #e0e0e0 !important",
        },
        ".ag-cell-focus": {
            "border": "2px solid #1e88e5 !important",
            "background-color": "#e3f2fd !important",
            "outline": "none !important",
        },
        ".ag-cell-focus:not(.ag-cell-range-selected)": {
            "border": "2px solid #1e88e5 !important",
            "background-color": "#e3f2fd !important",
        }
    }

    # Calculate dynamic height based on number of rows (min 400px, max 800px)
    num_rows = len(st.session_state.quote_data)
    row_height = 35  # Approximate height per row in pixels
    header_height = 50  # Header height
    calculated_height = min(max(400, (num_rows * row_height) + header_height), 800)

    # Use dynamic key to force grid refresh when data is imported
    grid_key = f"quote_data_grid_{st.session_state.get('grid_key', 0)}"

    grid_response = AgGrid(
        st.session_state.quote_data,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.MODEL_CHANGED,
        data_return_mode=DataReturnMode.AS_INPUT,
        fit_columns_on_grid_load=True,
        theme="streamlit",
        height=calculated_height,
        allow_unsafe_jscode=True,
        enable_enterprise_modules=False,
        custom_css=custom_css,
        reload_data=True,
        key=grid_key,
    )

    # Update session state with edited data (preserve raw input)
    if grid_response['data'] is not None:
        edited_df = normalize_quote_data(pd.DataFrame(grid_response['data']))
        st.session_state.quote_data = edited_df
    else:
        edited_df = st.session_state.quote_data

    # Compute calculations for display
    result_df = compute_quotes(edited_df.copy(), margin_pct, base_fee, dhl_nqd_rate)

    valid_mask = (
        result_df["SKU"].astype(str).str.strip().ne("")
        & (result_df["Billable Weight"] > 0)
    )
    valid_df = result_df[valid_mask].copy()

    # Display calculated results
    if not valid_df.empty:
        st.subheader("Calculated Results")

        display_df = valid_df[[
            "SKU", "Units", "Actual Weight", "DIM Weight", "Billable Weight", "Carrier",
            "Base Shipping Cost", "Surcharges", "Unit Cost", "Unit Price", "Extended Total"
        ]].copy()

        # Format currency columns
        for col in ["Base Shipping Cost", "Surcharges", "Unit Cost", "Unit Price", "Extended Total"]:
            display_df[col] = display_df[col].apply(lambda x: f"${x:,.2f}")

        st.dataframe(display_df, use_container_width=True, hide_index=True)

    # Warn about FedEx >70 lbs
    if not valid_df.empty and valid_df["_over_70"].any():
        over_skus = valid_df.loc[valid_df["_over_70"], "SKU"].tolist()
        st.warning(f"SKUs exceeding 70 lbs (FedEx max rate used): {', '.join(str(s) for s in over_skus)}")

    # Grand total metric with discount
    if not valid_df.empty:
        subtotal = valid_df["Extended Total"].sum()
        discount_amount = subtotal * (discount_pct / 100)
        grand_total = subtotal - discount_amount

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("Subtotal", f"${subtotal:,.2f}")
        with col_b:
            st.metric("Discount", f"-${discount_amount:,.2f}",
                     delta=f"-{discount_pct}%" if discount_pct > 0 else None,
                     delta_color="off")
        with col_c:
            st.metric("Grand Total", f"${grand_total:,.2f}")

        # ── Fee Breakdown Section ──
        st.divider()
        st.subheader("📊 Fee Breakdown")

        # Aggregate statistics
        total_base_fees = valid_df["Units"].sum() * base_fee
        total_shipping = valid_df["Base Shipping Cost"].sum()
        total_surcharges = valid_df["Surcharges"].sum()

        # Summary stats
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Base Fulfillment Fees", f"${total_base_fees:,.2f}",
                     help=f"{valid_df['Units'].sum()} units × ${base_fee:.2f}")
        with col2:
            st.metric("Total Shipping Costs", f"${total_shipping:,.2f}")
        with col3:
            st.metric("Total Surcharges", f"${total_surcharges:,.2f}")

        # Detailed surcharge breakdown
        if total_surcharges > 0:
            st.markdown("**Surcharge Details:**")

            # Recalculate to identify surcharge types
            surcharge_details = []
            for idx, row in valid_df.iterrows():
                if row["Surcharges"] > 0:
                    L, W, H = row["Length"], row["Width"], row["Height"]
                    AW = row["Actual Weight"]
                    carrier = row["Carrier"]

                    if carrier == "DHL":
                        girth = (2 * W) + (2 * H)
                        longest = max(L, W, H)
                        volume = L * W * H

                        reasons = []
                        if L + girth > 50:
                            reasons.append(f"Length + Girth ({L + girth:.1f}\") > 50\"")
                        if longest > 27:
                            reasons.append(f"Longest side ({longest:.1f}\") > 27\"")
                        if volume > 1728:
                            reasons.append(f"Volume ({volume:.0f} cu.in) > 1728 cu.in")

                        surcharge_details.append({
                            "SKU": row["SKU"],
                            "Type": "DHL NQD (Non-Qualified Dimension)",
                            "Reason": " OR ".join(reasons),
                            "Amount": row["Surcharges"],
                            "Calculation": f"{row['Billable Weight']} lbs × ${dhl_nqd_rate:.2f}/lb"
                        })

                    elif carrier == "FedEx":
                        girth = (2 * W) + (2 * H)
                        dims = sorted([L, W, H], reverse=True)
                        second_longest = dims[1]

                        # Determine which surcharge applies
                        if L > 96 or (L + girth > 130) or AW > 110:
                            reasons = []
                            if L > 96:
                                reasons.append(f"Length ({L:.1f}\") > 96\"")
                            if L + girth > 130:
                                reasons.append(f"Length + Girth ({L + girth:.1f}\") > 130\"")
                            if AW > 110:
                                reasons.append(f"Actual Weight ({AW:.1f} lbs) > 110 lbs")

                            surcharge_details.append({
                                "SKU": row["SKU"],
                                "Type": "FedEx Oversize",
                                "Reason": " OR ".join(reasons),
                                "Amount": row["Surcharges"],
                                "Calculation": "Flat $255.00"
                            })
                        elif AW > 50:
                            surcharge_details.append({
                                "SKU": row["SKU"],
                                "Type": "FedEx AHS (Additional Handling - Weight)",
                                "Reason": f"Actual Weight ({AW:.1f} lbs) > 50 lbs",
                                "Amount": row["Surcharges"],
                                "Calculation": "Flat $56.25"
                            })
                        else:
                            reasons = []
                            if L > 48:
                                reasons.append(f"Length ({L:.1f}\") > 48\"")
                            if second_longest > 30:
                                reasons.append(f"Second longest side ({second_longest:.1f}\") > 30\"")
                            if L + girth > 105:
                                reasons.append(f"Length + Girth ({L + girth:.1f}\") > 105\"")

                            surcharge_details.append({
                                "SKU": row["SKU"],
                                "Type": "FedEx AHS (Additional Handling - Dims)",
                                "Reason": " OR ".join(reasons),
                                "Amount": row["Surcharges"],
                                "Calculation": "Flat $38.50"
                            })

            # Display surcharge details as expandable sections
            for detail in surcharge_details:
                with st.expander(f"🔸 {detail['SKU']} — {detail['Type']} — ${detail['Amount']:,.2f}"):
                    st.write(f"**Trigger:** {detail['Reason']}")
                    st.write(f"**Calculation:** {detail['Calculation']}")
        else:
            st.success("✓ No surcharges applied to this quote")

        # Carrier breakdown
        st.markdown("**Carrier Breakdown:**")
        carrier_counts = valid_df.groupby("Carrier").agg({
            "Units": "sum",
            "Base Shipping Cost": "sum"
        }).reset_index()

        for _, crow in carrier_counts.iterrows():
            st.write(f"• **{crow['Carrier']}**: {crow['Units']} units, ${crow['Base Shipping Cost']:,.2f} shipping")

        # Important notice
        st.divider()
        st.warning(
            "⚠️ **IMPORTANT NOTICE:** Accurate SKU dimensions and weight reporting by clients is critical to maintaining the "
            "integrity of our margins on deals. If SKUs are received by our 3PL partners and they greatly exceed the "
            "communicated SKU details, BV may hold the campaign prior to shipment to our members and seek additional "
            "fulfillment charges."
        )

    else:
        st.info("Fill in SKU data above to see calculated quotes.")
        grand_total = 0.0
        subtotal = 0.0
        discount_amount = 0.0

    # ── Lock It In button ──
    st.divider()
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.button("🔒 Lock It In", type="primary", use_container_width=True):
            errors = []
            if not first_name.strip():
                errors.append("First Name is required.")
            if not last_name.strip():
                errors.append("Last Name is required.")
            if not email.strip():
                errors.append("Email Address is required.")
            if not product_type or product_type == "":
                errors.append("Product Type is required.")
            if valid_df.empty:
                errors.append("At least one SKU must be fully filled out.")

            if errors:
                for e in errors:
                    st.error(e)
            else:
                # Generate quote ID first
                quote_id = generate_quote_id()
                st.write(f"DEBUG: Quote ID generated: {quote_id}")

                pdf_filename = f"BV_Quote_{last_name}_{datetime.now().strftime('%Y%m%d')}.pdf"
                st.write(f"DEBUG: PDF filename: {pdf_filename}")

                try:
                    st.write("DEBUG: Starting PDF generation...")
                    pdf_bytes = generate_pdf(
                        valid_df, first_name, last_name, email, margin_pct, base_fee,
                        discount_pct, client_account, product_type, quote_id, dhl_nqd_rate
                    )
                    st.write(f"DEBUG: PDF generated successfully, size: {len(pdf_bytes)} bytes")
                except Exception as pdf_error:
                    st.error(f"PDF generation failed: {pdf_error}")
                    import traceback
                    st.error(f"Full traceback:\n{traceback.format_exc()}")
                    pdf_bytes = None
                    pdf_filename = None

                # Log quote to audit trail
                try:
                    log_quote_locked_in(
                        first_name=first_name,
                        last_name=last_name,
                        email=email,
                        client_account=client_account,
                        product_type=product_type,
                        margin_pct=margin_pct,
                        base_fee=base_fee,
                        dhl_nqd_rate=dhl_nqd_rate,
                        discount_pct=discount_pct,
                        valid_df=valid_df,
                        pdf_filename=pdf_filename,
                        quote_id=quote_id
                    )
                except Exception as e:
                    st.warning(f"Note: Audit logging failed ({e}). Contact administrator if this persists.")

                # Generate CSV export with SKU data and unit cost only
                try:
                    csv_filename = f"BV_Quote_{last_name}_{datetime.now().strftime('%Y%m%d')}.csv"
                    # Select only essential columns for CSV export
                    csv_columns = ["SKU", "Units", "Length", "Width", "Height", "Actual Weight",
                                   "DIM Weight", "Billable Weight", "Unit Cost", "Unit Price"]
                    csv_df = valid_df[csv_columns].copy()
                    csv_data = csv_df.to_csv(index=False)
                    csv_success = True
                except Exception as csv_error:
                    st.error(f"CSV generation failed: {csv_error}")
                    csv_data = ""
                    csv_filename = ""
                    csv_success = False

                # Store in session state to persist across reruns
                st.write("DEBUG: Storing data in session state...")
                st.session_state.quote_generated = True
                st.session_state.quote_id = quote_id
                st.session_state.pdf_bytes = pdf_bytes
                st.session_state.pdf_filename = pdf_filename
                st.session_state.csv_data = csv_data
                st.session_state.csv_filename = csv_filename
                st.session_state.csv_success = csv_success
                st.write(f"DEBUG: Session state updated. PDF bytes stored: {pdf_bytes is not None}")

    # Show download buttons if quote has been generated (outside button handler)
    if st.session_state.get('quote_generated', False):
        st.success(f"✓ Quote {st.session_state.quote_id} generated and logged successfully!")

        # Debug: Check what's in session state
        st.write(f"DEBUG: pdf_bytes is None: {st.session_state.get('pdf_bytes') is None}")
        st.write(f"DEBUG: csv_success: {st.session_state.get('csv_success', False)}")

        pdf_size = len(st.session_state.pdf_bytes) if st.session_state.get('pdf_bytes') else 0
        csv_size = len(st.session_state.csv_data) if st.session_state.get('csv_data') else 0
        st.info(f"📦 Files ready: {st.session_state.pdf_filename} ({pdf_size} bytes), {st.session_state.csv_filename} ({csv_size} bytes)")

        # Show download buttons - PDF always, CSV only if generated successfully
        if st.session_state.get('csv_success', False):
            col_pdf, col_csv = st.columns(2)

            with col_pdf:
                st.download_button(
                    label="📥 Download Quote PDF",
                    data=st.session_state.pdf_bytes,
                    file_name=st.session_state.pdf_filename,
                    mime="application/pdf",
                    use_container_width=True,
                )

            with col_csv:
                st.download_button(
                    label="📊 Download Quote CSV",
                    data=st.session_state.csv_data,
                    file_name=st.session_state.csv_filename,
                    mime="text/csv",
                    use_container_width=True,
                    help="SKU data with unit cost and pricing"
                )
        else:
            # Show only PDF button if CSV failed
            st.download_button(
                label="📥 Download Quote PDF",
                data=st.session_state.pdf_bytes,
                file_name=st.session_state.pdf_filename,
                mime="application/pdf",
                use_container_width=True,
            )


if __name__ == "__main__":
    main()
