# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Messaging API - send free-text reminders/invoices to tenants over SMS, Email
and/or WhatsApp.

Each channel has a whitelisted endpoint, plus a combined `send_reminder` that
fans out to several channels in one call and returns which succeeded/failed.

- Email uses Frappe's built-in mailer (frappe.sendmail).
- SMS uses Frappe's SMS Settings gateway (frappe.core...send_sms) if configured.
- WhatsApp uses the Cloud API via property_management.api.whatsapp module.

All endpoints return the v2 envelope {status, data, message}.
"""

import json

import frappe
from frappe import _

from property_management.api.v2 import envelope
from property_management.api.utils import resolve_organization


def _messaging_doc(organization=None):
	"""The org's Messaging Settings doc, or None if not configured."""
	if not organization:
		try:
			organization = resolve_organization()
		except Exception:
			organization = None
	if not organization:
		return None
	name = frappe.db.get_value("Messaging Settings", {"organization": organization}, "name")
	return frappe.get_doc("Messaging Settings", name) if name else None


def _secret(doc, field):
	"""Decrypt a Password field on a Messaging Settings doc."""
	if not doc:
		return None
	try:
		return doc.get_password(field, raise_exception=False)
	except Exception:
		return None


def _dispatch_sms(phone, message, organization=None):
	"""
	Send an SMS. Uses the org's saved SMS gateway credentials (Messaging Settings)
	when configured, else falls back to Frappe's built-in SMS Settings gateway.
	"""
	if not phone:
		raise frappe.ValidationError("Recipient phone number is required for SMS")

	doc = _messaging_doc(organization)
	if doc and doc.get("sms_enabled") and (doc.get("sms_provider") == "Africa's Talking") \
			and doc.get("sms_api_key") and _secret(doc, "sms_api_secret"):
		import requests
		base = (doc.get("sms_base_url") or "https://api.africastalking.com").rstrip("/")
		resp = requests.post(
			f"{base}/version1/messaging",
			data={"username": doc.get("sms_api_key"), "to": phone, "message": message,
				  "from": doc.get("sms_sender_id") or ""},
			headers={"apiKey": _secret(doc, "sms_api_secret"), "Accept": "application/json",
					 "Content-Type": "application/x-www-form-urlencoded"},
			timeout=20,
		)
		resp.raise_for_status()
		return

	# Fallback: Frappe's configured SMS gateway.
	from frappe.core.doctype.sms_settings.sms_settings import send_sms as _core_send_sms
	_core_send_sms([phone], message)


def _dispatch_email(email, message, subject="Notification from your Property Manager", organization=None):
	if not email:
		raise frappe.ValidationError("Recipient email is required for Email")

	doc = _messaging_doc(organization)
	sender = None
	if doc and doc.get("email_enabled") and doc.get("email_from_address"):
		from_name = doc.get("email_from_name") or ""
		sender = f"{from_name} <{doc.get('email_from_address')}>" if from_name else doc.get("email_from_address")

	frappe.sendmail(
		recipients=[email],
		sender=sender,
		subject=subject,
		message=frappe.utils.md_to_html(message) if hasattr(frappe.utils, "md_to_html") else message,
		now=False,  # queue it
	)


def _dispatch_whatsapp(phone, message, organization):
	"""
	Send a WhatsApp text message via the Cloud API.
	Uses the centralized whatsapp module for actual sending and logging.
	"""
	if not phone:
		raise frappe.ValidationError("Recipient phone number is required for WhatsApp")

	from property_management.api.whatsapp import send_text_message
	send_text_message(phone, message, organization)


def _dispatch_whatsapp_with_buttons(phone, message, buttons, organization, header=None, footer=None,
                                    ref_doctype=None, ref_name=None):
	"""
	Send a WhatsApp interactive button message.
	
	Args:
		phone: Recipient phone number
		message: Main message body
		buttons: List of button dicts [{"id": "btn_id", "title": "Button Text"}]
		organization: Organization name
		header: Optional header text
		footer: Optional footer text
		ref_doctype: Reference document type for tracking
		ref_name: Reference document name
	"""
	if not phone:
		raise frappe.ValidationError("Recipient phone number is required for WhatsApp")

	from property_management.api.whatsapp import send_interactive_buttons
	send_interactive_buttons(
		phone=phone,
		header_text=header,
		body_text=message,
		buttons=buttons,
		organization=organization,
		footer_text=footer,
		ref_doctype=ref_doctype,
		ref_name=ref_name
	)


# --------------------------------------------------------------------------
# Single-channel endpoints
# --------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def send_sms(message, phone=None, tenant=None, organization=None, **kwargs):
	organization = resolve_organization(explicit=organization)
	_dispatch_sms(phone, message, organization)
	return {"channel": "sms", "phone": phone}


@frappe.whitelist()
@envelope
def send_email(message, email=None, tenant=None, subject=None, organization=None, **kwargs):
	organization = resolve_organization(explicit=organization)
	_dispatch_email(email, message, subject or "Notification from your Property Manager", organization)
	return {"channel": "email", "email": email}


@frappe.whitelist()
@envelope
def send_whatsapp(message, phone=None, tenant=None, organization=None, **kwargs):
	organization = resolve_organization(explicit=organization)
	_dispatch_whatsapp(phone, message, organization)
	return {"channel": "whatsapp", "phone": phone}


# --------------------------------------------------------------------------
# Combined multi-channel endpoint
# --------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def send_reminder(message, channels, tenant=None, phone=None, email=None,
                  subject=None, organization=None, **kwargs):
	"""
	Send `message` over each requested channel.

	channels: JSON list or comma string of any of 'sms' | 'email' | 'whatsapp'.
	Returns {sent: [...], failed: [{channel, error}]}.
	"""
	if isinstance(channels, str):
		try:
			channels = frappe.parse_json(channels)
		except Exception:
			channels = [c.strip() for c in channels.split(",") if c.strip()]

	organization = resolve_organization(explicit=organization)
	sent, failed = [], []

	for channel in channels or []:
		try:
			if channel == "sms":
				_dispatch_sms(phone, message, organization)
			elif channel == "email":
				_dispatch_email(email, message, subject or "Notification from your Property Manager", organization)
			elif channel == "whatsapp":
				_dispatch_whatsapp(phone, message, organization)
			else:
				raise frappe.ValidationError(_("Unknown channel: {0}").format(channel))
			sent.append(channel)
		except Exception as e:
			failed.append({"channel": channel, "error": str(e)})

	return {"sent": sent, "failed": failed}


# --------------------------------------------------------------------------
# WhatsApp with Pay Button endpoints
# --------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def send_whatsapp_with_pay_button(message, phone=None, invoice=None, tenant=None,
                                   header=None, footer=None, organization=None, **kwargs):
	"""
	Send a WhatsApp message with a "Pay Now" button linked to an invoice.
	
	Args:
		message: Main message body
		phone: Recipient phone (or resolved from tenant)
		invoice: Invoice ID (Property Invoice or Sales Invoice)
		tenant: Tenant name (to look up phone if not provided)
		header: Optional header text
		footer: Optional footer text (default: "Nest Property Management")
		organization: Organization name
	
	Returns:
		{channel: "whatsapp", phone, invoice, buttons}
	"""
	organization = resolve_organization(explicit=organization)
	
	# Resolve phone from tenant if not provided
	if not phone and tenant:
		phone = frappe.db.get_value("Property Tenant", tenant, "phone")
	
	if not phone:
		raise frappe.ValidationError("Recipient phone number is required")
	
	if not invoice:
		raise frappe.ValidationError("Invoice ID is required for Pay button")
	
	# Build buttons
	buttons = [
		{"id": f"pay_stk_{invoice}", "title": "Pay Now"},
		{"id": "contact_support", "title": "Contact Support"}
	]
	
	# Determine reference doctype
	ref_doctype = "Sales Invoice" if frappe.db.exists("Sales Invoice", invoice) else "Property Invoice"
	
	_dispatch_whatsapp_with_buttons(
		phone=phone,
		message=message,
		buttons=buttons,
		organization=organization,
		header=header,
		footer=footer or "Nest Property Management",
		ref_doctype=ref_doctype,
		ref_name=invoice
	)
	
	return {"channel": "whatsapp", "phone": phone, "invoice": invoice, "buttons": buttons}


@frappe.whitelist()
@envelope
def send_rent_reminder_message(tenant, invoice=None, organization=None, **kwargs):
	"""
	Send a complete rent reminder to a tenant via WhatsApp with Pay Now button.
	
	This is a high-level function that composes the message, looks up invoice details,
	and sends via WhatsApp with interactive buttons.
	
	Args:
		tenant: Property Tenant name
		invoice: Property Invoice or Sales Invoice name (optional, will find latest if not provided)
		organization: Organization name
	
	Returns:
		{channel: "whatsapp", phone, invoice, status}
	"""
	organization = resolve_organization(explicit=organization)
	
	# Get tenant details
	tenant_doc = frappe.get_doc("Property Tenant", tenant)
	phone = tenant_doc.get("phone")
	
	if not phone:
		raise frappe.ValidationError(f"No phone number for tenant {tenant}")
	
	# Find invoice if not provided
	if not invoice:
		# Look for latest unpaid invoice
		invoices = frappe.get_all(
			"Sales Invoice",
			filters={"customer": tenant_doc.tenant_name, "outstanding_amount": [">", 0], "docstatus": 1},
			fields=["name"],
			order_by="due_date asc",
			limit=1
		)
		if invoices:
			invoice = invoices[0].name
		else:
			# Try Property Invoice
			invoices = frappe.get_all(
				"Property Invoice",
				filters={"tenant": tenant, "outstanding_amount": [">", 0]},
				fields=["name"],
				order_by="due_date asc",
				limit=1
			)
			if invoices:
				invoice = invoices[0].name
	
	if not invoice:
		raise frappe.ValidationError(f"No outstanding invoice found for tenant {tenant}")
	
	# Use the centralized whatsapp module
	from property_management.api.whatsapp import send_rent_reminder
	result = send_rent_reminder(tenant_doc, invoice, organization)
	
	return {"channel": "whatsapp", "phone": phone, "invoice": invoice, "status": result.get("status", "sent")}


@frappe.whitelist()
@envelope
def send_overdue_reminder_message(tenant, invoice=None, days_overdue=None, organization=None, **kwargs):
	"""
	Send an overdue payment notice to a tenant via WhatsApp with Pay Now button.
	
	Args:
		tenant: Property Tenant name
		invoice: Property Invoice or Sales Invoice name
		days_overdue: Number of days the payment is overdue
		organization: Organization name
	
	Returns:
		{channel: "whatsapp", phone, invoice, days_overdue, status}
	"""
	organization = resolve_organization(explicit=organization)
	
	# Get tenant details
	tenant_doc = frappe.get_doc("Property Tenant", tenant)
	phone = tenant_doc.get("phone")
	
	if not phone:
		raise frappe.ValidationError(f"No phone number for tenant {tenant}")
	
	if not invoice:
		raise frappe.ValidationError("Invoice ID is required for overdue reminder")
	
	# Calculate days overdue if not provided
	if days_overdue is None:
		from frappe.utils import date_diff, nowdate
		if frappe.db.exists("Sales Invoice", invoice):
			due_date = frappe.db.get_value("Sales Invoice", invoice, "due_date")
		else:
			due_date = frappe.db.get_value("Property Invoice", invoice, "due_date")
		days_overdue = date_diff(nowdate(), due_date) if due_date else 0
	
	# Use the centralized whatsapp module
	from property_management.api.whatsapp import send_overdue_notice
	result = send_overdue_notice(tenant_doc, invoice, days_overdue, organization)
	
	return {
		"channel": "whatsapp",
		"phone": phone,
		"invoice": invoice,
		"days_overdue": days_overdue,
		"status": result.get("status", "sent")
	}
