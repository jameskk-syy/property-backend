# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Scheduled background tasks.

daily():
  - generate the month's invoices on the 1st (safety net for the monthly hook)
  - send a pre-due reminder ONE DAY before each invoice's due date
  - send an overdue reminder for unpaid invoices past due
  - apply late fees to overdue, unpaid rent invoices

Every reminder carries a "Pay" deep link into the tenant portal
({portal_url}/tenant/invoices?pay=<invoice>) so tapping it opens the invoice and
fires the M-Pesa STK push. Reminders go over SMS + WhatsApp + Email.
"""

import frappe
from frappe.utils import add_days, flt, getdate, nowdate


def daily():
	"""Daily background tasks runner."""
	generate_monthly_invoices_on_first()
	send_predue_reminders()
	send_overdue_reminders()
	apply_late_fees_daily()


def monthly():
	"""Monthly scheduler hook: generate combined invoices for the just-ended month."""
	_run_monthly_billing()


# --------------------------------------------------------------------------
# Monthly billing
# --------------------------------------------------------------------------

def generate_monthly_invoices_on_first():
	"""Safety net: generate the current month's invoices on the 1st (idempotent)."""
	if getdate(nowdate()).day == 1:
		_run_monthly_billing()


def _run_monthly_billing():
	from property_management.integration.billing import generate_monthly_invoices
	try:
		result = generate_monthly_invoices()
		frappe.logger().info(f"Monthly billing: {result}")
	except Exception:
		frappe.log_error(title="monthly billing run failed", message=frappe.get_traceback())


def apply_late_fees_daily():
	from property_management.integration.billing import apply_late_fees
	try:
		result = apply_late_fees()
		frappe.logger().info(f"Late fees: {result}")
	except Exception:
		frappe.log_error(title="late fee run failed", message=frappe.get_traceback())


# --------------------------------------------------------------------------
# Reminders (native Sales Invoices + pay deep link)
# --------------------------------------------------------------------------

def _portal_url(organization):
	url = None
	if organization:
		url = frappe.db.get_value("Organization", organization, "portal_url")
	return (url or "http://localhost:5173").rstrip("/")


def _pay_link(invoice, organization):
	"""Deep link that opens the tenant portal at this invoice and starts payment."""
	return f"{_portal_url(organization)}/tenant/invoices?pay={invoice}"


def _tenant_contact(customer):
	"""Resolve tenant docname, phone and email from an invoice customer name."""
	tname = frappe.db.get_value("Property Tenant", {"tenant_name": customer},
							   ["name", "phone", "email"], as_dict=True)
	return tname or frappe._dict()


def _send_all_channels(message, phone, email, organization, subject):
	"""Best-effort send over SMS + WhatsApp + Email; never raises."""
	from property_management.api import messaging
	sent = []
	if phone:
		try:
			messaging._dispatch_sms(phone, message, organization)
			sent.append("sms")
		except Exception:
			frappe.log_error(title="reminder sms failed", message=frappe.get_traceback())
		try:
			messaging._dispatch_whatsapp(phone, message, organization)
			sent.append("whatsapp")
		except Exception:
			frappe.log_error(title="reminder whatsapp failed", message=frappe.get_traceback())
	if email:
		try:
			messaging._dispatch_email(email, message, subject, organization)
			sent.append("email")
		except Exception:
			frappe.log_error(title="reminder email failed", message=frappe.get_traceback())
	return sent


def _unpaid_monthly_invoices(extra_filters):
	filters = {
		"docstatus": 1,
		"outstanding_amount": [">", 0],
		"remarks": ["like", "%PM-MONTHLY:%"],
	}
	filters.update(extra_filters)
	return frappe.get_all(
		"Sales Invoice", filters=filters,
		fields=["name", "customer", "outstanding_amount", "due_date", "property_ref"],
	)


def _org_of(invoice_row):
	if invoice_row.get("property_ref"):
		return frappe.db.get_value("Property", invoice_row.property_ref, "organization")
	return None


def send_predue_reminders(as_of=None):
	"""
	Remind tenants ONE DAY before the due date (e.g. due on the 5th -> send on the
	4th). Includes the pay link so they can settle immediately.
	"""
	today = getdate(as_of or nowdate())
	target = add_days(today, 1)  # invoices due tomorrow
	invoices = _unpaid_monthly_invoices({"due_date": str(target)})
	sent_count = 0
	for si in invoices:
		org = _org_of(si)
		c = _tenant_contact(si.customer)
		if not (c.get("phone") or c.get("email")):
			continue
		link = _pay_link(si.name, org)
		msg = (f"Hello {si.customer}, your rent invoice {si.name} of "
			   f"KSh {flt(si.outstanding_amount):,.0f} is due tomorrow ({si.due_date}). "
			   f"Pay now: {link}")
		_send_all_channels(msg, c.get("phone"), c.get("email"), org, "Rent Due Tomorrow")
		sent_count += 1
	frappe.logger().info(f"Pre-due reminders sent: {sent_count}")
	return {"target_due_date": str(target), "reminders": sent_count}


def send_overdue_reminders(as_of=None):
	"""Remind tenants whose invoices are past due and still unpaid, with pay link."""
	today = getdate(as_of or nowdate())
	invoices = _unpaid_monthly_invoices({"due_date": ["<", str(today)]})
	sent_count = 0
	for si in invoices:
		org = _org_of(si)
		c = _tenant_contact(si.customer)
		if not (c.get("phone") or c.get("email")):
			continue
		days_over = (today - getdate(si.due_date)).days if si.due_date else 0
		link = _pay_link(si.name, org)
		msg = (f"Hello {si.customer}, your rent invoice {si.name} of "
			   f"KSh {flt(si.outstanding_amount):,.0f} is overdue by {days_over} day(s). "
			   f"Please pay to avoid late fees: {link}")
		_send_all_channels(msg, c.get("phone"), c.get("email"), org, "Overdue Rent")
		sent_count += 1
	frappe.logger().info(f"Overdue reminders sent: {sent_count}")
	return {"reminders": sent_count}
