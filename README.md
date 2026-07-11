# Logistics CRM - Operations Initial Stage

Ye CRM logistics company ke Operations module ke initial stage ke liye ready scaffold hai. Login ke baad hi CRM open hota hai. **Saara data PostgreSQL database me save hota hai** (default DB name: `logistics_crm`).

## Included Modules

Operations flow ke 7 steps:

1. Advance Voucher
2. Advance Fuel Entry
3. Trip Creation
4. Trip Reporting
5. Trip Unloading
6. Trip Performance
7. Trip Expense

## Major Features

- Secure login with hashed passwords and session based access
- Login time division selection
- CRM ke andar division change option without logout/refresh
- Vehicle number enter karte hi division check popup
- Vehicle details auto-fill: FTL Type, Driver Name, Driver No., Last Closing KM, Opening KM
- Add/Update Vehicle popup form
- Bulk vehicle upload by CSV/XLSX
- Cash/Bank/Fuel payment structure handling
- Back, Print, Save & Next buttons on all operation pages
- Professional PDF generation
- PDF preview page with Download, Print, Save, Cancel
- PDF PostgreSQL database me BYTEA format me save hota hai
- Dark / Light theme toggle (default dark, choice browser me save rehti hai)
- Auto TP number format: `DIVISION/COMPANY/FY/MONTH/SR.NO` (e.g. `AJ/TCI/26-27/06/0001`)
- Global search bar for vehicle, TP, driver, PDF and saved trip data
- Responsive UI with sidebar open/close
- Audit logs for important actions
- Scalable schema for next modules

## Setup Requirements

- Python 3.10+
- pip
- **PostgreSQL 12+** (server running locally or remote)

## Database Setup (PostgreSQL)

1. PostgreSQL install karein aur server start karein.
2. `.env.example` ko `.env` me copy karke apne DB credentials bharein:

   ```env
   PGHOST=localhost
   PGPORT=5432
   PGDATABASE=logistics_crm
   PGUSER=postgres
   PGPASSWORD=your_password
   ```

   Ya ek hi line me: `DATABASE_URL=postgresql://postgres:your_password@localhost:5432/logistics_crm`

3. Database (`logistics_crm`) agar exist nahi karta to app **khud bana deta hai** (jab `DATABASE_URL` use na ho). Tables aur seed data bhi automatically ban jaate hain pehli baar `app.py` run karne par.

## Run Locally

```bash
cd logistics_crm
python -m venv .venv
```

### Windows

```bash
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### macOS / Linux

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open browser:

```text
http://127.0.0.1:5000
```

## Demo Login

```text
User: admin
Password: admin123
```

or

```text
User: arun
Password: admin123
```

## Division List

- AJAY TRANSPORT
- SANJAY CARRIER
- SUNITA DEVI
- ASHISH SHARMA
- KAVITA SHARMA

VINITA SHARMA intentionally removed.

## Bulk Vehicle Upload Format

CSV/XLSX columns:

```text
vehicle_no, ftl_type, driver_name, driver_mobile, last_closing_km, opening_km, division_id, vehicle_type
```

Example:

```csv
vehicle_no,ftl_type,driver_name,driver_mobile,last_closing_km,opening_km,division_id,vehicle_type
HR55AB1234,32 FEET MXL,RAMESH KUMAR,9876543210,45000,45001,2,SELF
```

## Production Notes

Before real deployment:

1. Change `CRM_SECRET_KEY` in `.env` or server environment.
2. Change demo passwords.
3. Run behind HTTPS.
4. Backup PostgreSQL database regularly (`pg_dump logistics_crm > backup.sql`).
5. Production me ek dedicated DB user (kam privileges) aur strong password use karein.

## Production Run Example

```bash
pip install -r requirements.txt
export CRM_SECRET_KEY="put-a-long-random-secret-here"
gunicorn -w 4 -b 0.0.0.0:5000 wsgi:app
```

Windows PowerShell environment example:

```powershell
$env:CRM_SECRET_KEY="put-a-long-random-secret-here"
python app.py
```
