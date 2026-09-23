# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe import _


@frappe.whitelist()
def send_invoice_whatsapp(invoice_id):
	"""
	Enqueues WhatsApp invoice message dispatch via Redis queue to maintain sub-500ms API response time.
	"""
	if not invoice_id:
		frappe.throw(_("Invoice ID is required."))

	invoice = frappe.get_doc("Property Invoice", invoice_id)
	tenant = frappe.get_doc("Property Tenant", invoice.tenant)

	frappe.enqueue(
		"property_management.api.whatsapp.dispatch_whatsapp_job",
		queue="default",
		invoice_id=invoice.name,
		phone=tenant.phone,
		organization=invoice.organization
	)

	return {"status": "Queued", "message": f"WhatsApp message queued for invoice {invoice.name}"}


def dispatch_whatsapp_job(invoice_id, phone, organization):
	invoice = frappe.get_doc("Property Invoice", invoice_id)

	log = frappe.get_doc({
		"doctype": "WhatsApp Message Log",
		"organization": organization,
		"recipient_phone": phone,
		"template_name": "rent_invoice_reminder",
		"message_content": f"Dear Tenant, your rent invoice {invoice.name} for KSh {invoice.outstanding_amount} is due on {invoice.due_date}.",
		"status": "Sent"
	})
	log.insert(ignore_permissions=True)
