# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


# Daraja API base URLs by environment.
API_BASE = {
	"sandbox": "https://sandbox.safaricom.co.ke",
	"production": "https://api.safaricom.co.ke",
}


class MpesaSettings(Document):
	pass


def get_settings(organization):
	"""
	Return the Mpesa Settings doc for an organization, or None. Resolves the
	organization the same way the rest of the app does when not given.
	"""
	if not organization:
		from property_management.api.utils import resolve_organization
		organization = resolve_organization()
	if not organization:
		return None
	name = frappe.db.get_value("Mpesa Settings", {"organization": organization}, "name")
	if not name:
		return None
	return frappe.get_doc("Mpesa Settings", name)


def get_credentials(organization):
	"""
	Return a dict of decrypted Daraja credentials for an organization, or None
	if not configured / disabled.
	"""
	doc = get_settings(organization)
	if not doc or not doc.enabled:
		return None
	env = doc.environment or "sandbox"
	return {
		"organization": doc.organization,
		"environment": env,
		"base_url": API_BASE.get(env, API_BASE["sandbox"]),
		"shortcode": doc.shortcode,
		"paybill_number": doc.paybill_number or doc.shortcode,
		"consumer_key": doc.consumer_key,
		"consumer_secret": doc.get_password("consumer_secret") if doc.consumer_secret else None,
		"passkey": doc.get_password("passkey") if doc.passkey else None,
		"initiator_name": doc.initiator_name,
		"security_credential": doc.get_password("security_credential") if doc.security_credential else None,
		"b2c_shortcode": doc.b2c_shortcode,
		"callback_base_url": (doc.callback_base_url or "").rstrip("/"),
	}
