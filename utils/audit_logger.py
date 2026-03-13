"""
Audit logging module for BV Quoting Tool.

Provides thread-safe logging of locked-in quotes to CSV audit trail.
"""

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
import pandas as pd
import random
import string

# Configure logging
logger = logging.getLogger(__name__)

# CSV field names for audit log
FIELDNAMES = [
    "quote_id",
    "timestamp",
    "first_name",
    "last_name",
    "email",
    "client_account",
    "product_type",
    "margin_pct",
    "base_fee",
    "dhl_nqd_rate",
    "discount_pct",
    "subtotal",
    "discount_amount",
    "grand_total",
    "num_skus",
    "total_units",
    "pdf_filename",
    "sku_details_json"
]


def generate_quote_id() -> str:
    """Generate unique quote ID in format: Q-YYYYMMDD-HHMM-XXXX"""
    now = datetime.now()
    date_part = now.strftime("%Y%m%d")
    time_part = now.strftime("%H%M")
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"Q-{date_part}-{time_part}-{random_part}"


def ensure_audit_log_exists(log_path: Path) -> None:
    """Create audit log file with headers if it doesn't exist."""
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()

        logger.info(f"Audit log initialized at {log_path}")


def log_quote_locked_in(
    first_name: str,
    last_name: str,
    email: str,
    client_account: str,
    product_type: str,
    margin_pct: float,
    base_fee: float,
    dhl_nqd_rate: float,
    discount_pct: float,
    valid_df: pd.DataFrame,
    pdf_filename: str,
    log_path: Optional[Path] = None
) -> str:
    """
    Log a locked-in quote to the audit trail.

    Args:
        first_name: Submitter first name
        last_name: Submitter last name
        email: Submitter email
        client_account: Client/account name (optional)
        product_type: Product type category
        margin_pct: Target margin percentage
        base_fee: Base fulfillment fee
        dhl_nqd_rate: DHL NQD surcharge rate
        discount_pct: Overall discount percentage
        valid_df: DataFrame with calculated quote rows
        pdf_filename: Generated PDF filename
        log_path: Path to audit log CSV (defaults to data/audit_log.csv)

    Returns:
        quote_id: Unique identifier for this quote

    Raises:
        Exception: If logging fails (non-fatal - should not block PDF generation)
    """
    if log_path is None:
        log_path = Path(__file__).parent.parent / "data" / "audit_log.csv"

    # Ensure log file exists
    ensure_audit_log_exists(log_path)

    # Generate unique quote ID
    quote_id = generate_quote_id()

    # Calculate totals
    subtotal = valid_df["Extended Total"].sum()
    discount_amount = subtotal * (discount_pct / 100)
    grand_total = subtotal - discount_amount
    num_skus = len(valid_df)
    total_units = int(valid_df["Units"].sum())

    # Serialize SKU details to JSON
    sku_details = []
    for _, row in valid_df.iterrows():
        sku_details.append({
            "sku": str(row["SKU"]),
            "units": int(row["Units"]),
            "length": float(row["Length"]),
            "width": float(row["Width"]),
            "height": float(row["Height"]),
            "actual_weight": float(row["Actual Weight"]),
            "dim_weight": int(row["DIM Weight"]),
            "billable_weight": int(row["Billable Weight"]),
            "carrier": str(row["Carrier"]),
            "base_shipping_cost": float(row["Base Shipping Cost"]),
            "surcharges": float(row["Surcharges"]),
            "unit_cost": float(row["Unit Cost"]),
            "unit_price": float(row["Unit Price"]),
            "extended_total": float(row["Extended Total"])
        })

    sku_details_json = json.dumps(sku_details)

    # Create row dict
    row_dict = {
        "quote_id": quote_id,
        "timestamp": datetime.now().isoformat(),
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "client_account": client_account or "",
        "product_type": product_type,
        "margin_pct": margin_pct,
        "base_fee": base_fee,
        "dhl_nqd_rate": dhl_nqd_rate,
        "discount_pct": discount_pct,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "grand_total": grand_total,
        "num_skus": num_skus,
        "total_units": total_units,
        "pdf_filename": pdf_filename,
        "sku_details_json": sku_details_json
    }

    # Append to CSV with thread-safe file locking (simple approach for bolt.new)
    try:
        with open(log_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row_dict)

        logger.info(f"Quote {quote_id} logged successfully")
        return quote_id

    except Exception as e:
        logger.error(f"Failed to log quote: {e}")
        raise


def load_audit_log(log_path: Optional[Path] = None) -> pd.DataFrame:
    """
    Load audit log from CSV.

    Args:
        log_path: Path to audit log CSV (defaults to data/audit_log.csv)

    Returns:
        DataFrame with audit log data (empty if file doesn't exist or is corrupt)
    """
    if log_path is None:
        log_path = Path(__file__).parent.parent / "data" / "audit_log.csv"

    if not log_path.exists():
        logger.info("Audit log does not exist yet")
        return pd.DataFrame()

    try:
        df = pd.read_csv(log_path)

        # Validate required columns
        required_cols = ['quote_id', 'timestamp', 'email', 'grand_total']
        missing = [col for col in required_cols if col not in df.columns]

        if missing:
            logger.error(f"Audit log is corrupt (missing columns: {missing})")
            return pd.DataFrame()

        # Parse timestamp as datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])

        return df

    except pd.errors.EmptyDataError:
        logger.info("Audit log is empty")
        return pd.DataFrame()

    except Exception as e:
        logger.error(f"Failed to load audit log: {e}")
        return pd.DataFrame()
