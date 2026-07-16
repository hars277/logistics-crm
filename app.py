from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import psycopg2
import psycopg2.extras

# Rows come back as dict-like objects (RealDictRow); alias for type hints.
Row = Dict[str, Any]

from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    import openpyxl
except Exception:  # pragma: no cover - optional at runtime
    openpyxl = None

try:  # Load DB / secret config from a local .env file if present.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - optional at runtime
    pass

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
UPLOAD_DIR = BASE_DIR / "uploads"

# ----------------------------- PostgreSQL config -----------------------------
# Prefer a single DATABASE_URL (e.g. postgresql://user:pass@host:5432/dbname).
# Otherwise fall back to individual PG* variables with sensible local defaults.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PG_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": os.environ.get("PGPORT", "5432"),
    "dbname": os.environ.get("PGDATABASE", "logistics_crm"),
    "user": os.environ.get("PGUSER", "postgres"),
    "password": os.environ.get("PGPASSWORD", "postgres"),
}


def pg_connect(dbname: str | None = None) -> Any:
    """Open an autocommit PostgreSQL connection returning dict-like rows."""
    if DATABASE_URL and dbname is None:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cfg = dict(PG_CONFIG)
        if dbname is not None:
            cfg["dbname"] = dbname
        conn = psycopg2.connect(cursor_factory=psycopg2.extras.RealDictCursor, **cfg)
    conn.autocommit = True
    return conn

DIVISION_NAMES = [
    "AJAY TRANSPORT",
    "SANJAY CARRIER",
    "SUNITA DEVI",
    "ASHISH SHARMA",
    "KAVITA SHARMA",
]

# Optional explicit short codes for divisions used in the TP number.
# Anything not listed falls back to the first two letters of the division name.
DIVISION_CODES = {
    "AJAY TRANSPORT": "AJ",
    "SANJAY CARRIER": "SC",
    "SUNITA DEVI": "SD",
    "ASHISH SHARMA": "AS",
    "KAVITA SHARMA": "KS",
}

# Default "vehicle associated company" segment of the TP number (editable per voucher).
DEFAULT_VEHICLE_COMPANY = "TCI"

# Seed master data (editable later via Add Pump / Add Account popups).
PAYMENT_ACCOUNT_SEED = [
    ("CASH DHARUHERA", "CASH"),
    ("HDFC BANK : 123456789012 : HDFC00074", "BANK"),
    ("BPCL CMS WALLET", "FUEL"),
    ("HP SHRIPAL PUMP", "FUEL"),
    ("SAI BABA SERVICES", "FUEL"),
]
PUMP_SEED = [
    "AJAY TRANSPORT CNG",
    "BPCL CMS WALLET",
    "HP SHRIPAL PUMP",
    "SAI BABA SERVICES",
    "MAJOR BHIM SINGH AND SON",
]

# FTL options shared by the voucher form and the Add Vehicle popup.
FTL_TYPES = ["32 FEET SXL", "32 FEET MXL", "20 FEET", "22 FEET", "24 FEET", "40 FEET", "CONTAINER", "OPEN BODY"]

# Fields that should auto-fill across steps once known for a vehicle's trip.
# These are merged from earlier saved steps and the vehicle master record.
CROSS_STEP_AUTOFILL_KEYS = [
    "vehicle_no", "ftl_type", "vehicle_type", "driver_name", "driver_mobile",
    "fuel_type", "tank_capacity", "mileage", "last_closing_km", "opening_km",
    "route", "route_distance", "party_name", "tp_no", "assoc_company",
    "voucher_date", "voucher_time", "adv_voucher_date", "adv_voucher_time",
    "tp_date", "tp_time", "tp_start_date", "tp_start_time", "vendor_name",
]

STEP_ORDER = [
    "advance-voucher",
    "advance-fuel",
    "trip-creation",
    "trip-reporting",
    "trip-unloading",
    "trip-performance",
    "trip-expense",
]

STEP_TITLES = {
    "advance-voucher": "Advance Voucher",
    "advance-fuel": "Advance Fuel Entry",
    "trip-creation": "Trip Creation",
    "trip-reporting": "Trip Reporting",
    "trip-unloading": "Trip Unloading",
    "trip-performance": "Trip Performance",
    "trip-expense": "Trip Expense",
}

STEP_DB_COL = {
    "advance-voucher": "voucher_json",
    "advance-fuel": "fuel_json",
    "trip-creation": "trip_creation_json",
    "trip-reporting": "reporting_json",
    "trip-unloading": "unloading_json",
    "trip-performance": "performance_json",
    "trip-expense": "expense_json",
}

# UI metadata. Marking codes from paper notes (A1/M1 etc.) are intentionally NOT displayed in UI.
FIELD_CONFIG: Dict[str, List[Dict[str, Any]]] = {
    "advance-voucher": [
        {"title": "Voucher Details", "fields": [
            {"name": "trip_mode", "label": "Trip Mode", "type": "radio", "options": ["Empty Trip", "One Way"], "default": "One Way"},
            {"name": "tp_no", "label": "TP No", "type": "text", "readonly": True},
            {"name": "assoc_company", "label": "Vehicle Assoc. Company", "type": "text", "default": DEFAULT_VEHICLE_COMPANY},
            {"name": "voucher_date", "label": "Date", "type": "date"},
            {"name": "voucher_time", "label": "Time", "type": "time"},
            {"name": "vendor_name", "label": "Vendor Name", "type": "select", "options": ["AJAY TRANSPORT COMPANY"]},
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "ftl_type", "label": "FTL Type", "type": "select", "options": ["32 FEET SXL", "32 FEET MXL", "20 FEET", "22 FEET", "24 FEET", "40 FEET", "CONTAINER", "OPEN BODY"]},
            {"name": "serial_no", "label": "Serial No.", "type": "number", "default": "0"},
            {"name": "last_closing_km", "label": "Last Closing KM", "type": "number"},
            {"name": "opening_km", "label": "Opening KM", "type": "number"},
        ]},
        {"title": "Driver Details", "fields": [
            {"name": "driver_name", "label": "First Driver Code/Name", "type": "text"},
            {"name": "driver_mobile", "label": "First Driver Mobile No", "type": "tel"},
        ]},
        {"title": "Route - Fuel Details", "fields": [
            {"name": "previous_route", "label": "Previous Route", "type": "text", "readonly": True, "default": "N/A"},
            {"name": "previous_trip", "label": "Previous Trip", "type": "text", "readonly": True, "default": "N/A"},
            {"name": "previous_tp_date", "label": "Previous TP Date", "type": "text", "readonly": True, "default": "N/A"},
            {"name": "previous_tp_adv", "label": "Previous TP Adv.", "type": "number", "readonly": True, "default": "0"},
            {"name": "route", "label": "Route", "type": "text"},
            {"name": "route_distance", "label": "Route Distance (KM)", "type": "number", "default": "0"},
            {"name": "fuel_type", "label": "Fuel Type", "type": "select", "options": ["DIESEL", "CNG", "PETROL", "EV"]},
            {"name": "advance_type", "label": "Advance Type", "type": "select", "options": ["Per Km", "Fixed"]},
            {"name": "select_advance", "label": "Select Advance", "type": "select", "options": ["", "Branch Advance", "Other Advance", "Fuel Advance", "Driver Advance"]},
            {"name": "rate_per_km", "label": "Rate Per Km", "type": "number", "default": "0"},
            {"name": "req_fuel_qty", "label": "Req. Fuel Qty (Ltr)", "type": "number", "default": "0"},
        ]},
        {"title": "Advance Amount", "fields": [
            {"name": "amount_paid_by", "label": "Amount Paid By", "type": "payment", "options": ["CASH", "BANK", "FUEL"], "default": "CASH"},
            {"name": "payment_account", "label": "Payment Account", "type": "select", "options": ["CASH DHARUHERA", "HDFC BANK : 123456789012 : HDFC00074", "BPCL CMS WALLET", "HP SHRIPAL PUMP", "SAI BABA SERVICES"]},
            {"name": "paid_to_account", "label": "Paid To (A/C No)", "type": "text"},
            {"name": "available_balance", "label": "Available Balance", "type": "number", "readonly": True, "default": "0"},
            {"name": "payable_advance", "label": "Payable Advance", "type": "number", "default": "0"},
            {"name": "paid_advance", "label": "Paid Advance", "type": "number", "default": "0"},
            {"name": "balance_advance", "label": "Balance Advance", "type": "number", "readonly": True, "default": "0"},
            {"name": "total_advance", "label": "Total Advance", "type": "number", "readonly": True, "default": "0"},
            {"name": "remarks", "label": "Remarks", "type": "textarea"},
        ]},
    ],
    "advance-fuel": [
        {"title": "Trip Fuel Entry", "fields": [
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "tp_no", "label": "TP No", "type": "text"},
            {"name": "driver_name", "label": "Driver", "type": "text", "readonly": True, "default": "N/A"},
            {"name": "tp_date", "label": "TP Date", "type": "date"},
            {"name": "fuel_type", "label": "Fuel Type", "type": "text", "readonly": True, "default": "N/A"},
            {"name": "advance_type", "label": "Advance Type", "type": "select", "options": ["Fixed", "Per Km"]},
            {"name": "tank_capacity", "label": "Tank Capacity", "type": "number", "default": "0"},
            {"name": "available_fuel", "label": "Available Fuel", "type": "number", "readonly": True, "default": "0"},
            {"name": "fuel_filling_qty", "label": "Trip Filling Qty", "type": "number", "default": "0"},
            {"name": "pump_name", "label": "Pump Name", "type": "select", "options": ["Select Fuel Provider", "AJAY TRANSPORT CNG", "BPCL CMS WALLET", "HP SHRIPAL PUMP", "SAI BABA SERVICES", "MAJOR BHIM SINGH AND SON"]},
        ]},
        {"title": "Add Fuel", "fields": [
            {"name": "payment_account", "label": "Payment Account", "type": "text", "readonly": True, "default": "N/A"},
            {"name": "req_fuel_qty", "label": "Req. Fuel Qty", "type": "number", "default": "0"},
            {"name": "discount_per_ltr", "label": "Discount Amt. Per Ltr", "type": "number", "default": "0"},
            {"name": "advance_amt", "label": "Advance Amt (Rs)", "type": "number", "default": "0"},
            {"name": "fuel_rate", "label": "Fuel Rate", "type": "number", "default": "0"},
            {"name": "driver_adjustment", "label": "Driver Adjustment", "type": "number", "default": "0"},
            {"name": "tcs", "label": "TCS", "type": "number", "default": "0"},
            {"name": "fuel_slip_no", "label": "Fuel Slip No.", "type": "text"},
            {"name": "required_fuel_qty", "label": "Required Fuel Qty", "type": "number", "default": "0"},
            {"name": "fuel_amount", "label": "Fuel Amount", "type": "number", "readonly": True, "default": "0"},
            {"name": "balanced_fuel_qty", "label": "Balanced Fuel Qty", "type": "number", "readonly": True, "default": "0"},
            {"name": "required_advance", "label": "Required Advance (Rs)", "type": "number", "readonly": True, "default": "0"},
            {"name": "paid_advance", "label": "Paid Advance (Rs)", "type": "number", "readonly": True, "default": "0"},
            {"name": "balance_advance", "label": "Balance Advance (Rs)", "type": "number", "readonly": True, "default": "0"},
            {"name": "refuel_date", "label": "Re-Fuel Date", "type": "date"},
            {"name": "req_fuel_amount", "label": "Req. Fuel Amount", "type": "number", "readonly": True, "default": "0"},
            {"name": "round_trip", "label": "Round Trip", "type": "checkbox"},
            {"name": "round_fuel_amt", "label": "Round Fuel Amt", "type": "number", "readonly": True, "default": "0"},
            {"name": "remarks", "label": "Remarks", "type": "textarea"},
        ]},
        {"title": "Fuel Line Items", "table": True, "columns": ["Refuel", "Qty", "Rate", "Date / Time", "Final Price", "Remark"]},
    ],
    "trip-creation": [
        {"title": "Trip Creation", "fields": [
            {"name": "select_type", "label": "Select Type", "type": "radio", "options": ["SELF", "MARKET"], "default": "SELF"},
            {"name": "tp_no", "label": "TP No", "type": "text"},
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "tp_opening_km", "label": "TP Opening KM", "type": "number", "default": "0"},
            {"name": "vehicle_type", "label": "Vehicle Type", "type": "text"},
            {"name": "loading_date", "label": "Loading Date", "type": "date"},
            {"name": "reporting_date", "label": "Reporting Date", "type": "date"},
            {"name": "loading_time", "label": "Loading Time", "type": "time"},
            {"name": "reporting_time", "label": "Reporting Time", "type": "time"},
            {"name": "route", "label": "Route", "type": "text"},
            {"name": "entry_type", "label": "Entry Type", "type": "radio", "options": ["Credit", "Paid", "To Pay"], "default": "Credit"},
            {"name": "route_distance", "label": "Route Distance (KM)", "type": "number", "default": "0"},
            {"name": "load_type", "label": "Load Type", "type": "select", "options": ["", "FTL", "LTL", "Part Load"]},
            {"name": "trip_create_opening_km", "label": "Trip Create Opening KM", "type": "number", "default": "0"},
            {"name": "trip_id", "label": "Trip Id", "type": "text", "readonly": True},
            {"name": "closing_km", "label": "Closing KM", "type": "number", "default": "0"},
            {"name": "on_account", "label": "On Account", "type": "text"},
            {"name": "rate_type", "label": "Rate Type", "type": "select", "options": ["", "Per KM", "Fixed", "Per Ton", "Per Packet"]},
            {"name": "document_type", "label": "Document Type", "type": "radio", "options": ["GRN No", "Challan No"], "default": "GRN No"},
            {"name": "grn_challan_no", "label": "GRN/Challan No", "type": "text"},
            {"name": "grn_challan_date", "label": "GRN/Challan Date", "type": "date"},
        ]},
        {"title": "Consignor", "fields": [
            {"name": "temporary_consignor", "label": "Temporary Consignor", "type": "checkbox"},
            {"name": "consignor", "label": "Consignor", "type": "text"},
            {"name": "consignor_address", "label": "Address", "type": "textarea"},
            {"name": "consignor_state", "label": "State", "type": "text"},
            {"name": "consignor_pin", "label": "PIN", "type": "text"},
            {"name": "consignor_city", "label": "City", "type": "text"},
            {"name": "consignor_phone", "label": "Phone", "type": "tel"},
            {"name": "consignor_email", "label": "Email", "type": "email"},
            {"name": "consignor_gst", "label": "GST IN No.", "type": "text"},
        ]},
        {"title": "Consignee", "fields": [
            {"name": "temporary_consignee", "label": "Temporary Consignee", "type": "checkbox"},
            {"name": "consignee", "label": "Consignee", "type": "text"},
            {"name": "consignee_address", "label": "Address", "type": "textarea"},
            {"name": "consignee_state", "label": "State", "type": "text"},
            {"name": "consignee_pin", "label": "PIN", "type": "text"},
            {"name": "consignee_city", "label": "City", "type": "text"},
            {"name": "consignee_phone", "label": "Phone", "type": "tel"},
            {"name": "consignee_email", "label": "Email", "type": "email"},
            {"name": "consignee_gst", "label": "GST IN No.", "type": "text"},
        ]},
        {"title": "Freight & Tax", "fields": [
            {"name": "rate", "label": "Rate", "type": "number", "default": "0"},
            {"name": "packet", "label": "Pkt", "type": "number", "default": "0"},
            {"name": "weight", "label": "Weight", "type": "number", "default": "0"},
            {"name": "basic_freight", "label": "Basic Freight", "type": "number", "default": "0"},
            {"name": "loading_unloading", "label": "Loading/Unloading", "type": "number", "default": "0"},
            {"name": "green_tax", "label": "Green Tax", "type": "number", "default": "0"},
            {"name": "halting_charge", "label": "Halting Charge", "type": "number", "default": "0"},
            {"name": "touching_charge", "label": "Touching Charge", "type": "number", "default": "0"},
            {"name": "holding_charge", "label": "Holding Charge", "type": "number", "default": "0"},
            {"name": "detention_damage_charge", "label": "Detention/Damage Charge", "type": "number", "default": "0"},
            {"name": "labour_charge", "label": "Labour Charge", "type": "number", "default": "0"},
            {"name": "deduction_name", "label": "Deduction Name", "type": "text"},
            {"name": "deduction_charge", "label": "Deduction Charge", "type": "number", "default": "0"},
            {"name": "total_deduction", "label": "Total Deduction", "type": "number", "readonly": True, "default": "0"},
            {"name": "tax_type", "label": "Tax Type", "type": "radio", "options": ["GST", "IGST", "EXEMPTED"], "default": "EXEMPTED"},
            {"name": "taxable_amt", "label": "Taxable Amt", "type": "number", "readonly": True, "default": "0"},
            {"name": "cgst_percent", "label": "CGST %", "type": "number", "default": "0"},
            {"name": "cgst_tax", "label": "CGST Tax", "type": "number", "readonly": True, "default": "0"},
            {"name": "sgst_percent", "label": "SGST %", "type": "number", "default": "0"},
            {"name": "sgst_tax", "label": "SGST Tax", "type": "number", "readonly": True, "default": "0"},
            {"name": "igst_percent", "label": "IGST %", "type": "number", "default": "0"},
            {"name": "igst_tax", "label": "IGST Tax", "type": "number", "readonly": True, "default": "0"},
            {"name": "total_freight", "label": "Total Freight", "type": "number", "readonly": True, "default": "0"},
            {"name": "description_goods", "label": "Description of Goods", "type": "textarea"},
        ]},
    ],
    "trip-reporting": [
        {"title": "Reporting Trip Creation", "fields": [
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "tp_no", "label": "TP No", "type": "text"},
            {"name": "adv_voucher_date", "label": "Adv. Voucher Date", "type": "date"},
            {"name": "adv_voucher_time", "label": "Adv. Voucher Time", "type": "time"},
            {"name": "tp_date", "label": "TP Date", "type": "date"},
            {"name": "tp_time", "label": "TP Time", "type": "time"},
            {"name": "reporting_date", "label": "Reporting Date", "type": "date"},
            {"name": "reporting_time", "label": "Reporting Time", "type": "time"},
            {"name": "party_name", "label": "Party Name", "type": "text"},
            {"name": "route", "label": "Route", "type": "text"},
            {"name": "route_code", "label": "Route Code", "type": "text"},
        ]},
    ],
    "trip-unloading": [
        {"title": "Trip Unloading", "fields": [
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "tp_no", "label": "TP No", "type": "text"},
            {"name": "adv_voucher_date", "label": "Adv. Voucher Date", "type": "date"},
            {"name": "adv_voucher_time", "label": "Adv. Voucher Time", "type": "time"},
            {"name": "tp_date", "label": "TP Date", "type": "date"},
            {"name": "tp_time", "label": "TP Time", "type": "time"},
            {"name": "unloading_date", "label": "Unloading Date", "type": "date"},
            {"name": "unloading_time", "label": "Unloading Time", "type": "time"},
            {"name": "party_name", "label": "Party Name", "type": "text"},
            {"name": "route", "label": "Route", "type": "text"},
            {"name": "route_code", "label": "Route Code", "type": "text"},
        ]},
    ],
    "trip-performance": [
        {"title": "Trip Performance Entry", "fields": [
            {"name": "vehicle_type", "label": "Vehicle Type", "type": "radio", "options": ["SELF", "ATTACHED", "MARKET"], "default": "SELF"},
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "tp_no", "label": "TP No", "type": "text"},
            {"name": "tp_start_date", "label": "TP Start Date", "type": "date"},
            {"name": "tp_start_time", "label": "TP Start Time", "type": "time"},
            {"name": "tp_closed_date", "label": "TP Closed Date", "type": "date"},
            {"name": "tp_closed_time", "label": "TP Closed Time", "type": "time"},
            {"name": "no_of_days", "label": "No. of Days", "type": "number", "readonly": True},
            {"name": "perf_date", "label": "Performance Date", "type": "date"},
            {"name": "perf_time", "label": "Performance Time", "type": "time"},
            {"name": "trip_type", "label": "Trip Type", "type": "text", "readonly": True},
            {"name": "opening_km", "label": "Opening KM", "type": "number", "default": "0"},
            {"name": "closing_km", "label": "Closing KM", "type": "number", "default": "0"},
        ]},
    ],
    "trip-expense": [
        {"title": "Trip Header", "fields": [
            {"name": "division_name", "label": "Division", "type": "text", "readonly": True},
            {"name": "vendor_type", "label": "Vendor Type", "type": "radio", "options": ["Self", "Market"], "default": "Self"},
            {"name": "vehicle_no", "label": "Vehicle No", "type": "vehicle"},
            {"name": "tp_no", "label": "TP No", "type": "text"},
            {"name": "tp_start_date", "label": "TP Start Date", "type": "date"},
            {"name": "tp_start_time", "label": "TP Start Time", "type": "time"},
            {"name": "vendor_name", "label": "Vendor Name", "type": "text"},
            {"name": "tp_close_date", "label": "TP Close Date", "type": "date"},
            {"name": "tp_exp_create_date", "label": "TP Exp Create Date", "type": "date"},
            {"name": "no_of_days", "label": "No. of Days", "type": "number", "readonly": True},
            {"name": "total_tp_amt", "label": "Total TP Amt", "type": "number", "readonly": True},
            {"name": "route", "label": "Route", "type": "text", "readonly": True},
            {"name": "challan_status", "label": "Challan Status", "type": "text", "readonly": True},
            {"name": "driver_name", "label": "Driver Name", "type": "text", "readonly": True},
            {"name": "run_km", "label": "Run KM", "type": "number", "readonly": True, "default": "0"},
            {"name": "vehicle_type", "label": "Vehicle Type", "type": "text", "readonly": True, "default": "N/A"},
        ]},
        {"title": "Trip Advance Details", "fields": [
            {"name": "branch_advance", "label": "Branch Advance", "type": "number", "default": "0"},
            {"name": "advance", "label": "Advance", "type": "number", "default": "0"},
            {"name": "balance", "label": "Balance", "type": "number", "readonly": True, "default": "0"},
            {"name": "other_advance", "label": "Other Advance", "type": "number", "default": "0"},
            {"name": "diesel_cash", "label": "Diesel Cash", "type": "number", "default": "0"},
            {"name": "deduct_adv_amt", "label": "Deduct Adv. Amt (-)", "type": "number", "default": "0"},
            {"name": "total_fuel", "label": "Total Fuel", "type": "number", "default": "0"},
            {"name": "toll_tax", "label": "Toll Tax", "type": "number", "default": "0"},
            {"name": "loading_unloading_amt", "label": "Loading/Unloading Amt", "type": "number", "default": "0"},
            {"name": "total_freight_advance", "label": "Total Freight Advance", "type": "number", "readonly": True, "default": "0"},
        ]},
        {"title": "Trip Expense Details", "fields": [
            {"name": "extra_km", "label": "Extra KM", "type": "number", "default": "0"},
            {"name": "fuel_qty", "label": "Fuel Qty", "type": "number", "default": "0"},
            {"name": "fuel_rate", "label": "Fuel Rate", "type": "number", "default": "0"},
            {"name": "extra_diesel", "label": "Extra Diesel", "type": "number", "default": "0"},
            {"name": "toll_tax_exp", "label": "Toll Tax", "type": "number", "default": "0"},
            {"name": "naka", "label": "Naka", "type": "number", "default": "0"},
            {"name": "bhati", "label": "Bhati", "type": "number", "default": "0"},
            {"name": "rto", "label": "RTO", "type": "number", "default": "0"},
            {"name": "entry", "label": "Entry", "type": "number", "default": "0"},
            {"name": "challan", "label": "Challan", "type": "number", "default": "0"},
            {"name": "labour_charges", "label": "Labour Charges", "type": "number", "default": "0"},
            {"name": "accidental_settlement", "label": "Accidental Settlement", "type": "number", "default": "0"},
            {"name": "cash_toll", "label": "Cash Toll", "type": "number", "default": "0"},
            {"name": "incentive", "label": "Incentive", "type": "number", "default": "0"},
            {"name": "police_expense", "label": "Police Expense", "type": "number", "default": "0"},
            {"name": "other", "label": "Other", "type": "number", "default": "0"},
            {"name": "repair_maintenance", "label": "Repair & Maintenance", "type": "number", "default": "0"},
            {"name": "urea_charge", "label": "Urea Charge", "type": "number", "default": "0"},
            {"name": "pod_charge", "label": "POD Charge", "type": "number", "default": "0"},
            {"name": "parking_charge", "label": "Parking Charge", "type": "number", "default": "0"},
            {"name": "fooding", "label": "Fooding", "type": "number", "default": "0"},
            {"name": "fooding_from", "label": "Fooding From", "type": "date"},
            {"name": "fooding_to", "label": "Fooding To", "type": "date"},
            {"name": "receive_by", "label": "Receive By", "type": "select", "options": ["Driver", "Branch", "Account"]},
            {"name": "atm_charge", "label": "ATM Charge", "type": "number", "default": "0"},
            {"name": "diesel_cash_exp", "label": "Diesel Cash", "type": "number", "default": "0"},
            {"name": "pesgi", "label": "PESGI", "type": "number", "default": "0"},
            {"name": "phone_charge", "label": "Phone Charge", "type": "number", "default": "0"},
            {"name": "air_grease", "label": "Air & Grease", "type": "number", "default": "0"},
            {"name": "monthly_mechanical_exp", "label": "Monthly Mechanical Exp.", "type": "number", "default": "0"},
            {"name": "advance_salary", "label": "Advance Salary", "type": "number", "default": "0"},
            {"name": "weight_exp_slip", "label": "Weight Exp. Slip", "type": "number", "default": "0"},
        ]},
        {"title": "Driver Detail", "fields": [
            {"name": "select_driver", "label": "Select Driver", "type": "text"},
            {"name": "available_balance", "label": "Available Balance", "type": "number", "readonly": True, "default": "0"},
        ]},
        {"title": "Extra", "fields": [
            {"name": "route_survey_charge", "label": "Route Survey Charge", "type": "number", "default": "0"},
            {"name": "driver_incentive", "label": "Driver Incentive", "type": "number", "default": "0"},
            {"name": "agent_commission", "label": "Agent Commission", "type": "number", "default": "0"},
            {"name": "behanthi", "label": "Behanthi", "type": "number", "default": "0"},
            {"name": "penalty_deduction", "label": "Penalty Deduction", "type": "number", "default": "0"},
        ]},
        {"title": "Loading / Unloading Charge", "fields": [
            {"name": "loading_charge", "label": "Loading Charge", "type": "number", "default": "0"},
            {"name": "unloading_charge", "label": "Unloading Charge", "type": "number", "default": "0"},
            {"name": "halting_charge", "label": "Halting Charge", "type": "number", "default": "0"},
            {"name": "start_date", "label": "Start Date", "type": "date"},
            {"name": "reporting_date", "label": "Reporting Date", "type": "date"},
            {"name": "unloading_delivery_date", "label": "Unloading/Delivery Date", "type": "date"},
            {"name": "empty_start_date", "label": "Empty Start Date", "type": "date"},
        ]},
        {"title": "Total", "fields": [
            {"name": "driver_expense", "label": "Driver Expense", "type": "number", "readonly": True, "default": "0"},
            {"name": "payable_to_driver", "label": "Payable To Driver", "type": "number", "readonly": True, "default": "0"},
            {"name": "receivable_from_driver", "label": "Receivable From Driver", "type": "number", "readonly": True, "default": "0"},
            {"name": "total_freight_advance_final", "label": "Total Freight Advance", "type": "number", "readonly": True, "default": "0"},
            {"name": "total_tp_expense", "label": "Total TP Expense", "type": "number", "readonly": True, "default": "0"},
            {"name": "remarks", "label": "Remarks", "type": "textarea"},
        ]},
    ],
}


def create_app() -> Flask:
    app = Flask(__name__)
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    app.config.update(
        SECRET_KEY=os.environ.get("CRM_SECRET_KEY", "dev-change-this-secret-key"),
        MAX_CONTENT_LENGTH=60 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    )

    @app.before_request
    def before_request() -> None:
        g.db = get_db()
        g.user = current_user()
        if g.user and not session.get("csrf_token"):
            session["csrf_token"] = secrets.token_urlsafe(32)

    @app.after_request
    def add_security_headers(resp: Response) -> Response:
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["X-XSS-Protection"] = "1; mode=block"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Self-hosted assets only; inline styles/scripts used by templates are allowed.
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
        )
        resp.headers["Cache-Control"] = "no-store" if g.get("user") else "no-cache"
        return resp

    @app.teardown_appcontext
    def close_db(exc: Exception | None = None) -> None:
        db = g.pop("db_conn", None)
        if db is not None:
            db.close()

    @app.context_processor
    def inject_globals() -> Dict[str, Any]:
        divisions = []
        current_division = None
        if g.get("user"):
            divisions = query_all("SELECT * FROM divisions WHERE active=1 ORDER BY name")
            current_division = get_current_division()
        return {
            "user": g.get("user"),
            "divisions": divisions,
            "current_division": current_division,
            "csrf_token": session.get("csrf_token", ""),
            "step_order": STEP_ORDER,
            "step_titles": STEP_TITLES,
            "show_reminders": session.pop("just_logged_in", False),
        }

    @app.route("/")
    def home():
        if not g.user:
            return redirect(url_for("login"))
        return redirect(url_for("operations", step="advance-voucher"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            division_id = request.form.get("division_id")
            user = query_one("SELECT * FROM users WHERE username=? AND active=1", (username,))
            if user and check_password_hash(user["password_hash"], password):
                allowed = query_one(
                    "SELECT 1 FROM user_divisions WHERE user_id=? AND division_id=?",
                    (user["id"], division_id),
                )
                if not allowed:
                    flash("Selected division access is not assigned to this user.", "error")
                else:
                    session.clear()
                    session["user_id"] = user["id"]
                    session["current_division_id"] = int(division_id)
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    session["just_logged_in"] = True
                    record_audit("LOGIN", "users", user["id"], {"division_id": division_id})
                    return redirect(url_for("operations", step="advance-voucher"))
            else:
                flash("Invalid user id or password.", "error")
        divisions = query_all("SELECT * FROM divisions WHERE active=1 ORDER BY name")
        return render_template("login.html", divisions=divisions)

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            # Log the request for the admin; never reveal whether the user exists (no enumeration).
            try:
                u = query_one("SELECT id FROM users WHERE username=?", (username,))
                record_audit("PASSWORD_RESET_REQUEST", "users", u["id"] if u else None, {"username": username})
            except Exception:
                pass
            flash("Reset request bhej diya gaya. Admin aapka password reset karke naya password bata denge.", "success")
            return redirect(url_for("login"))
        return render_template("forgot_password.html")

    @app.route("/logout")
    @login_required
    def logout():
        record_audit("LOGOUT", "users", session.get("user_id"), {})
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        counts = {
            "vehicles": query_one("SELECT COUNT(*) AS c FROM vehicles")["c"],
            "trips": query_one("SELECT COUNT(*) AS c FROM trip_processes")["c"],
            "pdfs": query_one("SELECT COUNT(*) AS c FROM pdf_documents")["c"],
            "open_trips": query_one("SELECT COUNT(*) AS c FROM trip_processes WHERE status!='Closed'")["c"],
        }
        recent = query_all(
            """
            SELECT tp.tp_no, tp.status, tp.current_step, tp.updated_at, v.vehicle_no, d.name AS division_name
            FROM trip_processes tp
            LEFT JOIN vehicles v ON v.id = tp.vehicle_id
            LEFT JOIN divisions d ON d.id = tp.division_id
            ORDER BY tp.updated_at DESC LIMIT 10
            """
        )
        return render_template("dashboard.html", counts=counts, recent=recent)

    @app.route("/peshgi")
    @login_required
    def peshgi():
        return render_template("peshgi.html", today=datetime.now().strftime("%Y-%m-%d"))

    @app.route("/api/peshgi/save", methods=["POST"])
    @login_required
    @csrf_required
    def api_peshgi_save():
        # Accept multipart (with photos) or JSON.
        if request.files or request.form:
            p = sanitize_step_data({k: v for k, v in request.form.items()})
        else:
            p = sanitize_step_data(request.get_json(force=True) or {})
        vehicle_no = normalize_vehicle_no(p.get("vehicle_no"))
        if not vehicle_no:
            return jsonify({"ok": False, "message": "Vehicle No. is required."}), 400
        mobile = re.sub(r"[^0-9]", "", normalize_text(p.get("driver_mobile")))
        if mobile and len(mobile) != 10:
            return jsonify({"ok": False, "message": "Driver mobile must be exactly 10 digits."}), 400

        current_div_id = session["current_division_id"]
        vehicle = find_vehicle(vehicle_no)
        if vehicle and vehicle["current_division_id"] != current_div_id:
            division = query_one("SELECT * FROM divisions WHERE id=?", (vehicle["current_division_id"],))
            return jsonify({
                "ok": False, "code": "division_mismatch",
                "message": f"{vehicle_no} {division['name'] if division else 'doosri'} division ka hai. Upar se us division me switch karke continue karein.",
                "division": dict(division) if division else None,
            }), 409

        now = datetime.utcnow().isoformat()
        if not vehicle:
            new_id = execute_returning_id(
                """INSERT INTO vehicles(vehicle_no, current_division_id, ftl_type, driver_name, driver_mobile, last_closing_km, opening_km, vehicle_type, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (vehicle_no, current_div_id, "", normalize_text(p.get("driver_name")), mobile, 0, 0, "SELF", now, now),
            )
            vehicle = query_one("SELECT * FROM vehicles WHERE id=?", (new_id,))

        div = get_current_division()
        route = " - ".join([x for x in [normalize_text(p.get("from_place")), normalize_text(p.get("to_place"))] if x])
        total = normalize_text(p.get("total_peshgi")) or "0"
        # Peshgi raw data (stored + carried into operations autofill).
        peshgi = {
            "tcs_no": normalize_text(p.get("tcs_no")),
            "vehicle_no": vehicle_no,
            "driver_name": normalize_text(p.get("driver_name")),
            "driver_mobile": mobile,
            "from_place": normalize_text(p.get("from_place")),
            "to_place": normalize_text(p.get("to_place")),
            "fuel_type": normalize_text(p.get("fuel_type")),
            "labour": normalize_text(p.get("labour")),
            "fuel_amount": normalize_text(p.get("fuel_amount")),
            "roti": normalize_text(p.get("roti")),
            "babu": normalize_text(p.get("babu")),
            "hold": normalize_text(p.get("hold")),
            "total_peshgi": total,
            "payment_receiving": normalize_text(p.get("payment_receiving")),
        }
        # Map into the advance-voucher so a trip/voucher is created for this vehicle.
        voucher = {
            "vehicle_no": vehicle_no,
            "driver_name": normalize_text(p.get("driver_name")),
            "driver_mobile": mobile,
            "route": route,
            "voucher_date": normalize_text(p.get("entry_date")),
            "fuel_type": normalize_text(p.get("fuel_type")),
            "vendor_name": div["name"] if div else "",
            "payable_advance": total,
            "paid_advance": total,
            "total_advance": total,
            "paid_to_account": normalize_text(p.get("payment_receiving")),
            "remarks": f"Peshgi via TCI form. Labour {peshgi['labour'] or 0}, Roti {peshgi['roti'] or 0}, Babu {peshgi['babu'] or 0}, Hold {peshgi['hold'] or 0}.",
        }

        open_trip = open_trip_for_vehicle(vehicle["id"])
        if open_trip:
            tp_no = open_trip["tp_no"]
            voucher["tp_no"] = tp_no
            # keep existing voucher data if already present; always refresh peshgi.
            execute(
                "UPDATE trip_processes SET peshgi_json=?, voucher_json=COALESCE(NULLIF(voucher_json,''), ?), status='In Progress', updated_at=? WHERE id=?",
                (json.dumps(peshgi), json.dumps(voucher), now, open_trip["id"]),
            )
            trip_id = open_trip["id"]
        else:
            tp_no = next_tp_no()
            voucher["tp_no"] = tp_no
            trip_id = execute_returning_id(
                """INSERT INTO trip_processes(tp_no, vehicle_id, division_id, status, current_step, voucher_json, peshgi_json, created_by, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (tp_no, vehicle["id"], current_div_id, "In Progress", "advance-voucher", json.dumps(voucher), json.dumps(peshgi), session["user_id"], now, now),
            )
        # Save uploaded photos (TCS/THC/LR + QR) into the database, linked to this trip.
        saved_photos = []
        uploads = [(f, "tcs") for f in request.files.getlist("tcs_photos")]
        qr = request.files.get("qr_photo")
        if qr and qr.filename:
            uploads.append((qr, "qr"))
        for fs, kind in uploads[:12]:  # cap total photos per submit
            if not fs or not fs.filename:
                continue
            blob = fs.read()
            if not blob or len(blob) > 12 * 1024 * 1024:  # skip empty / >12MB
                continue
            mime = fs.mimetype or "image/jpeg"
            fname = secure_filename(fs.filename)[:200] or f"{kind}.jpg"
            pid = execute_returning_id(
                "INSERT INTO trip_photos(tp_no, trip_id, kind, filename, mime, photo_blob, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (tp_no, trip_id, kind, fname, mime, psycopg2.Binary(blob), session["user_id"], now),
            )
            saved_photos.append({"id": pid, "kind": kind, "filename": fname, "url": url_for("trip_photo", photo_id=pid)})

        record_audit("PESHGI_SAVE", "trip_processes", trip_id, {"tp_no": tp_no, "vehicle": vehicle_no, "photos": len(saved_photos)})
        return jsonify({
            "ok": True, "tp_no": tp_no, "photos": saved_photos,
            "message": f"Peshgi saved & voucher created. TP No: {tp_no}" + (f" ({len(saved_photos)} photo saved)" if saved_photos else ""),
        })

    @app.route("/photo/<int:photo_id>")
    @login_required
    def trip_photo(photo_id: int):
        row = query_one("SELECT filename, mime, photo_blob FROM trip_photos WHERE id=?", (photo_id,))
        if not row:
            abort(404)
        return send_file(io.BytesIO(bytes(row["photo_blob"])), mimetype=row["mime"] or "image/jpeg", download_name=row["filename"])

    @app.route("/trip/<path:tp_no>/photos")
    @login_required
    def trip_photos_page(tp_no: str):
        photos = query_all("SELECT id, kind, filename, created_at FROM trip_photos WHERE tp_no=? ORDER BY id", (tp_no,))
        return render_template("trip_photos.html", tp_no=tp_no, photos=[dict(p) for p in photos])

    @app.route("/operations/<step>")
    @login_required
    def operations(step: str):
        if step not in STEP_ORDER:
            abort(404)
        data = empty_defaults(step)
        now = datetime.now()
        # empty_defaults() pre-seeds every field with "", so fill these only when blank.
        for key in ["voucher_date", "tp_date", "tp_start_date", "loading_date", "reporting_date", "unloading_date", "tp_closed_date", "perf_date", "tp_exp_create_date"]:
            if not data.get(key):
                data[key] = now.strftime("%Y-%m-%d")
        for key in ["voucher_time", "tp_start_time", "loading_time", "reporting_time", "unloading_time", "tp_closed_time", "perf_time"]:
            if not data.get(key):
                data[key] = now.strftime("%H:%M")
        if step == "advance-voucher" and not data.get("tp_no"):
            data["tp_no"] = next_tp_no(data.get("assoc_company"))
        if step == "trip-creation" and not data.get("trip_id"):
            data["trip_id"] = "ATC" + now.strftime("%H%M%S")
        cur_div = get_current_division()
        # Vendor follows the current division (changes when division is switched).
        if step == "advance-voucher" and cur_div and not data.get("vendor_name"):
            data["vendor_name"] = cur_div["name"]
        if step == "trip-expense":
            if cur_div and not data.get("vendor_name"):
                data["vendor_name"] = cur_div["name"]
            if cur_div and not data.get("division_name"):
                data["division_name"] = f"DIV{cur_div['id']:04d}:{cur_div['name']}"
        return render_template(
            "operations.html",
            step=step,
            title=STEP_TITLES[step],
            sections=FIELD_CONFIG[step],
            data=data,
            step_index=STEP_ORDER.index(step),
            ftl_types=FTL_TYPES,
        )

    @app.route("/search")
    @login_required
    def search_page():
        q = request.args.get("q", "").strip()
        results = global_search(q) if q else []
        return render_template("search.html", q=q, results=results)

    @app.route("/api/search")
    @login_required
    def api_search():
        q = request.args.get("q", "").strip()
        return jsonify({"ok": True, "results": global_search(q) if q else []})

    @app.route("/api/division/change", methods=["POST"])
    @login_required
    @csrf_required
    def api_division_change():
        payload = request.get_json(force=True)
        division_id = int(payload.get("division_id"))
        allowed = query_one(
            "SELECT 1 FROM user_divisions WHERE user_id=? AND division_id=?",
            (session["user_id"], division_id),
        )
        if not allowed:
            return jsonify({"ok": False, "message": "This user does not have access to that division."}), 403
        division = query_one("SELECT * FROM divisions WHERE id=? AND active=1", (division_id,))
        if not division:
            return jsonify({"ok": False, "message": "Division not found."}), 404
        session["current_division_id"] = division_id
        record_audit("DIVISION_CHANGE", "divisions", division_id, {})
        return jsonify({"ok": True, "division": dict(division)})

    @app.route("/api/vehicle/<vehicle_no>")
    @login_required
    def api_vehicle(vehicle_no: str):
        vehicle = find_vehicle(vehicle_no)
        current = get_current_division()
        if not vehicle:
            return jsonify({"ok": False, "found": False, "message": "Vehicle not found. Add vehicle to continue."})
        division = query_one("SELECT * FROM divisions WHERE id=?", (vehicle["current_division_id"],))
        latest_trip = query_one(
            "SELECT * FROM trip_processes WHERE vehicle_id=? ORDER BY updated_at DESC LIMIT 1",
            (vehicle["id"],),
        )
        prev = previous_trip_summary(latest_trip)
        open_trip = open_trip_for_vehicle(vehicle["id"])
        return jsonify({
            "ok": True,
            "found": True,
            "vehicle": dict(vehicle),
            "division": dict(division) if division else None,
            "current_division_id": current["id"] if current else None,
            "needs_division_switch": bool(current and division and current["id"] != division["id"]),
            "previous": prev,
            "autofill": vehicle_autofill(vehicle, open_trip),
            "has_open_trip": bool(open_trip),
            "open_trip_no": open_trip["tp_no"] if open_trip else None,
            "open_trip_step": open_trip["current_step"] if open_trip else None,
        })

    @app.route("/api/vehicle/suggest")
    @login_required
    def api_vehicle_suggest():
        q = normalize_vehicle_no(request.args.get("q", ""))
        if not q:
            return jsonify({"ok": True, "results": []})
        rows = query_all(
            """
            SELECT v.vehicle_no, v.driver_name, v.ftl_type, d.name AS division_name
            FROM vehicles v LEFT JOIN divisions d ON d.id = v.current_division_id
            WHERE v.vehicle_no LIKE ? ORDER BY v.vehicle_no LIMIT 10
            """,
            (q + "%",),
        )
        return jsonify({"ok": True, "results": [dict(r) for r in rows]})

    @app.route("/api/pumps")
    @login_required
    def api_pumps():
        rows = query_all("SELECT id, name FROM pumps WHERE active=1 ORDER BY name")
        return jsonify({"ok": True, "results": [dict(r) for r in rows]})

    @app.route("/api/pumps", methods=["POST"])
    @login_required
    @csrf_required
    def api_pump_add():
        name = normalize_text((request.get_json(force=True) or {}).get("name"))[:120]
        if not name:
            return jsonify({"ok": False, "message": "Pump name is required."}), 400
        execute("INSERT INTO pumps(name, active, created_at) VALUES(?,1,?) ON CONFLICT (name) DO NOTHING", (name, datetime.utcnow().isoformat()))
        record_audit("PUMP_ADD", "pumps", None, {"name": name})
        rows = query_all("SELECT id, name FROM pumps WHERE active=1 ORDER BY name")
        return jsonify({"ok": True, "message": "Pump added.", "name": name, "results": [dict(r) for r in rows]})

    @app.route("/api/payment-accounts")
    @login_required
    def api_payment_accounts():
        acc_type = normalize_text(request.args.get("type")).upper()
        if acc_type in ("CASH", "BANK", "FUEL"):
            rows = query_all("SELECT id, name, acc_type FROM payment_accounts WHERE active=1 AND acc_type=? ORDER BY name", (acc_type,))
        else:
            rows = query_all("SELECT id, name, acc_type FROM payment_accounts WHERE active=1 ORDER BY name")
        return jsonify({"ok": True, "results": [dict(r) for r in rows]})

    @app.route("/api/payment-accounts", methods=["POST"])
    @login_required
    @csrf_required
    def api_payment_account_add():
        payload = request.get_json(force=True) or {}
        name = normalize_text(payload.get("name"))[:160]
        acc_type = normalize_text(payload.get("acc_type")).upper()
        if acc_type not in ("CASH", "BANK", "FUEL"):
            acc_type = "CASH"
        if not name:
            return jsonify({"ok": False, "message": "Account name is required."}), 400
        execute("INSERT INTO payment_accounts(name, acc_type, active, created_at) VALUES(?,?,1,?) ON CONFLICT (name) DO NOTHING", (name, acc_type, datetime.utcnow().isoformat()))
        record_audit("ACCOUNT_ADD", "payment_accounts", None, {"name": name, "acc_type": acc_type})
        rows = query_all("SELECT id, name, acc_type FROM payment_accounts WHERE active=1 AND acc_type=? ORDER BY name", (acc_type,))
        return jsonify({"ok": True, "message": "Account added.", "name": name, "results": [dict(r) for r in rows]})

    @app.route("/api/vehicle", methods=["POST"])
    @login_required
    @csrf_required
    def api_vehicle_save():
        payload = request.get_json(force=True)
        data = sanitize_vehicle_payload(payload)
        if not data["vehicle_no"]:
            return jsonify({"ok": False, "message": "Vehicle No. is required."}), 400
        if data["driver_mobile"] and len(data["driver_mobile"]) != 10:
            return jsonify({"ok": False, "message": "Driver mobile must be exactly 10 digits."}), 400
        division = query_one("SELECT * FROM divisions WHERE id=? AND active=1", (data["current_division_id"],))
        if not division:
            return jsonify({"ok": False, "message": "Valid division is required."}), 400
        existing = find_vehicle(data["vehicle_no"])
        now = datetime.utcnow().isoformat()
        if existing:
            execute(
                """
                UPDATE vehicles SET ftl_type=?, driver_name=?, driver_mobile=?, last_closing_km=?, opening_km=?,
                tank_capacity=?, mileage=?, fuel_type=?, current_division_id=?, vehicle_type=?, updated_at=? WHERE id=?
                """,
                (
                    data["ftl_type"], data["driver_name"], data["driver_mobile"], data["last_closing_km"],
                    data["opening_km"], data["tank_capacity"], data["mileage"], data["fuel_type"],
                    data["current_division_id"], data["vehicle_type"], now, existing["id"]
                ),
            )
            vehicle_id = existing["id"]
            action = "updated"
        else:
            vehicle_id = execute_returning_id(
                """
                INSERT INTO vehicles(vehicle_no, ftl_type, driver_name, driver_mobile, last_closing_km, opening_km,
                tank_capacity, mileage, fuel_type, current_division_id, vehicle_type, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    data["vehicle_no"], data["ftl_type"], data["driver_name"], data["driver_mobile"],
                    data["last_closing_km"], data["opening_km"], data["tank_capacity"], data["mileage"],
                    data["fuel_type"], data["current_division_id"], data["vehicle_type"], now, now
                ),
            )
            action = "created"
        record_audit("VEHICLE_" + action.upper(), "vehicles", vehicle_id, data)
        vehicle = query_one("SELECT * FROM vehicles WHERE id=?", (vehicle_id,))
        return jsonify({"ok": True, "message": f"Vehicle {action} successfully.", "vehicle": dict(vehicle)})

    @app.route("/api/vehicle/bulk", methods=["POST"])
    @login_required
    @csrf_required
    def api_vehicle_bulk():
        f = request.files.get("file")
        division_id = request.form.get("division_id") or session.get("current_division_id")
        if not f:
            return jsonify({"ok": False, "message": "Upload CSV/XLSX file."}), 400
        filename = secure_filename(f.filename or "vehicles")
        ext = Path(filename).suffix.lower()
        if ext not in [".csv", ".xlsx", ".xlsm"]:
            return jsonify({"ok": False, "message": "Only CSV or XLSX files are allowed."}), 400
        rows = parse_vehicle_upload(f, ext)
        added, updated, errors = 0, 0, []
        for i, row in enumerate(rows, start=2):
            try:
                payload = {
                    "vehicle_no": row.get("vehicle_no") or row.get("vehicle no") or row.get("vehicle") or "",
                    "ftl_type": row.get("ftl_type") or row.get("ftl type") or "",
                    "driver_name": row.get("driver_name") or row.get("driver name") or "",
                    "driver_mobile": row.get("driver_mobile") or row.get("driver no") or row.get("driver mobile") or "",
                    "fuel_type": row.get("fuel_type") or row.get("fuel type") or "",
                    "tank_capacity": row.get("tank_capacity") or row.get("tank capacity") or 0,
                    "mileage": row.get("mileage") or 0,
                    "last_closing_km": row.get("last_closing_km") or row.get("last closing km") or 0,
                    "opening_km": row.get("opening_km") or row.get("opening km") or 0,
                    "current_division_id": int(row.get("division_id") or division_id),
                    "vehicle_type": row.get("vehicle_type") or row.get("vehicle type") or "SELF",
                }
                data = sanitize_vehicle_payload(payload)
                if not data["vehicle_no"]:
                    raise ValueError("Vehicle No missing")
                existing = find_vehicle(data["vehicle_no"])
                now = datetime.utcnow().isoformat()
                if existing:
                    execute(
                        """
                        UPDATE vehicles SET ftl_type=?, driver_name=?, driver_mobile=?, last_closing_km=?, opening_km=?,
                        tank_capacity=?, mileage=?, fuel_type=?, current_division_id=?, vehicle_type=?, updated_at=? WHERE id=?
                        """,
                        (data["ftl_type"], data["driver_name"], data["driver_mobile"], data["last_closing_km"], data["opening_km"], data["tank_capacity"], data["mileage"], data["fuel_type"], data["current_division_id"], data["vehicle_type"], now, existing["id"]),
                    )
                    updated += 1
                else:
                    execute(
                        """
                        INSERT INTO vehicles(vehicle_no, ftl_type, driver_name, driver_mobile, last_closing_km, opening_km, tank_capacity, mileage, fuel_type, current_division_id, vehicle_type, created_at, updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (data["vehicle_no"], data["ftl_type"], data["driver_name"], data["driver_mobile"], data["last_closing_km"], data["opening_km"], data["tank_capacity"], data["mileage"], data["fuel_type"], data["current_division_id"], data["vehicle_type"], now, now),
                    )
                    added += 1
            except Exception as exc:
                errors.append(f"Row {i}: {exc}")
        record_audit("VEHICLE_BULK_UPLOAD", "vehicles", None, {"added": added, "updated": updated, "errors": errors[:20]})
        return jsonify({"ok": True, "added": added, "updated": updated, "errors": errors[:20]})

    @app.route("/api/operations/save-step", methods=["POST"])
    @login_required
    @csrf_required
    def api_save_step():
        payload = request.get_json(force=True)
        step = payload.get("step")
        if step not in STEP_ORDER:
            return jsonify({"ok": False, "message": "Invalid step."}), 400
        data = payload.get("data") or {}
        data = sanitize_step_data(data)
        tp_no = normalize_text(data.get("tp_no")) or payload.get("tp_no") or next_tp_no(data.get("assoc_company"))
        data["tp_no"] = tp_no
        vehicle = None
        vehicle_no = normalize_vehicle_no(data.get("vehicle_no", ""))
        if vehicle_no:
            vehicle = find_vehicle(vehicle_no)

        current_div_id = session["current_division_id"]
        # A vehicle belongs to one division: you must be working in that division to use it.
        if vehicle and vehicle["current_division_id"] != current_div_id:
            division = query_one("SELECT * FROM divisions WHERE id=?", (vehicle["current_division_id"],))
            return jsonify({
                "ok": False,
                "code": "division_mismatch",
                "message": f"{vehicle['vehicle_no']} {division['name'] if division else 'doosri'} division ka hai. Upar se us division me switch karke continue karein.",
                "division": dict(division) if division else None,
            }), 409

        # One active trip per vehicle: no new voucher until the current trip is closed (trip-expense).
        if vehicle:
            open_trip = open_trip_for_vehicle(vehicle["id"])
            if step == "advance-voucher" and open_trip and open_trip["tp_no"] != tp_no:
                return jsonify({
                    "ok": False,
                    "code": "trip_active",
                    "message": f"{vehicle['vehicle_no']} pehle se ek active trip ({open_trip['tp_no']}) me hai. Naya Advance Voucher tabhi banega jab us trip ka Trip Expense complete ho jaye.",
                    "open_trip_no": open_trip["tp_no"],
                }), 409
            if step != "advance-voucher" and open_trip:
                # Continue the same open trip across the remaining steps.
                tp_no = open_trip["tp_no"]
                data["tp_no"] = tp_no

        if not vehicle and vehicle_no:
            # minimal vehicle record, because Add Vehicle popup may be completed later.
            now = datetime.utcnow().isoformat()
            new_vehicle_id = execute_returning_id(
                """INSERT INTO vehicles(vehicle_no, current_division_id, ftl_type, driver_name, driver_mobile, last_closing_km, opening_km, vehicle_type, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (vehicle_no, session["current_division_id"], data.get("ftl_type", ""), data.get("driver_name", ""), data.get("driver_mobile", ""), to_float(data.get("last_closing_km")), to_float(data.get("opening_km")), data.get("vehicle_type", "SELF"), now, now),
            )
            vehicle = query_one("SELECT * FROM vehicles WHERE id=?", (new_vehicle_id,))
        trip = query_one("SELECT * FROM trip_processes WHERE tp_no=?", (tp_no,))
        now = datetime.utcnow().isoformat()
        col = STEP_DB_COL[step]
        status = "Closed" if step == "trip-expense" else "In Progress"
        if trip:
            execute(
                f"UPDATE trip_processes SET {col}=?, vehicle_id=COALESCE(?, vehicle_id), division_id=?, current_step=?, status=?, updated_at=?, closed_at=CASE WHEN ?='Closed' THEN ? ELSE closed_at END WHERE id=?",
                (json.dumps(data), vehicle["id"] if vehicle else None, session["current_division_id"], step, status, now, status, now, trip["id"]),
            )
            trip_id = trip["id"]
        else:
            cols = ["tp_no", "vehicle_id", "division_id", "status", "current_step", col, "created_by", "created_at", "updated_at"]
            vals = [tp_no, vehicle["id"] if vehicle else None, session["current_division_id"], status, step, json.dumps(data), session["user_id"], now, now]
            placeholders = ",".join(["?"] * len(vals))
            trip_id = execute_returning_id(f"INSERT INTO trip_processes({','.join(cols)}) VALUES({placeholders})", vals)
        if step == "trip-expense" and vehicle:
            update_vehicle_after_expense(vehicle["id"], data, tp_no)
        record_audit("STEP_SAVE", "trip_processes", trip_id, {"step": step, "tp_no": tp_no})
        next_step = None
        if STEP_ORDER.index(step) + 1 < len(STEP_ORDER):
            next_step = STEP_ORDER[STEP_ORDER.index(step) + 1]
        return jsonify({"ok": True, "message": f"{STEP_TITLES[step]} saved.", "tp_no": tp_no, "next_step": next_step})

    @app.route("/api/operations/load/<tp_no>")
    @login_required
    def api_load_trip(tp_no: str):
        trip = query_one("SELECT * FROM trip_processes WHERE tp_no=?", (tp_no,))
        if not trip:
            return jsonify({"ok": False, "message": "TP not found."}), 404
        payload = {"tp_no": trip["tp_no"], "status": trip["status"], "current_step": trip["current_step"], "steps": {}}
        for step, col in STEP_DB_COL.items():
            payload["steps"][step] = json.loads(trip[col]) if trip[col] else {}
        return jsonify({"ok": True, "trip": payload})

    @app.route("/api/operations/next-tp-no")
    @login_required
    def api_next_tp_no():
        company = request.args.get("company", "")
        return jsonify({"ok": True, "tp_no": next_tp_no(company)})

    @app.route("/api/reminders")
    @login_required
    def api_reminders():
        """Vehicles whose trip flow is started but not yet completed (closed)."""
        rows = query_all(
            """
            SELECT tp.*, v.vehicle_no, d.name AS division_name
            FROM trip_processes tp
            LEFT JOIN vehicles v ON v.id = tp.vehicle_id
            LEFT JOIN divisions d ON d.id = tp.division_id
            WHERE tp.status != 'Closed'
            ORDER BY tp.updated_at DESC LIMIT 50
            """
        )
        out = []
        for r in rows:
            done = [s for s in STEP_ORDER if r.get(STEP_DB_COL[s])]
            pending = [s for s in STEP_ORDER if s not in done]
            out.append({
                "tp_no": r["tp_no"],
                "vehicle_no": r["vehicle_no"] or "N/A",
                "division_name": r["division_name"] or "N/A",
                "done": [STEP_TITLES[s] for s in done],
                "pending_titles": [STEP_TITLES[s] for s in pending],
                "next_step": pending[0] if pending else None,
            })
        return jsonify({"ok": True, "count": len(out), "results": out})

    @app.route("/api/pdf/create", methods=["POST"])
    @login_required
    @csrf_required
    def api_pdf_create():
        payload = request.get_json(force=True)
        step = payload.get("step")
        if step not in STEP_ORDER:
            return jsonify({"ok": False, "message": "Invalid step."}), 400
        data = sanitize_step_data(payload.get("data") or {})
        tp_no = normalize_text(data.get("tp_no")) or next_tp_no(data.get("assoc_company"))
        pdf_bytes = make_pdf_bytes(STEP_TITLES[step], tp_no, data)
        now = datetime.utcnow().isoformat()
        filename = f"{secure_filename(tp_no)}_{step}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
        pdf_id = execute_returning_id(
            """
            INSERT INTO pdf_documents(tp_no, step_name, title, pdf_blob, filename, is_saved, created_by, created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (tp_no, step, STEP_TITLES[step], psycopg2.Binary(pdf_bytes), filename, 1, session["user_id"], now),
        )
        record_audit("PDF_CREATE", "pdf_documents", pdf_id, {"step": step, "tp_no": tp_no})
        return jsonify({"ok": True, "pdf_id": pdf_id, "view_url": url_for("pdf_view", pdf_id=pdf_id)})

    @app.route("/pdf/<int:pdf_id>")
    @login_required
    def pdf_view(pdf_id: int):
        doc = query_one("SELECT id, tp_no, step_name, title, filename, is_saved, created_at FROM pdf_documents WHERE id=?", (pdf_id,))
        if not doc:
            abort(404)
        return render_template("pdf_view.html", doc=doc)

    @app.route("/pdf/file/<int:pdf_id>")
    @login_required
    def pdf_file(pdf_id: int):
        doc = query_one("SELECT * FROM pdf_documents WHERE id=?", (pdf_id,))
        if not doc:
            abort(404)
        return send_file(io.BytesIO(bytes(doc["pdf_blob"])), mimetype="application/pdf", as_attachment=False, download_name=doc["filename"])

    @app.route("/pdf/download/<int:pdf_id>")
    @login_required
    def pdf_download(pdf_id: int):
        doc = query_one("SELECT * FROM pdf_documents WHERE id=?", (pdf_id,))
        if not doc:
            abort(404)
        execute("UPDATE pdf_documents SET is_saved=1 WHERE id=?", (pdf_id,))
        record_audit("PDF_DOWNLOAD", "pdf_documents", pdf_id, {})
        return send_file(io.BytesIO(bytes(doc["pdf_blob"])), mimetype="application/pdf", as_attachment=True, download_name=doc["filename"])

    @app.route("/api/pdf/save/<int:pdf_id>", methods=["POST"])
    @login_required
    @csrf_required
    def api_pdf_save(pdf_id: int):
        doc = query_one("SELECT id FROM pdf_documents WHERE id=?", (pdf_id,))
        if not doc:
            abort(404)
        execute("UPDATE pdf_documents SET is_saved=1 WHERE id=?", (pdf_id,))
        record_audit("PDF_SAVE", "pdf_documents", pdf_id, {})
        return jsonify({"ok": True, "message": "PDF saved in database."})

    return app


def get_db() -> Any:
    if "db_conn" not in g:
        g.db_conn = pg_connect()
    return g.db_conn


def _sql(sql: str) -> str:
    """Translate the codebase's '?' placeholders to psycopg2's '%s'."""
    return sql.replace("?", "%s")


def query_one(sql: str, params: Iterable[Any] = ()) -> Row | None:
    with get_db().cursor() as cur:
        cur.execute(_sql(sql), tuple(params))
        return cur.fetchone()


def query_all(sql: str, params: Iterable[Any] = ()) -> List[Row]:
    with get_db().cursor() as cur:
        cur.execute(_sql(sql), tuple(params))
        return list(cur.fetchall())


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    """Run a write statement (connection is autocommit)."""
    with get_db().cursor() as cur:
        cur.execute(_sql(sql), tuple(params))


def execute_returning_id(sql: str, params: Iterable[Any] = ()) -> int | None:
    """Run an INSERT and return the new row's id via RETURNING id."""
    with get_db().cursor() as cur:
        cur.execute(_sql(sql) + " RETURNING id", tuple(params))
        row = cur.fetchone()
        return row["id"] if row else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


def csrf_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not token or token != session.get("csrf_token"):
            return jsonify({"ok": False, "message": "Invalid CSRF token."}), 403
        return fn(*args, **kwargs)
    return wrapper


def current_user() -> Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return query_one("SELECT id, username, full_name, role FROM users WHERE id=? AND active=1", (user_id,))


def get_current_division() -> Row | None:
    did = session.get("current_division_id")
    if not did:
        return None
    return query_one("SELECT * FROM divisions WHERE id=? AND active=1", (did,))


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_vehicle_no(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value).upper())


def to_float(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except Exception:
        return 0.0


def sanitize_step_data(data: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for key, value in data.items():
        safe_key = re.sub(r"[^a-zA-Z0-9_]", "", str(key))[:80]
        if isinstance(value, str):
            clean[safe_key] = value.strip()[:5000]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[safe_key] = value
        elif isinstance(value, list):
            clean[safe_key] = value[:200]
        else:
            clean[safe_key] = str(value)[:5000]
    if "vehicle_no" in clean:
        clean["vehicle_no"] = normalize_vehicle_no(clean.get("vehicle_no"))
    return clean


def sanitize_vehicle_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "vehicle_no": normalize_vehicle_no(payload.get("vehicle_no")),
        "ftl_type": normalize_text(payload.get("ftl_type"))[:100],
        "driver_name": normalize_text(payload.get("driver_name"))[:150],
        "driver_mobile": re.sub(r"[^0-9]", "", normalize_text(payload.get("driver_mobile")))[:10],
        "last_closing_km": to_float(payload.get("last_closing_km")),
        "opening_km": to_float(payload.get("opening_km")),
        "tank_capacity": to_float(payload.get("tank_capacity")),
        "mileage": to_float(payload.get("mileage")),
        "fuel_type": normalize_text(payload.get("fuel_type"))[:50],
        "current_division_id": int(payload.get("current_division_id") or session.get("current_division_id") or 1),
        "vehicle_type": normalize_text(payload.get("vehicle_type") or "SELF")[:50],
    }


def find_vehicle(vehicle_no: str) -> Row | None:
    return query_one("SELECT * FROM vehicles WHERE vehicle_no=?", (normalize_vehicle_no(vehicle_no),))


def open_trip_for_vehicle(vehicle_id: int) -> Row | None:
    """The vehicle's in-progress trip (not yet closed via trip-expense), if any."""
    return query_one(
        "SELECT * FROM trip_processes WHERE vehicle_id=? AND status!='Closed' ORDER BY updated_at DESC LIMIT 1",
        (vehicle_id,),
    )


def merged_trip_data(trip: Row | None) -> Dict[str, Any]:
    """Flatten all saved step JSON for a trip into one dict for cross-step autofill."""
    merged: Dict[str, Any] = {}
    if not trip:
        return merged
    # peshgi first (base), then the 7 step JSONs override it.
    for col in ["peshgi_json"] + list(STEP_DB_COL.values()):
        raw = trip.get(col)
        if raw:
            try:
                merged.update(json.loads(raw))
            except Exception:
                pass
    merged["tp_no"] = trip.get("tp_no", merged.get("tp_no", ""))
    return merged


def vehicle_autofill(vehicle: Row, trip: Row | None) -> Dict[str, Any]:
    """Values to push into the current form when a vehicle is selected.

    Master vehicle record first, then anything already captured on the open trip
    (so today's voucher data shows up when you continue the flow tomorrow)."""
    data: Dict[str, Any] = {
        "vehicle_no": vehicle["vehicle_no"],
        "ftl_type": vehicle.get("ftl_type") or "",
        "vehicle_type": vehicle.get("vehicle_type") or "",
        "driver_name": vehicle.get("driver_name") or "",
        "driver_mobile": vehicle.get("driver_mobile") or "",
        "fuel_type": vehicle.get("fuel_type") or "",
        "tank_capacity": vehicle.get("tank_capacity") or "",
        "mileage": vehicle.get("mileage") or "",
        "last_closing_km": vehicle.get("last_closing_km") if vehicle.get("last_closing_km") is not None else "",
        "opening_km": vehicle.get("opening_km") if vehicle.get("opening_km") is not None else "",
    }
    # Carry EVERYTHING captured on the open trip so far (all steps) — the current
    # form only applies the keys that match its own fields.
    merged = merged_trip_data(trip)
    for key, val in merged.items():
        if isinstance(val, list):
            continue  # skip line-item tables
        if val not in (None, ""):
            data[key] = val
    data["vehicle_no"] = vehicle["vehicle_no"]  # never let a stale value override
    return data


def division_code(name: str | None) -> str:
    """Short code for a division: explicit map, else first 2 letters of first word."""
    if not name:
        return "XX"
    if name in DIVISION_CODES:
        return DIVISION_CODES[name]
    word = re.sub(r"[^A-Za-z]", "", name.split()[0]) if name.split() else ""
    return (word[:2] or "XX").upper()


def company_code(value: Any) -> str:
    """Sanitize the vehicle-associated-company segment (letters/digits, 2-5 chars)."""
    code = re.sub(r"[^A-Za-z0-9]", "", normalize_text(value).upper())[:5]
    return code or DEFAULT_VEHICLE_COMPANY


def financial_year(dt: datetime | None = None) -> str:
    """Indian financial year (Apr-Mar) as two-digit pair, e.g. 26-27."""
    dt = dt or datetime.now()
    start = dt.year if dt.month >= 4 else dt.year - 1
    return f"{str(start)[-2:]}-{str(start + 1)[-2:]}"


def next_tp_no(company: Any = None, division: Row | None = None) -> str:
    """Build the next TP number: DIVISION / ASSOC. COMPANY / FY / MONTH / SR. NO.

    e.g. AJ/TCI/26-27/06/0001  (serial resets per division+company+FY+month).
    """
    now = datetime.now()
    div = division or get_current_division()
    dcode = division_code(div["name"] if div else None)
    comp = company_code(company)
    prefix = f"{dcode}/{comp}/{financial_year(now)}/{now.strftime('%m')}/"
    row = query_one("SELECT COUNT(*) AS c FROM trip_processes WHERE tp_no LIKE ?", (prefix + "%",))
    serial = (row["c"] if row else 0) + 1
    return f"{prefix}{serial:04d}"


def empty_defaults(step: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for section in FIELD_CONFIG[step]:
        for field in section.get("fields", []):
            if "default" in field:
                data[field["name"]] = field["default"]
            else:
                data[field["name"]] = ""
    return data


def previous_trip_summary(latest_trip: Row | None) -> Dict[str, Any]:
    prev = {"previous_route": "N/A", "previous_trip": "N/A", "previous_tp_date": "N/A", "previous_tp_adv": "0"}
    if not latest_trip:
        return prev
    prev["previous_trip"] = latest_trip["tp_no"] or "N/A"
    if latest_trip["closed_at"]:
        prev["previous_tp_date"] = latest_trip["closed_at"][:10]
    try:
        trip_creation = json.loads(latest_trip["trip_creation_json"] or "{}")
        voucher = json.loads(latest_trip["voucher_json"] or "{}")
        prev["previous_route"] = trip_creation.get("route") or voucher.get("route") or "N/A"
        prev["previous_tp_adv"] = voucher.get("total_advance") or voucher.get("paid_advance") or "0"
    except Exception:
        pass
    return prev


def update_vehicle_after_expense(vehicle_id: int, data: Dict[str, Any], tp_no: str) -> None:
    closing = to_float(data.get("tp_close_km") or data.get("closing_km") or data.get("run_km") or data.get("opening_km"))
    if closing <= 0:
        closing = to_float(data.get("run_km"))
    now = datetime.utcnow().isoformat()
    execute("UPDATE vehicles SET last_closing_km=?, opening_km=?, updated_at=? WHERE id=?", (closing, closing + 1 if closing else 0, now, vehicle_id))


def parse_vehicle_upload(file_storage, ext: str) -> List[Dict[str, Any]]:
    if ext == ".csv":
        text = file_storage.stream.read().decode("utf-8-sig", errors="ignore")
        reader = csv.DictReader(io.StringIO(text))
        return [{str(k).strip().lower(): v for k, v in row.items()} for row in reader]
    if openpyxl is None:
        raise RuntimeError("openpyxl is required for Excel upload.")
    wb = openpyxl.load_workbook(file_storage.stream, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h or "").strip().lower() for h in rows[0]]
    output = []
    for row in rows[1:]:
        output.append({headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))})
    return output


def global_search(q: str) -> List[Dict[str, Any]]:
    safe = f"%{q.strip()}%"
    if not q.strip():
        return []
    results: List[Dict[str, Any]] = []
    for row in query_all(
        """
        SELECT v.vehicle_no, v.driver_name, v.driver_mobile, v.ftl_type, d.name AS division_name, v.updated_at
        FROM vehicles v LEFT JOIN divisions d ON d.id=v.current_division_id
        WHERE v.vehicle_no LIKE ? OR v.driver_name LIKE ? OR v.driver_mobile LIKE ? OR v.ftl_type LIKE ?
        ORDER BY v.updated_at DESC LIMIT 25
        """,
        (safe, safe, safe, safe),
    ):
        results.append({"type": "Vehicle", "title": row["vehicle_no"], "subtitle": row["driver_name"] or row["ftl_type"], "details": f"Division: {row['division_name']} | Mobile: {row['driver_mobile'] or 'N/A'}"})
    for row in query_all(
        """
        SELECT tp.tp_no, tp.status, tp.current_step, tp.updated_at, v.vehicle_no, d.name AS division_name
        FROM trip_processes tp
        LEFT JOIN vehicles v ON v.id=tp.vehicle_id
        LEFT JOIN divisions d ON d.id=tp.division_id
        WHERE tp.tp_no LIKE ? OR v.vehicle_no LIKE ? OR tp.voucher_json LIKE ? OR tp.trip_creation_json LIKE ? OR tp.expense_json LIKE ?
        ORDER BY tp.updated_at DESC LIMIT 25
        """,
        (safe, safe, safe, safe, safe),
    ):
        results.append({"type": "Trip", "title": row["tp_no"], "subtitle": f"{STEP_TITLES.get(row['current_step'], row['current_step'])} | {row['status']}", "details": f"Vehicle: {row['vehicle_no'] or 'N/A'} | Division: {row['division_name'] or 'N/A'}"})
    for row in query_all(
        """
        SELECT id, tp_no, title, step_name, filename, created_at FROM pdf_documents
        WHERE tp_no LIKE ? OR title LIKE ? OR filename LIKE ?
        ORDER BY created_at DESC LIMIT 15
        """,
        (safe, safe, safe),
    ):
        results.append({"type": "PDF", "title": row["filename"], "subtitle": f"{row['title']} | {row['tp_no']}", "details": url_for("pdf_view", pdf_id=row["id"])})
    return results[:50]


def make_pdf_bytes(title: str, tp_no: str, data: Dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=28, bottomMargin=28, leftMargin=28, rightMargin=28)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph("AJAY TRANSPORT - LOGISTICS CRM", styles["Title"]))
    story.append(Paragraph(f"{title} | TP No: {tp_no}", styles["Heading2"]))
    div = get_current_division()
    story.append(Paragraph(f"Division: {div['name'] if div else 'N/A'} | Generated: {datetime.now().strftime('%d-%m-%Y %H:%M')}", styles["Normal"]))
    story.append(Spacer(1, 12))
    rows = [["Field", "Value"]]
    for key, value in data.items():
        if isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        rows.append([key.replace("_", " ").title(), str(value or "")])
    table = Table(rows, colWidths=[180, 340])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#143A5A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B7C7D8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8FB")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(table)
    story.append(Spacer(1, 18))
    story.append(Paragraph("This PDF is generated and stored inside CRM database.", styles["Italic"]))
    doc.build(story)
    buffer.seek(0)
    return buffer.read()


def record_audit(action: str, table_name: str, record_id: Any, metadata: Dict[str, Any]) -> None:
    try:
        execute(
            "INSERT INTO audit_logs(user_id, action, table_name, record_id, metadata, created_at) VALUES(?,?,?,?,?,?)",
            (session.get("user_id"), action, table_name, str(record_id or ""), json.dumps(metadata), datetime.utcnow().isoformat()),
        )
    except Exception:
        pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS divisions(
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users(
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_divisions(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    division_id INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    PRIMARY KEY(user_id, division_id)
);
CREATE TABLE IF NOT EXISTS vehicles(
    id SERIAL PRIMARY KEY,
    vehicle_no TEXT NOT NULL UNIQUE,
    ftl_type TEXT,
    driver_name TEXT,
    driver_mobile TEXT,
    last_closing_km DOUBLE PRECISION DEFAULT 0,
    opening_km DOUBLE PRECISION DEFAULT 0,
    tank_capacity DOUBLE PRECISION DEFAULT 0,
    mileage DOUBLE PRECISION DEFAULT 0,
    fuel_type TEXT DEFAULT '',
    current_division_id INTEGER NOT NULL REFERENCES divisions(id),
    vehicle_type TEXT DEFAULT 'SELF',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vehicles_no ON vehicles(vehicle_no);
CREATE INDEX IF NOT EXISTS idx_vehicles_division ON vehicles(current_division_id);
CREATE TABLE IF NOT EXISTS pumps(
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_accounts(
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    acc_type TEXT NOT NULL DEFAULT 'CASH',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trip_processes(
    id SERIAL PRIMARY KEY,
    tp_no TEXT NOT NULL UNIQUE,
    vehicle_id INTEGER REFERENCES vehicles(id),
    division_id INTEGER NOT NULL REFERENCES divisions(id),
    status TEXT NOT NULL DEFAULT 'Draft',
    current_step TEXT NOT NULL,
    voucher_json TEXT,
    fuel_json TEXT,
    trip_creation_json TEXT,
    reporting_json TEXT,
    unloading_json TEXT,
    performance_json TEXT,
    expense_json TEXT,
    peshgi_json TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_trips_tp ON trip_processes(tp_no);
CREATE INDEX IF NOT EXISTS idx_trips_vehicle ON trip_processes(vehicle_id);
CREATE TABLE IF NOT EXISTS pdf_documents(
    id SERIAL PRIMARY KEY,
    tp_no TEXT NOT NULL,
    step_name TEXT NOT NULL,
    title TEXT NOT NULL,
    pdf_blob BYTEA NOT NULL,
    filename TEXT NOT NULL,
    is_saved INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdfs_tp ON pdf_documents(tp_no);
CREATE TABLE IF NOT EXISTS trip_photos(
    id SERIAL PRIMARY KEY,
    tp_no TEXT NOT NULL,
    trip_id INTEGER REFERENCES trip_processes(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'tcs',
    filename TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT 'image/jpeg',
    photo_blob BYTEA NOT NULL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trip_photos_tp ON trip_photos(tp_no);
CREATE TABLE IF NOT EXISTS audit_logs(
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    table_name TEXT,
    record_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
"""


def ensure_database() -> None:
    """Create the target database if it does not exist (skipped for DATABASE_URL)."""
    if DATABASE_URL:
        return
    try:
        admin = pg_connect(dbname="postgres")
    except Exception as exc:  # pragma: no cover - depends on local server
        print(f"[init_db] Could not reach PostgreSQL server: {exc}")
        raise
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (PG_CONFIG["dbname"],))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{PG_CONFIG["dbname"]}"')
                print(f"[init_db] Created database '{PG_CONFIG['dbname']}'.")
    finally:
        admin.close()


def init_db() -> None:
    ensure_database()
    conn = pg_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            # Migration for databases created before these columns existed.
            cur.execute("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS tank_capacity DOUBLE PRECISION DEFAULT 0")
            cur.execute("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS mileage DOUBLE PRECISION DEFAULT 0")
            cur.execute("ALTER TABLE vehicles ADD COLUMN IF NOT EXISTS fuel_type TEXT DEFAULT ''")
            cur.execute("ALTER TABLE trip_processes ADD COLUMN IF NOT EXISTS peshgi_json TEXT")
            now = datetime.utcnow().isoformat()
            for name in DIVISION_NAMES:
                cur.execute("INSERT INTO divisions(name, active) VALUES(%s,1) ON CONFLICT (name) DO NOTHING", (name,))
            for pump in PUMP_SEED:
                cur.execute("INSERT INTO pumps(name, active, created_at) VALUES(%s,1,%s) ON CONFLICT (name) DO NOTHING", (pump, now))
            for acc_name, acc_type in PAYMENT_ACCOUNT_SEED:
                cur.execute("INSERT INTO payment_accounts(name, acc_type, active, created_at) VALUES(%s,%s,1,%s) ON CONFLICT (name) DO NOTHING", (acc_name, acc_type, now))
            # Passwords come from env in production (set ADMIN_PASSWORD / ARUN_PASSWORD on the host).
            admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")
            arun_pw = os.environ.get("ARUN_PASSWORD", "admin123")
            cur.execute("SELECT id FROM users WHERE username='admin'")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users(username,password_hash,full_name,role,active,created_at) VALUES(%s,%s,%s,%s,1,%s)",
                    ("admin", generate_password_hash(admin_pw), "Admin User", "admin", now),
                )
            elif os.environ.get("ADMIN_PASSWORD"):
                cur.execute("UPDATE users SET password_hash=%s WHERE username='admin'", (generate_password_hash(admin_pw),))
            cur.execute("SELECT id FROM users WHERE username='arun'")
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users(username,password_hash,full_name,role,active,created_at) VALUES(%s,%s,%s,%s,1,%s)",
                    ("arun", generate_password_hash(arun_pw), "Arun E1038", "operator", now),
                )
            elif os.environ.get("ARUN_PASSWORD"):
                cur.execute("UPDATE users SET password_hash=%s WHERE username='arun'", (generate_password_hash(arun_pw),))
            cur.execute("SELECT id FROM users")
            user_rows = cur.fetchall()
            cur.execute("SELECT id FROM divisions")
            div_rows = cur.fetchall()
            for u in user_rows:
                for d in div_rows:
                    cur.execute(
                        "INSERT INTO user_divisions(user_id, division_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                        (u["id"], d["id"]),
                    )
            # Demo vehicles to verify division popup and auto-fill.
            demo = [
                ("2434HR47D", "32 FEET SXL", "BADRE ALAM S/O BADULLA", "9889167991", 1860, 1861, "AJAY TRANSPORT", "SELF"),
                ("HR55AB1234", "32 FEET MXL", "RAMESH KUMAR", "9876543210", 45000, 45001, "SANJAY CARRIER", "SELF"),
                ("RJ14CD5678", "20 FEET", "SURESH YADAV", "9988776655", 12000, 12001, "KAVITA SHARMA", "ATTACHED"),
            ]
            for vehicle_no, ftl, driver, mobile, close_km, open_km, div_name, vtype in demo:
                cur.execute("SELECT id FROM divisions WHERE name=%s", (div_name,))
                div = cur.fetchone()
                if not div:
                    continue
                cur.execute(
                    """
                    INSERT INTO vehicles(vehicle_no, ftl_type, driver_name, driver_mobile, last_closing_km, opening_km, current_division_id, vehicle_type, created_at, updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (vehicle_no) DO NOTHING
                    """,
                    (vehicle_no, ftl, driver, mobile, close_km, open_km, div["id"], vtype, now, now),
                )
        print("[init_db] PostgreSQL schema ready and seed data ensured.")
    finally:
        conn.close()


app = create_app()

if __name__ == "__main__":
    try:
        init_db()
    except psycopg2.OperationalError as exc:
        target = DATABASE_URL or f"{PG_CONFIG['user']}@{PG_CONFIG['host']}:{PG_CONFIG['port']}/{PG_CONFIG['dbname']}"
        print("\n[ERROR] Could not connect to PostgreSQL:\n  " + str(exc).strip())
        print(f"  Tried: {target}")
        print("  Make sure PostgreSQL is running and credentials in .env are correct.\n")
        raise SystemExit(1)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
