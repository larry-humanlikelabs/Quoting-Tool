import streamlit as st

st.set_page_config(
    page_title="Tool Logic - BV Quoting Tool",
    page_icon="📚",
    layout="wide",
)

st.title("📚 Tool Logic & Calculation Methodology")

st.markdown("""
This page explains the complete calculation logic used in the BV Fulfillment Quoting Tool.
All calculations are based on industry-standard carrier pricing rules and dimensional weight formulas.
""")

# ─── Section 1: Base Calculations ───
st.header("1️⃣ Base Calculations")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Dimensional (DIM) Weight")
    st.markdown("""
    **Formula:** `Volume ÷ 166` (rounded up to nearest whole number)

    **Details:**
    - Volume = Length × Width × Height (cubic inches)
    - DIM divisor = 166 (industry standard for domestic shipments)
    - Result is always rounded **up** to the next whole integer

    **Why it matters:** Carriers charge based on space occupied in their trucks/planes,
    not just weight. Large, lightweight packages cost more to ship than their actual weight suggests.

    **Example:**
    - Package: 20" × 15" × 10"
    - Volume = 3,000 cubic inches
    - DIM Weight = 3,000 ÷ 166 = 18.07 → **19 lbs** (rounded up)
    """)

with col2:
    st.subheader("Billable Weight")
    st.markdown("""
    **Formula:** `MAX(Actual Weight, DIM Weight)` (rounded up)

    **Details:**
    - Compares the package's actual weight vs. dimensional weight
    - The **higher** value is used for pricing
    - Result is always rounded **up** to the next whole integer

    **Why it matters:** This ensures carriers are compensated fairly for both
    heavy packages and large, bulky packages.

    **Example:**
    - Actual Weight = 15 lbs
    - DIM Weight = 19 lbs
    - Billable Weight = **19 lbs** (the greater value)
    """)

st.divider()

# ─── Section 2: Carrier Routing ───
st.header("2️⃣ Carrier Routing Logic")

st.markdown("""
All shipments are routed using **Zone 6** pricing (standard domestic mid-range zone).

The system automatically selects the carrier based on billable weight:
""")

col1, col2 = st.columns(2)

with col1:
    st.info("""
    **DHL eCommerce** 🚚

    **When:** Billable Weight **< 25 lbs**

    **Rate Source:** DHL Zone 6 rate table (CSV)
    - Ounce rates: 1-16 oz
    - Pound rates: 1-24 lbs

    **Why DHL?** Cost-effective for lighter packages, extensive residential delivery network.
    """)

with col2:
    st.success("""
    **FedEx Ground Economy** 📦

    **When:** Billable Weight **≥ 25 lbs**

    **Rate Source:** FedEx Zone 6 rate table (hardcoded)
    - Weight range: 25-70 lbs

    **Why FedEx?** Better pricing for heavier packages, reliable ground service.
    """)

st.divider()

# ─── Section 3: Surcharge Engine ───
st.header("3️⃣ Surcharge Engine (The Penalty Box)")

st.warning("""
**Critical:** Surcharges can significantly impact profitability. The system automatically
detects and applies dimensional/handling penalties **before** margin is applied.
""")

st.subheader("DHL Surcharges")

st.markdown("""
### Non-Qualified Dimension (NQD) Fee

**Trigger Conditions (ANY of these):**
1. Length + Girth > 50 inches
2. Longest side > 27 inches
3. Volume > 1,728 cubic inches (1 cubic foot)

**Where:**
- Girth = (2 × Width) + (2 × Height)
- Longest side = MAX(Length, Width, Height)

**Penalty Calculation:**
```
Surcharge = Billable Weight × $2.00/lb
```

**Example:**
- Package: 30" × 20" × 15"
- Girth = (2×20) + (2×15) = 70"
- Length + Girth = 30 + 70 = 100" → **EXCEEDS 50"**
- Billable Weight = 55 lbs
- **NQD Surcharge = 55 × $2.00 = $110.00**
""")

st.divider()

st.subheader("FedEx Surcharges (Cascading Logic)")

st.markdown("""
**Important:** FedEx surcharges are evaluated in **cascading order**.
Only the **first match** applies — no double-billing.
""")

tab1, tab2, tab3 = st.tabs(["🔴 Oversize", "🟠 AHS Weight", "🟡 AHS Dims"])

with tab1:
    st.markdown("""
    ### Oversize Charge (Highest Priority)

    **Trigger Conditions (ANY of these):**
    1. Length > 96 inches
    2. Length + Girth > 130 inches
    3. Actual Weight > 110 lbs

    **Penalty:** Flat **$255.00**

    **Example:**
    - Package: 100" × 30" × 20"
    - Length = 100" → **EXCEEDS 96"**
    - **Oversize Surcharge = $255.00**
    - (No other surcharges evaluated — cascade stops here)
    """)

with tab2:
    st.markdown("""
    ### Additional Handling Surcharge - Weight (Medium Priority)

    **Trigger Conditions (ALL must be true):**
    1. **NOT** Oversize (checked first)
    2. Actual Weight > 50 lbs

    **Penalty:** Flat **$56.25**

    **Example:**
    - Package: 40" × 30" × 20", 65 lbs actual weight
    - Not Oversize ✓
    - Actual Weight (65 lbs) > 50 lbs ✓
    - **AHS Weight Surcharge = $56.25**
    - (Cascade stops — AHS Dims not evaluated)
    """)

with tab3:
    st.markdown("""
    ### Additional Handling Surcharge - Dimensions (Lowest Priority)

    **Trigger Conditions (ALL must be true):**
    1. **NOT** Oversize
    2. **NOT** AHS Weight
    3. **ANY** of these dimensional thresholds:
       - Length > 48 inches
       - Second Longest Side > 30 inches
       - Length + Girth > 105 inches

    **Penalty:** Flat **$38.50**

    **Where:**
    - Second Longest Side = middle value when [L, W, H] are sorted descending

    **Example:**
    - Package: 50" × 28" × 20", 40 lbs actual weight
    - Not Oversize ✓
    - Not AHS Weight (40 < 50) ✓
    - Length (50") > 48" → **EXCEEDS 48"**
    - **AHS Dims Surcharge = $38.50**
    """)

st.divider()

# ─── Section 4: Pricing Formulas ───
st.header("4️⃣ Pricing Formulas")

st.markdown("""
The system calculates pricing in layers, building from base costs to final client-facing prices:
""")

col1, col2 = st.columns(2)

with col1:
    st.markdown("""
    ### Unit Cost (Internal)

    **Formula:**
    ```
    Unit Cost = Base Fulfillment Fee + Base Shipping Cost + Surcharges
    ```

    **Components:**
    - **Base Fulfillment Fee:** Per-unit handling fee (default $2.50)
    - **Base Shipping Cost:** Carrier rate from DHL/FedEx tables
    - **Surcharges:** Any NQD/Oversize/AHS penalties

    **Example:**
    - Base Fulfillment = $2.50
    - DHL Shipping (6 lbs) = $11.02
    - No Surcharges = $0.00
    - **Unit Cost = $13.52**
    """)

with col2:
    st.markdown("""
    ### Recommended Unit Price (Client-Facing)

    **Formula:**
    ```
    Unit Price = Unit Cost ÷ (1 - Margin%)
    ```

    **Details:**
    - Margin % = Target profit margin (default 60%)
    - This is a **markup**, not a margin deduction
    - At 60% margin, you charge 2.5× the cost

    **Example:**
    - Unit Cost = $13.52
    - Margin = 60% (0.60)
    - Unit Price = $13.52 ÷ (1 - 0.60)
    - Unit Price = $13.52 ÷ 0.40
    - **Unit Price = $33.80**
    """)

st.markdown("""
### Extended Total (Per SKU)

**Formula:**
```
Extended Total = Recommended Unit Price × Units
```

**Example:**
- Unit Price = $33.80
- Units = 10
- **Extended Total = $338.00**
""")

st.divider()

# ─── Section 5: Quote Totals ───
st.header("5️⃣ Quote-Level Totals")

st.markdown("""
### Subtotal
Sum of all Extended Totals across all SKUs in the quote.

### Discount (Optional)
```
Discount Amount = Subtotal × (Discount % ÷ 100)
```
Applied to the overall quote, not individual line items.

### Grand Total
```
Grand Total = Subtotal - Discount Amount
```

This is the final amount presented to the client.
""")

st.divider()

# ─── Section 6: Edge Cases ───
st.header("6️⃣ Edge Cases & Guardrails")

st.markdown("""
The tool handles several edge cases to ensure accurate quotes:

1. **Margin = 100%**
   - Results in infinite pricing (division by zero)
   - System warns user and caps calculations at 99% for safety

2. **FedEx Weights > 70 lbs**
   - Rate table only covers 25-70 lbs
   - System uses 70 lb rate as max and displays warning
   - User should verify special handling rates for 70+ lb packages

3. **Sub-Pound Weights (DHL)**
   - Packages under 1 lb use ounce-based rates
   - Automatically converts to ounce lookup (1-16 oz)

4. **Missing Logo**
   - PDF generation continues without logo if logo.png is missing
   - Header displays title only

5. **Empty Rows**
   - System filters out blank SKU rows before calculations
   - Only validates rows with SKU name and non-zero dimensions
""")

st.divider()

# ─── Section 7: Performance Optimizations ───
st.header("7️⃣ Performance Optimizations")

st.markdown("""
The tool uses vectorized calculations for maximum performance:

- **Numpy Arrays:** All weight/dimension calculations use numpy for batch processing
- **Pandas Operations:** No row-by-row loops — vectorized operations across entire dataset
- **Cached Rate Loading:** DHL rates loaded once and cached for session
- **Pre-computed Lookups:** Numpy arrays for O(1) rate lookups

**Result:** Can process 100+ SKUs in milliseconds.
""")

st.divider()

# ─── Footer ───
st.info("""
**Questions or Issues?**

This tool implements industry-standard carrier pricing logic. All formulas have been
validated against DHL eCommerce and FedEx Ground Economy published rate cards.

For rate discrepancies or questions about specific surcharges, consult the carrier's
official rate documentation or contact your account representative.
""")
