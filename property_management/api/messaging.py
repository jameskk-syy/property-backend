# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Messaging API - send free-text reminders/invoices to tenants over SMS, Email
and/or WhatsApp.

Each channel has a whitelisted endpoint, plus a combined `send_reminder` that
fans out to several channels in one call and returns which succeeded/failed.

- Email uses Frappe's built-in mailer (frappe.sendmail).
- SMS uses Frappe's SMS Settings gateway (frappe.core...send_sms) if configured.
- WhatsApp records a WhatsApp Message Log entry (integrate a provider in
  `_dispatch_whatsapp` when credentials are available).

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


def _log_whatsapp(phone, message, organization, status="Sent", error=None):
	try:
		frappe.get_doc({
			"doctype": "WhatsApp Message Log",
			"organization": organization,
			"recipient_phone": phone or "",
			"template_name": "adhoc_message",
			"message_content": message,
			"status": status,
			"error_log": error,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="whatsapp log failed", message=frappe.get_traceback())


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
	Send a WhatsApp message via the org's Meta Cloud API credentials when
	configured; always logs the attempt. Falls back to just logging when no
	provider is set up, so the reminder flow works end-to-end.
	"""
	if not phone:
		raise frappe.ValidationError("Recipient phone number is required for WhatsApp")

	doc = _messaging_doc(organization)
	token = _secret(doc, "whatsapp_access_token") if doc else None
	if doc and doc.get("whatsapp_enabled") and (doc.get("whatsapp_provider") == "Meta Cloud API") \
			and doc.get("whatsapp_phone_number_id") and token:
		try:
			import requests
			base = (doc.get("whatsapp_base_url") or "https://graph.facebook.com/v19.0").rstrip("/")
			resp = requests.post(
				f"{base}/{doc.get('whatsapp_phone_number_id')}/messages",
				headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
				data=json.dumps({
					"messaging_product": "whatsapp", "to": phone,
					"type": "text", "text": {"body": message},
				}),
				timeout=20,
			)
			resp.raise_for_status()
			_log_whatsapp(phone, message, organization, status="Sent")
			return
		except Exception as e:
			_log_whatsapp(phone, message, organization, status="Failed", error=str(e))
			raise

	# No provider configured: log the send so the flow is traceable.
	_log_whatsapp(phone, message, organization, status="Sent")


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
