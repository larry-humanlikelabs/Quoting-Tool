"""
Admin - BV Quoting Tool

Password-protected page for ops managers to view all locked-in quotes.
Supports multiple admin users via environment variable password list.
"""

import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.audit_logger import load_audit_log

st.set_page_config(
    page_title="Admin - BV Quoting Tool",
    page_icon="🔐",
    layout="wide",
)


def get_admin_passwords() -> list:
    """
    Get list of admin passwords from environment variable.

    Format: BV_ADMIN_PASSWORDS="password1,password2,password3"
    Fallback: Single password from BV_ADMIN_PASSWORD or default
    """
    # Check for comma-separated list first
    passwords_str = os.getenv("BV_ADMIN_PASSWORDS", "")
    if passwords_str:
        return [p.strip() for p in passwords_str.split(",") if p.strip()]

    # Fallback to single password or default
    single_password = os.getenv("BV_ADMIN_PASSWORD", "BV2026Admin!")
    return [single_password]


def check_authentication() -> bool:
    """Simple password authentication with session state."""

    # Already authenticated in this session
    if st.session_state.get("authenticated", False):
        return True

    st.title("🔐 Admin Access Required")
    st.warning("This page contains sensitive quote data. Please authenticate to continue.")

    # Get valid admin passwords
    valid_passwords = get_admin_passwords()

    # Show number of admin users (without revealing passwords)
    st.info(f"ℹ️ {len(valid_passwords)} authorized admin user(s) configured.")

    password = st.text_input("Enter Admin Password", type="password", key="admin_password_input")

    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        if st.button("🔓 Login", use_container_width=True):
            if password in valid_passwords:
                st.session_state.authenticated = True
                st.success("✓ Authentication successful! Redirecting...")
                st.rerun()
            else:
                st.error("✗ Incorrect password. Access denied.")

    return False


def parse_sku_details_safe(sku_details_json: str) -> list:
    """Safely parse SKU details JSON with fallback."""
    try:
        return json.loads(sku_details_json)
    except (json.JSONDecodeError, TypeError):
        return []


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Apply user-selected filters to audit log DataFrame."""

    if df.empty:
        return df

    filtered = df.copy()

    # Date range filter
    if filters.get('start_date'):
        filtered = filtered[filtered['timestamp'] >= pd.Timestamp(filters['start_date'])]
    if filters.get('end_date'):
        # Add 1 day to end_date to include the entire day
        end_datetime = pd.Timestamp(filters['end_date']) + timedelta(days=1)
        filtered = filtered[filtered['timestamp'] < end_datetime]

    # Email filter (case-insensitive partial match)
    if filters.get('email'):
        filtered = filtered[
            filtered['email'].str.contains(filters['email'], case=False, na=False)
        ]

    # Client/Account filter
    if filters.get('client'):
        filtered = filtered[
            filtered['client_account'].str.contains(filters['client'], case=False, na=False)
        ]

    # Product type filter (multiselect)
    if filters.get('product_types'):
        filtered = filtered[filtered['product_type'].isin(filters['product_types'])]

    # Grand total range filter
    if filters.get('min_total') is not None and filters.get('min_total') > 0:
        filtered = filtered[filtered['grand_total'] >= filters['min_total']]
    if filters.get('max_total') is not None and filters.get('max_total') < float('inf'):
        filtered = filtered[filtered['grand_total'] <= filters['max_total']]

    return filtered


def main():
    # Check authentication first
    if not check_authentication():
        return

    # Logout button in sidebar
    with st.sidebar:
        st.title("🔐 Admin Panel")
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()

        st.divider()
        st.header("Filters")

        # Date range filter
        st.subheader("Date Range")
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", value=None, key="filter_start_date")
        with col2:
            end_date = st.date_input("End Date", value=None, key="filter_end_date")

        # Email filter
        st.subheader("Search")
        email_filter = st.text_input("Submitter Email", placeholder="john@example.com", key="filter_email")
        client_filter = st.text_input("Client/Account", placeholder="Acme Corp", key="filter_client")

        # Product type filter
        st.subheader("Product Type")
        product_types_filter = st.multiselect(
            "Select Types",
            options=[
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
            key="filter_product_types"
        )

        # Grand total range filter
        st.subheader("Grand Total Range")
        min_total = st.number_input("Min Total ($)", min_value=0.0, value=0.0, step=100.0, key="filter_min_total")
        max_total = st.number_input("Max Total ($)", min_value=0.0, value=1000000.0, step=100.0, key="filter_max_total")

        st.divider()

        col_a, col_b = st.columns(2)
        with col_a:
            apply_filters_btn = st.button("✓ Apply Filters", use_container_width=True, type="primary")
        with col_b:
            clear_filters_btn = st.button("✗ Clear Filters", use_container_width=True)

    # Main content
    st.title("📊 Quote Audit Trail")
    st.markdown("View and audit all locked-in quotes. Filter, search, and export historical data.")

    # Load audit log
    try:
        df = load_audit_log()
    except Exception as e:
        st.error(f"Failed to load audit log: {e}")
        return

    if df.empty:
        st.info("📭 No quotes have been locked in yet. The audit log is empty.")
        st.markdown("""
        **How it works:**
        1. Users fill out quote details on the main page
        2. Click "Lock It In" to generate PDF
        3. Quote data is automatically logged here for audit trail
        """)
        return

    # Build filters dict
    filters = {
        'start_date': start_date,
        'end_date': end_date,
        'email': email_filter,
        'client': client_filter,
        'product_types': product_types_filter,
        'min_total': min_total,
        'max_total': max_total
    }

    # Clear filters button
    if clear_filters_btn:
        st.session_state.filter_start_date = None
        st.session_state.filter_end_date = None
        st.session_state.filter_email = ""
        st.session_state.filter_client = ""
        st.session_state.filter_product_types = []
        st.session_state.filter_min_total = 0.0
        st.session_state.filter_max_total = 1000000.0
        st.rerun()

    # Apply filters
    filtered_df = apply_filters(df, filters)

    # Summary metrics
    st.subheader("📈 Summary Metrics")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Quotes", f"{len(filtered_df):,}")

    with col2:
        total_revenue = filtered_df['grand_total'].sum()
        st.metric("Total Revenue", f"${total_revenue:,.2f}")

    with col3:
        avg_quote = filtered_df['grand_total'].mean() if len(filtered_df) > 0 else 0
        st.metric("Avg Quote Value", f"${avg_quote:,.2f}")

    with col4:
        if len(filtered_df) > 0:
            date_range_str = f"{filtered_df['timestamp'].min().strftime('%m/%d/%y')} - {filtered_df['timestamp'].max().strftime('%m/%d/%y')}"
        else:
            date_range_str = "N/A"
        st.metric("Date Range", date_range_str)

    st.divider()

    # Display audit log table
    st.subheader("🗂️ Audit Log Records")

    if filtered_df.empty:
        st.warning("No quotes match the current filters. Try adjusting your filter criteria.")
        return

    # Prepare display DataFrame
    display_df = filtered_df[[
        'quote_id', 'timestamp', 'first_name', 'last_name', 'email',
        'client_account', 'product_type', 'grand_total', 'num_skus', 'total_units'
    ]].copy()

    # Format columns
    display_df['timestamp'] = display_df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    display_df['grand_total'] = display_df['grand_total'].apply(lambda x: f"${x:,.2f}")
    display_df['submitter'] = display_df['first_name'] + " " + display_df['last_name']

    # Reorder columns
    display_df = display_df[[
        'quote_id', 'timestamp', 'submitter', 'email', 'client_account',
        'product_type', 'grand_total', 'num_skus', 'total_units'
    ]]

    display_df.columns = [
        'Quote ID', 'Timestamp', 'Submitter', 'Email', 'Client/Account',
        'Product Type', 'Grand Total', '# SKUs', 'Total Units'
    ]

    # Display table
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()

    # CSV export
    st.subheader("📥 Export Data")

    col_export1, col_export2 = st.columns(2)

    with col_export1:
        # Export filtered data
        csv_data = filtered_df.to_csv(index=False)
        st.download_button(
            label=f"📥 Download Filtered Audit Log ({len(filtered_df)} records)",
            data=csv_data,
            file_name=f"audit_log_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
            help="Download currently filtered audit log as CSV"
        )

    with col_export2:
        # Export full data
        full_csv_data = df.to_csv(index=False)
        st.download_button(
            label=f"📥 Download Full Audit Log ({len(df)} records)",
            data=full_csv_data,
            file_name=f"audit_log_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
            help="Download complete audit log (all quotes, unfiltered)"
        )

    st.divider()

    # Expandable quote details
    st.subheader("🔍 Quote Details")
    st.markdown("Click on a quote below to view full SKU-level details.")

    for idx, row in filtered_df.iterrows():
        quote_id = row['quote_id']
        timestamp_str = row['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
        submitter = f"{row['first_name']} {row['last_name']}"
        grand_total = row['grand_total']

        with st.expander(f"📋 {quote_id} - {submitter} - ${grand_total:,.2f} ({timestamp_str})"):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**Quote Metadata**")
                st.write(f"**Quote ID:** {row['quote_id']}")
                st.write(f"**Timestamp:** {timestamp_str}")
                st.write(f"**Submitter:** {submitter}")
                st.write(f"**Email:** {row['email']}")
                st.write(f"**Client/Account:** {row['client_account'] or 'N/A'}")
                st.write(f"**Product Type:** {row['product_type']}")
                st.write(f"**PDF Filename:** {row['pdf_filename']}")

            with col2:
                st.markdown("**Pricing Settings**")
                st.write(f"**Target Margin:** {row['margin_pct']}%")
                st.write(f"**Base Fee:** ${row['base_fee']:.2f}")
                st.write(f"**DHL NQD Rate:** ${row['dhl_nqd_rate']:.2f}/lb")
                st.write(f"**Discount:** {row['discount_pct']}%")
                st.write(f"**Subtotal:** ${row['subtotal']:,.2f}")
                st.write(f"**Discount Amount:** ${row['discount_amount']:,.2f}")
                st.write(f"**Grand Total:** ${row['grand_total']:,.2f}")

            st.markdown("---")
            st.markdown("**SKU-Level Details**")

            # Parse and display SKU details
            sku_details = parse_sku_details_safe(row['sku_details_json'])

            if sku_details:
                sku_df = pd.DataFrame(sku_details)

                # Format currency columns
                currency_cols = ['base_shipping_cost', 'surcharges', 'unit_cost', 'unit_price', 'extended_total']
                for col in currency_cols:
                    if col in sku_df.columns:
                        sku_df[col] = sku_df[col].apply(lambda x: f"${x:,.2f}")

                # Rename columns for display
                sku_df.columns = [col.replace('_', ' ').title() for col in sku_df.columns]

                st.dataframe(sku_df, use_container_width=True, hide_index=True)
            else:
                st.warning("SKU details unavailable or malformed for this quote.")


if __name__ == "__main__":
    main()
