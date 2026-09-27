# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Scheduled background tasks.

daily():
  - generate the month's invoices on the 1st (safety net for the monthly hook)
  - send a pre-due reminder 3 DAYS and 1 DAY before each invoice's due date
  - send an overdue reminder for unpaid invoices past due (day 1, 7, 14)
  - apply late fees to overdue, unpaid rent invoices

Reminders use WhatsApp with interactive "Pay Now" buttons that trigger M-Pesa
STK push when tapped. Also sends SMS + Email as fallbacks.
"""

import frappe
from frappe.utils import add_days, flt, getdate, nowdate, date_diff


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
# Reminders (WhatsApp with Pay buttons + SMS/Email fallbacks)
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
	"""Best-effort send over SMS + WhatsApp (plain text) + Email; never raises."""
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


def _send_whatsapp_with_pay_button(tenant_name, phone, invoice, amount, due_date, 
                                   organization, header, is_overdue=False, days_overdue=0):
	"""
	Send a WhatsApp message with Pay Now button.
	Falls back to plain text if button message fails.
	"""
	from property_management.api import whatsapp
	
	try:
		if is_overdue:
			# Use overdue notice format
			body = (
				f"Hello {tenant_name},\n\n"
				f"⚠️ Your rent payment is {days_overdue} days overdue.\n\n"
				f"💰 Outstanding: KES {flt(amount):,.0f}\n\n"
				f"Please pay immediately to avoid late fees.\n\n"
				f"Tap 'Pay Now' to pay via M-Pesa."
			)
		else:
			# Use pre-due reminder format
			body = (
				f"Hello {tenant_name},\n\n"
				f"This is a reminder that your rent payment is due soon.\n\n"
				f"💰 Amount: KES {flt(amount):,.0f}\n"
				f"📅 Due Date: {due_date}\n\n"
				f"Tap 'Pay Now' to pay instantly via M-Pesa."
			)
		
		buttons = [
			{"id": f"pay_stk_{invoice}", "title": "Pay Now"},
			{"id": "contact_support", "title": "Contact Support"}
		]
		
		whatsapp.send_interactive_buttons(
			phone=phone,
			header_text=header,
			body_text=body,
			buttons=buttons,
			organization=organization,
			footer_text="Nest Property Management",
			ref_doctype="Sales Invoice",
			ref_name=invoice
		)
		return True
	except Exception as e:
		frappe.log_error(title="WhatsApp pay button failed", message=frappe.get_traceback())
		# Fallback to plain text
		try:
			link = _pay_link(invoice, organization)
			fallback_msg = (
				f"Hello {tenant_name}, your rent of KES {flt(amount):,.0f} "
				f"{'is overdue' if is_overdue else f'is due on {due_date}'}. "
				f"Pay now: {link}"
			)
			whatsapp.send_text_message(phone, fallback_msg, organization)
			return True
		except Exception:
			frappe.log_error(title="WhatsApp fallback failed", message=frappe.get_traceback())
			return False


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
	Remind tenants before the due date:
	- 3 days before: First reminder with Pay Now button
	- 1 day before: Final reminder with Pay Now button
	
	Uses WhatsApp with interactive buttons for instant M-Pesa payment.
	"""
	today = getdate(as_of or nowdate())
	sent_count = 0
	
	# Reminder days before due date
	reminder_days = [3, 1]
	
	for days_before in reminder_days:
		target = add_days(today, days_before)  # invoices due in X days
		invoices = _unpaid_monthly_invoices({"due_date": str(target)})
		
		for si in invoices:
			org = _org_of(si)
			c = _tenant_contact(si.customer)
			
			if not c.get("phone") and not c.get("email"):
				continue
			
			# Send WhatsApp with Pay button
			if c.get("phone"):
				header = "📅 Rent Reminder" if days_before == 3 else "⏰ Final Reminder"
				_send_whatsapp_with_pay_button(
					tenant_name=si.customer,
					phone=c.get("phone"),
					invoice=si.name,
					amount=si.outstanding_amount,
					due_date=si.due_date,
					organization=org,
					header=header,
					is_overdue=False
				)
			
			# Also send email as backup
			if c.get("email"):
				link = _pay_link(si.name, org)
				email_msg = (
					f"Hello {si.customer},\n\n"
					f"Your rent invoice {si.name} of KSh {flt(si.outstanding_amount):,.0f} "
					f"is due on {si.due_date}.\n\n"
					f"Pay now: {link}\n\n"
					f"Best regards,\nNest Property Management"
				)
				try:
					from property_management.api import messaging
					subject = f"Rent Due in {days_before} Day{'s' if days_before > 1 else ''}"
					messaging._dispatch_email(c.get("email"), email_msg, subject, org)
				except Exception:
					frappe.log_error(title="reminder email failed", message=frappe.get_traceback())
			
			sent_count += 1
	
	frappe.logger().info(f"Pre-due reminders sent: {sent_count}")
	return {"reminders": sent_count}


def send_overdue_reminders(as_of=None):
	"""
	Remind tenants whose invoices are past due:
	- Day 1 overdue: First overdue notice
	- Day 7 overdue: Second notice
	- Day 14 overdue: Urgent notice
	
	Uses WhatsApp with Pay Now button for instant M-Pesa payment.
	"""
	today = getdate(as_of or nowdate())
	invoices = _unpaid_monthly_invoices({"due_date": ["<", str(today)]})
	sent_count = 0
	
	# Only send on specific overdue days to avoid spamming
	reminder_days = [1, 7, 14]
	
	for si in invoices:
		org = _org_of(si)
		c = _tenant_contact(si.customer)
		
		if not c.get("phone") and not c.get("email"):
			continue
		
		days_overdue = date_diff(today, getdate(si.due_date)) if si.due_date else 0
		
		# Only send reminder on specific days (1, 7, 14 days overdue)
		if days_overdue not in reminder_days:
			continue
		
		# Send WhatsApp with Pay button
		if c.get("phone"):
			if days_overdue == 1:
				header = "⚠️ Payment Overdue"
			elif days_overdue == 7:
				header = "🚨 Urgent: Payment Overdue"
			else:
				header = "❗ Final Notice: Payment Overdue"
			
			_send_whatsapp_with_pay_button(
				tenant_name=si.customer,
				phone=c.get("phone"),
				invoice=si.name,
				amount=si.outstanding_amount,
				due_date=si.due_date,
				organization=org,
				header=header,
				is_overdue=True,
				days_overdue=days_overdue
			)
		
		# Also send email
		if c.get("email"):
			link = _pay_link(si.name, org)
			email_msg = (
				f"Hello {si.customer},\n\n"
				f"Your rent invoice {si.name} of KSh {flt(si.outstanding_amount):,.0f} "
				f"is {days_overdue} day(s) overdue.\n\n"
				f"Please pay immediately to avoid late fees and service disruption.\n\n"
				f"Pay now: {link}\n\n"
				f"Best regards,\nNest Property Management"
			)
			try:
				from property_management.api import messaging
				messaging._dispatch_email(c.get("email"), email_msg, "Overdue Rent Payment", org)
			except Exception:
				frappe.log_error(title="reminder email failed", message=frappe.get_traceback())
		
		sent_count += 1
	
	frappe.logger().info(f"Overdue reminders sent: {sent_count}")
	return {"reminders": sent_count}


# --------------------------------------------------------------------------
# Manual trigger endpoints (for testing)
# --------------------------------------------------------------------------

@frappe.whitelist()
def trigger_predue_reminders():
	"""Manually trigger pre-due reminders (for testing)."""
	return send_predue_reminders()


@frappe.whitelist()
def trigger_overdue_reminders():
	"""Manually trigger overdue reminders (for testing)."""
	return send_overdue_reminders()
