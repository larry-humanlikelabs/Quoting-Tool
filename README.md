# BV Fulfillment Quoting Tool

A professional Streamlit application for generating fulfillment pricing quotes with DHL and FedEx rate calculations.

## Features

- Interactive SKU data entry with Excel-like grid navigation
- CSV import/export for bulk SKU management
- Automated carrier routing (DHL for <25 lbs, FedEx for 25+ lbs)
- Comprehensive surcharge calculations (NQD, Oversize, AHS)
- PDF quote generation with branding
- Admin audit trail for locked-in quotes
- Multi-user admin access with password protection

## Running the App

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run app.py
```

### Production Deployment

```bash
# Using the start script
./start.sh

# Or using npm scripts
npm run dev
```

The app will be available at `http://localhost:5173`

## Project Structure

```
.
├── app.py                      # Main Streamlit application
├── dhl_rates.csv              # DHL Zone 6 rate table
├── logo.png                   # Company logo for PDFs
├── SKU_Import_Template.csv    # Template for bulk imports
├── pages/
│   ├── 1_📚_Tool_Logic.py    # Documentation page
│   └── 2_🔐_Admin.py         # Admin audit trail
├── utils/
│   ├── __init__.py
│   └── audit_logger.py        # Audit logging utilities
└── data/
    └── .gitkeep               # Audit log storage

```

## Environment Variables

The app uses Supabase for data persistence (optional). Set these in `.env`:

```
VITE_SUPABASE_URL=your_supabase_url
VITE_SUPABASE_SUPABASE_ANON_KEY=your_anon_key
```

### Admin Access

Configure admin passwords using environment variables:

**Multiple Admins:**
```
BV_ADMIN_PASSWORDS="password1,password2,password3"
```

**Single Admin:**
```
BV_ADMIN_PASSWORD="YourSecurePassword123!"
```

Default password (if not set): `BV2026Admin!`

## Admin Panel

Access the admin panel at `/Admin` to:
- View all locked-in quotes
- Filter by date, email, client, product type, and total amount
- Export audit data to CSV
- View detailed SKU-level breakdowns

## Rate Configuration

- **DHL Rates:** Loaded from `dhl_rates.csv` (Zone 6 pricing)
- **FedEx Rates:** Hardcoded in `app.py` (Zone 6, 25-70 lbs)
- **Base Fulfillment Fee:** Configurable via sidebar (default $2.50)
- **DHL NQD Rate:** Configurable via sidebar (default $2.50/lb)

## CSV Import Format

Use the template `SKU_Import_Template.csv` with these columns:

```
SKU, Units, Length (in), Width (in), Height (in), Actual Weight (lbs)
```

## Security Notes

- Admin panel is password-protected
- Quote data is logged to `data/audit_log.csv`
- No sensitive data is exposed in PDFs beyond pricing
- All calculations are performed server-side

## Support

For questions or issues, contact your BV Fulfillment operations manager.
