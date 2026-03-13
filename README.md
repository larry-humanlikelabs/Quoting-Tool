# BV Fulfillment Quoting Tool

Professional web application for generating accurate fulfillment and shipping quotes.

## Features

- **Automated Carrier Routing**: DHL eCommerce for packages <25 lbs, FedEx Ground Economy for ≥25 lbs
- **Dimensional Weight Calculations**: Automatic DIM weight and billable weight calculations
- **Surcharge Protection**: Automatic detection and application of DHL NQD and FedEx surcharges
- **Bulk Import**: CSV upload for processing multiple SKUs at once
- **Professional PDF Output**: Branded quotes with detailed line-item breakdowns
- **Flexible Pricing**: Adjustable margins, base fees, and overall quote discounts

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

### Run Locally

```bash
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`

## Deployment

### Deploy to bolt.new

1. Fork or clone this repository
2. Go to [bolt.new](https://bolt.new)
3. Click "Import from GitHub"
4. Select this repository
5. bolt.new will auto-detect Streamlit and deploy

## Usage

1. **Enter Client Info**: Fill in name, email, client/account, and product type
2. **Set Parameters**: Adjust margin %, base fulfillment fee, and discount %
3. **Add SKU Data**:
   - Enter manually in the data grid, OR
   - Download the CSV template, fill it out, and upload
4. **Review Calculations**: Check the calculated results table
5. **Generate PDF**: Click "Lock It In" to download a professional quote

## Files

- `app.py` - Main Streamlit application
- `requirements.txt` - Python dependencies
- `dhl_rates.csv` - DHL Zone 6 rate data
- `SKU_Import_Template.csv` - CSV template for bulk imports
- `logo.png` - Company logo for PDF quotes
- `pages/1_📚_Tool_Logic.py` - Documentation page explaining calculations

## Requirements

- Python 3.10+
- Streamlit
- Pandas
- FPDF2
- NumPy

## License

© 2026 BazaarVoice. Internal use only.
