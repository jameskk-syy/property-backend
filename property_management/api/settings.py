# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Settings API - rent billing deadlines, late-fee & standing-charge configuration.

Settings live on the Organization; a Property may override any of them.
`get_billing_settings(property)` returns the effective values (property override
falling back to org). `list_property_billing_settings` returns a paginated table
of effective settings per property for the admin UI, and
`set_property_billing_settings` updates a single property's overrides.
"""

import frappe

from property_management.api.v2 import envelope
from property_management.api.utils import resolve_organization

_ORG_FIELDS = [
	"rent_due_day", "late_fee_grace_days", "late_fee_amount",
	"second_penalty_days", "second_penalty_amount",
	"garbage_charge", "water_rate_per_unit",
]

# Fields a Property may override (subset of org fields that exist on Property).
_PROPERTY_FIELDS = [
	"rent_due_day", "late_fee_amount", "second_penalty_amount",
	"garbage_charge", "water_rate_per_unit",
]

_DEFAULTS = {
	"rent_due_day": 5,
	"late_fee_grace_days": 0,
	"late_fee_amount": 500,
	"second_penalty_days": 10,
	"second_penalty_amount": 1000,
	"garbage_charge": 0,
	"water_rate_per_unit": 150,
}


def _org_settings(organization):
	settings = dict(_DEFAULTS)
	if organization:
		org = frappe.db.get_value("Organization", organization, _ORG_FIELDS, as_dict=True) or {}
		for k in _ORG_FIELDS:
			if org.get(k) not in (None, ""):
				settings[k] = org[k]
	return settings


def _effective_settings(property=None, organization=None):
	organization = resolve_organization(explicit=organization)
	settings = _org_settings(organization)

	if property and frappe.db.exists("Property", property):
		prop = frappe.db.get_value("Property", property, _PROPERTY_FIELDS, as_dict=True) or {}
		for k, v in prop.items():
			# Treat None/empty as "inherit"; an explicit 0 is a valid override.
			if v not in (None, ""):
				settings[k] = v

	settings["organization"] = organization
	settings["property"] = property
	return settings


@frappe.whitelist()
@envelope
def get_billing_settings(property=None, organization=None):
	"""Return effective billing settings (property override -> org -> defaults)."""
	return _effective_settings(property=property, organization=organization)


@frappe.whitelist()
@envelope
def set_billing_settings(organization=None, **kwargs):
	"""Update the Organization-level billing settings and return the effective values."""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	doc = frappe.get_doc("Organization", organization)
	for k in _ORG_FIELDS:
		if k in kwargs and kwargs[k] not in (None, ""):
			doc.set(k, kwargs[k])
	doc.save(ignore_permissions=True)

	return _effective_settings(organization=organization)


@frappe.whitelist()
@envelope
def list_property_billing_settings(organization=None, page=1, page_size=10, search=None):
	"""
	Paginated list of effective billing settings per property.

	Each row shows the property's own override values plus the effective
	(override -> org -> default) values the UI can display and edit.
	"""
	organization = resolve_organization(explicit=organization)
	filters = {}
	if organization:
		filters["organization"] = organization
	if search:
		filters["property_name"] = ["like", f"%{search}%"]

	page = int(page or 1)
	page_size = int(page_size or 10)
	start = (page - 1) * page_size

	total = frappe.db.count("Property", filters)
	props = frappe.get_all(
		"Property",
		filters=filters,
		fields=["name", "property_name"] + _PROPERTY_FIELDS,
		order_by="property_name asc",
		start=start,
		page_length=page_size,
	)

	org_defaults = _org_settings(organization)
	rows = []
	for p in props:
		effective = dict(org_defaults)
		for k in _PROPERTY_FIELDS:
			if p.get(k) not in (None, ""):
				effective[k] = p[k]
		rows.append({
			"property": p.name,
			"property_name": p.property_name or p.name,
			# raw overrides (None/0 means "inherit from org")
			"overrides": {k: p.get(k) for k in _PROPERTY_FIELDS},
			# effective values shown in the table
			"effective": {k: effective[k] for k in _PROPERTY_FIELDS + ["rent_due_day"]},
		})

	return {
		"rows": rows,
		"total": total,
		"page": page,
		"page_size": page_size,
		"org_defaults": org_defaults,
	}


_MPESA_PLAIN_FIELDS = ["enabled", "environment", "shortcode", "paybill_number",
					   "consumer_key", "initiator_name", "b2c_shortcode", "callback_base_url"]
# Password fields are never returned in plaintext; we only report whether they're set.
_MPESA_SECRET_FIELDS = ["consumer_secret", "passkey", "security_credential"]


@frappe.whitelist()
@envelope
def get_mpesa_settings(organization=None):
	"""
	Return the M-Pesa settings for an organization for the admin form. Secret
	fields are NOT returned in plaintext — only a boolean '<field>_set' flag so
	the UI can show 'configured' without exposing the value.
	"""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	name = frappe.db.get_value("Mpesa Settings", {"organization": organization}, "name")
	out = {"organization": organization, "exists": bool(name), "environment": "sandbox", "enabled": 1}
	if name:
		doc = frappe.get_doc("Mpesa Settings", name)
		for f in _MPESA_PLAIN_FIELDS:
			out[f] = doc.get(f)
		for f in _MPESA_SECRET_FIELDS:
			out[f + "_set"] = bool(doc.get(f))
	else:
		for f in _MPESA_SECRET_FIELDS:
			out[f + "_set"] = False
	return out


@frappe.whitelist()
@envelope
def set_mpesa_settings(organization=None, **kwargs):
	"""
	Create/update the M-Pesa settings for an organization. Password fields are
	only written when a non-empty value is supplied (blank = keep existing).
	"""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	name = frappe.db.get_value("Mpesa Settings", {"organization": organization}, "name")
	if name:
		doc = frappe.get_doc("Mpesa Settings", name)
	else:
		doc = frappe.get_doc({"doctype": "Mpesa Settings", "organization": organization})

	for f in _MPESA_PLAIN_FIELDS:
		if f in kwargs and kwargs[f] not in (None,):
			doc.set(f, kwargs[f])
	# Secrets: only overwrite when a fresh value is provided.
	for f in _MPESA_SECRET_FIELDS:
		val = kwargs.get(f)
		if val not in (None, ""):
			doc.set(f, val)

	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True) if name else doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return get_mpesa_settings(organization)


# --------------------------------------------------------------------------
# Messaging (SMS / Email / WhatsApp) credentials — per organization
# --------------------------------------------------------------------------

_MSG_PLAIN_FIELDS = [
	"sms_enabled", "sms_provider", "sms_sender_id", "sms_api_key", "sms_base_url",
	"email_enabled", "email_from_name", "email_from_address",
	"smtp_host", "smtp_port", "smtp_use_tls", "smtp_username",
	"whatsapp_enabled", "whatsapp_provider", "whatsapp_phone_number_id", "whatsapp_base_url",
]
# Secrets are never returned in plaintext; only a '<field>_set' flag is reported.
_MSG_SECRET_FIELDS = ["sms_api_secret", "smtp_password", "whatsapp_access_token"]


def _messaging_settings_payload(organization):
	"""Masked messaging settings dict (secrets reported only as '<field>_set')."""
	name = frappe.db.get_value("Messaging Settings", {"organization": organization}, "name")
	out = {"organization": organization, "exists": bool(name)}
	if name:
		doc = frappe.get_doc("Messaging Settings", name)
		for f in _MSG_PLAIN_FIELDS:
			out[f] = doc.get(f)
		for f in _MSG_SECRET_FIELDS:
			out[f + "_set"] = bool(doc.get(f))
	else:
		out.update({
			"sms_enabled": 0, "sms_provider": "Africa's Talking",
			"email_enabled": 0, "smtp_port": 587, "smtp_use_tls": 1,
			"whatsapp_enabled": 0, "whatsapp_provider": "Meta Cloud API",
		})
		for f in _MSG_SECRET_FIELDS:
			out[f + "_set"] = False
	return out


@frappe.whitelist()
@envelope
def get_messaging_settings(organization=None):
	"""
	Return per-organization SMS/Email/WhatsApp settings for the admin form.
	Secret fields are NOT returned in plaintext — only a boolean '<field>_set' flag.
	"""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")
	return _messaging_settings_payload(organization)


@frappe.whitelist()
@envelope
def set_messaging_settings(organization=None, **kwargs):
	"""
	Create/update per-organization messaging credentials. Password fields are only
	written when a non-empty value is supplied (blank = keep existing).
	"""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	name = frappe.db.get_value("Messaging Settings", {"organization": organization}, "name")
	if name:
		doc = frappe.get_doc("Messaging Settings", name)
	else:
		doc = frappe.get_doc({"doctype": "Messaging Settings", "organization": organization})

	for f in _MSG_PLAIN_FIELDS:
		if f in kwargs and kwargs[f] is not None:
			doc.set(f, kwargs[f])
	for f in _MSG_SECRET_FIELDS:
		val = kwargs.get(f)
		if val not in (None, ""):
			doc.set(f, val)

	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True) if name else doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return _messaging_settings_payload(organization)


@frappe.whitelist()
@envelope
def set_property_billing_settings(property, **kwargs):
	"""Update a single property's billing/charge overrides."""
	if not property or not frappe.db.exists("Property", property):
		raise frappe.ValidationError("Property not found")

	doc = frappe.get_doc("Property", property)
	for k in _PROPERTY_FIELDS:
		if k in kwargs:
			val = kwargs[k]
			doc.set(k, val if val not in ("",) else None)
	doc.save(ignore_permissions=True)

	return _effective_settings(property=property)
