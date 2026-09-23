# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

EXCLUDED_DOCTYPES = {"Audit Log", "WhatsApp Message Log", "Sessions", "Activity Log", "DocType", "Custom Field", "Property Setter"}


def capture_audit_log(doc, method=None):
	"""
	Hook handler called on doc_events (after_insert, on_update, on_trash).
	Auto-creates Audit Log records for system tracking.
	"""
	if doc.doctype in EXCLUDED_DOCTYPES or getattr(doc.flags, "ignore_audit_log", False):
		return

	if not frappe.db.exists("DocType", "Audit Log"):
		return

	user = frappe.session.user if getattr(frappe, "session", None) else "Administrator"

	org = getattr(doc, "organization", None)
	if not org and user and frappe.db.has_column("User", "organization"):
		try:
			org = frappe.db.get_value("User", user, "organization")
		except Exception:
			org = None

	action_map = {
		"after_insert": "Created",
		"on_update": "Updated",
		"on_trash": "Deleted"
	}
	action = action_map.get(method, "Updated")

	try:
		log_doc = frappe.get_doc({
			"doctype": "Audit Log",
			"organization": org,
			"user": user,
			"action": action,
			"doctype_name": doc.doctype,
			"document_name": doc.name,
			"timestamp": now_datetime()
		})
		log_doc.flags.ignore_audit_log = True
		log_doc.insert(ignore_permissions=True)
	except Exception:
		pass
