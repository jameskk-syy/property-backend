# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Safaricom Daraja (M-Pesa) HTTP layer.

Implements OAuth token retrieval and Lipa Na M-Pesa Online (STK Push) using the
per-organization credentials stored in the `Mpesa Settings` doctype. Credentials
and the public callback base URL (e.g. an ngrok HTTPS URL) are resolved per org.
"""

import base64
from datetime import datetime

import frappe
import requests

from property_management.property_management.doctype.mpesa_settings.mpesa_settings import get_credentials


CALLBACK_PATH = "/api/method/property_management.api.mpesa.stk_callback"


def _timestamp():
	return datetime.now().strftime("%Y%m%d%H%M%S")


def _normalize_phone(phone):
	"""Return a 2547XXXXXXXX / 2541XXXXXXXX MSISDN from various input formats."""
	digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
	if digits.startswith("0"):
		digits = "254" + digits[1:]
	elif digits.startswith("7") or digits.startswith("1"):
		digits = "254" + digits
	elif digits.startswith("254"):
		pass
	return digits


def get_access_token(creds):
	"""Fetch a Daraja OAuth token. Raises on failure."""
	url = f"{creds['base_url']}/oauth/v1/generate?grant_type=client_credentials"
	resp = requests.get(url, auth=(creds["consumer_key"], creds["consumer_secret"]), timeout=30)
	resp.raise_for_status()
	token = resp.json().get("access_token")
	if not token:
		frappe.throw("Daraja OAuth returned no access_token")
	return token


def stk_push(organization, phone, amount, account_reference, description="Payment", callback_path=None):
	"""
	Initiate an STK push. Returns the Daraja response dict (contains
	CheckoutRequestID / MerchantRequestID on success). Raises if not configured.
	"""
	creds = get_credentials(organization)
	if not creds:
		frappe.throw(f"M-Pesa is not configured for organization '{organization}'.")
	if not creds.get("callback_base_url"):
		frappe.throw("M-Pesa callback base URL is not set (paste your ngrok HTTPS URL in Mpesa Settings).")
	if not (creds.get("consumer_key") and creds.get("consumer_secret") and creds.get("passkey") and creds.get("shortcode")):
		frappe.throw("M-Pesa credentials incomplete (need consumer key/secret, passkey, shortcode).")

	token = get_access_token(creds)
	ts = _timestamp()
	shortcode = creds["shortcode"]
	password = base64.b64encode(f"{shortcode}{creds['passkey']}{ts}".encode()).decode()
	callback_url = creds["callback_base_url"] + (callback_path or CALLBACK_PATH)

	# Safaricom requires an integer amount (KES). Enforce a minimum of 1.
	amt = max(1, int(round(float(amount))))

	payload = {
		"BusinessShortCode": shortcode,
		"Password": password,
		"Timestamp": ts,
		"TransactionType": "CustomerPayBillOnline",
		"Amount": amt,
		"PartyA": _normalize_phone(phone),
		"PartyB": shortcode,
		"PhoneNumber": _normalize_phone(phone),
		"CallBackURL": callback_url,
		"AccountReference": (account_reference or "RENT")[:12],
		"TransactionDesc": (description or "Payment")[:20],
	}

	url = f"{creds['base_url']}/mpesa/stkpush/v1/processrequest"
	resp = requests.post(url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=30)
	data = {}
	try:
		data = resp.json()
	except Exception:
		pass
	if resp.status_code >= 400 or str(data.get("ResponseCode", "1")) != "0":
		frappe.log_error(title="Daraja STK push failed", message=f"{resp.status_code} {resp.text}")
		frappe.throw(f"STK push failed: {data.get('errorMessage') or data.get('ResponseDescription') or resp.text}")
	return data
