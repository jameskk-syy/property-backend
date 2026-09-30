# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Monthly billing.

For each active lease, generate TWO separate Sales Invoices per month:
  - Rent invoice     (PM-MONTHLY): rent from the lease. Reminders + late fees
                     apply to this invoice only.
  - Utility invoice  (PM-UTILITY): garbage (flat effective charge, if configured)
                     + water/electricity (metered consumption during the month).
                     No late fees apply to utilities.

Both invoices' due dates are derived from the property's rent settings
(`rent_due_day` + `late_fee_grace_days`). No utility charges are raised at
onboarding; utilities only appear on the utility invoice once metered/configured.
"""

import contextlib

import frappe
from frappe.utils import flt, getdate, nowdate, get_first_day, get_last_day, add_days, getdate as _gd


@contextlib.contextmanager
def _as_system_user():
	original = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(original)


def _period_bounds(period):
	"""period = 'YYYY-MM' or a date; returns (start_date, end_date) of that month."""
	if period and len(str(period)) == 7:
		ref = getdate(str(period) + "-01")
	elif period:
		ref = getdate(period)
	else:
		ref = getdate(nowdate())
	return get_first_day(ref), get_last_day(ref)


def _due_date(property_name, organization, end_date):
	"""
	Compute the invoice due date from settings: `rent_due_day` of the month
	FOLLOWING the billing period, plus grace days. Invoices post on the last day of
	the billing period, so the due date must land in the next month (never before
	the posting date, which ERPNext rejects). Falls back to end_of_period + 5.
	"""
	from property_management.api.settings import _effective_settings
	s = _effective_settings(property=property_name, organization=organization)
	due_day = int(s.get("rent_due_day") or 5)
	grace = int(s.get("late_fee_grace_days") or 0)
	# Posting date is the period end; due date is rent_due_day of the NEXT month.
	base = getdate(end_date)
	first_of_next_month = add_days(base, 1)  # end-of-month + 1 day = 1st of next month
	try:
		due = first_of_next_month.replace(day=min(due_day, 28))
	except Exception:
		due = add_days(base, 5)
	if grace:
		due = add_days(due, grace)
	# Safety net: never allow a due date before the posting date.
	if getdate(due) < base:
		due = add_days(base, 5)
	return due


def _metered_amount(unit, utility_type, start, end):
	"""Sum billing_amount of meter readings for a unit+utility within the period."""
	rows = frappe.get_all(
		"Meter Reading",
		filters={
			"unit": unit,
			"utility_type": utility_type,
			"reading_date": ["between", [str(start), str(end)]],
		},
		fields=["sum(billing_amount) as amt"],
	)
	return flt(rows[0].amt) if rows and rows[0].amt else 0.0


def build_invoice_lines(lease, start, end, settings):
	"""
	Return ALL invoice line items for a lease for the period (rent + utilities).

	Kept for backward compatibility. Monthly billing now bills rent and utilities
	on separate invoices via build_rent_lines() and build_utility_lines().
	"""
	return build_rent_lines(lease, start, end, settings) + build_utility_lines(lease, start, end, settings)


def build_rent_lines(lease, start, end, settings):
	"""Return the rent-only invoice line items for a lease for the period."""
	lines = []
	if flt(lease.rent_amount) > 0:
		lines.append({"item_name": "Rent", "description": f"Monthly Rent ({start} to {end})",
					  "quantity": 1, "rate": flt(lease.rent_amount)})
	return lines


def build_utility_lines(lease, start, end, settings):
	"""
	Return the utility invoice line items for a lease for the period:
	garbage (flat) + metered water/electricity. No utility line is added unless it
	is configured (garbage) or has been metered (water/electricity) for the month.
	"""
	lines = []
	# Garbage (flat effective charge)
	garbage = flt(settings.get("garbage_charge"))
	if garbage > 0:
		lines.append({"item_name": "Garbage", "description": "Garbage / refuse collection",
					  "quantity": 1, "rate": garbage})
	# Metered utilities for the month.
	for utility in ("Water", "Electricity"):
		amt = _metered_amount(lease.unit, utility, start, end)
		if amt > 0:
			lines.append({"item_name": utility, "description": f"{utility} consumption ({start} to {end})",
						  "quantity": 1, "rate": amt})
	return lines


@frappe.whitelist()
def generate_monthly_invoices(period=None, organization=None, property=None):
	"""
	Generate monthly invoices for every active lease in scope. Rent and utilities
	are billed SEPARATELY:
	  - Rent      -> its own Sales Invoice, tagged `PM-MONTHLY:` (reminders + late
	                 fees apply to this invoice).
	  - Utilities -> a second Sales Invoice combining garbage + water + electricity,
	                 tagged `PM-UTILITY:` (no late fees; utilities only appear once
	                 configured/metered for the month).

	Returns a summary. Idempotent per (lease, period, kind): skips a lease's rent or
	utility invoice if one already exists for the month (tracked via remarks tags).
	"""
	start, end = _period_bounds(period)
	rent_tag = f"PM-MONTHLY:{start}"
	utility_tag = f"PM-UTILITY:{start}"

	lease_filters = {"status": "Active"}
	if organization:
		lease_filters["organization"] = organization
	if property:
		lease_filters["property"] = property

	leases = frappe.get_all(
		"Lease Agreement", filters=lease_filters,
		fields=["name", "organization", "property", "unit", "tenant", "rent_amount"],
	)

	from property_management.integration.invoicing import create_sales_invoice
	from property_management.api.settings import _effective_settings

	def _already_invoiced(lease_name, prop, tag):
		return bool(frappe.db.exists("Sales Invoice", {
			"property_ref": prop,
			"remarks": ["like", f"%{tag}:{lease_name}%"],
			"docstatus": ["<", 2],
		}))

	created, skipped, failed = [], [], []
	with _as_system_user():
		for lease in leases:
			lease = frappe._dict(lease)
			try:
				settings = _effective_settings(property=lease.property, organization=lease.organization)
				due = _due_date(lease.property, lease.organization, end)
				made_any = False

				# 1. Rent invoice (PM-MONTHLY): reminders + late fees apply here.
				rent_lines = build_rent_lines(lease, start, end, settings)
				if rent_lines and not _already_invoiced(lease.name, lease.property, rent_tag):
					rent_si = create_sales_invoice(
						property_name=lease.property, tenant=lease.tenant, items=rent_lines,
						invoice_type="Rent", posting_date=str(end), due_date=str(due), submit=True,
					)
					frappe.db.set_value("Sales Invoice", rent_si, "remarks",
										f"{rent_tag}:{lease.name}", update_modified=False)
					created.append({"lease": lease.name, "kind": "rent",
									"sales_invoice": rent_si, "due_date": str(due)})
					made_any = True

				# 2. Utilities invoice (PM-UTILITY): garbage + water + electricity. No late fees.
				utility_lines = build_utility_lines(lease, start, end, settings)
				if utility_lines and not _already_invoiced(lease.name, lease.property, utility_tag):
					util_si = create_sales_invoice(
						property_name=lease.property, tenant=lease.tenant, items=utility_lines,
						invoice_type="Utility", posting_date=str(end), due_date=str(due), submit=True,
					)
					frappe.db.set_value("Sales Invoice", util_si, "remarks",
										f"{utility_tag}:{lease.name}", update_modified=False)
					created.append({"lease": lease.name, "kind": "utility",
									"sales_invoice": util_si, "due_date": str(due)})
					made_any = True

				if not made_any:
					skipped.append(lease.name)
			except Exception as e:
				failed.append({"lease": lease.name, "error": str(e)})
				frappe.log_error(title="monthly invoice failed", message=frappe.get_traceback())

	frappe.db.commit()
	return {
		"period": f"{start} to {end}",
		"created": created,
		"skipped": skipped,
		"failed": failed,
		"total_leases": len(leases),
	}


def _late_fee_exists(original_si, tier):
	"""True if a late-fee invoice for this (original invoice, tier) already exists."""
	tag = f"PM-LATEFEE:{original_si}:{tier}"
	return bool(frappe.db.exists("Sales Invoice", {
		"remarks": ["like", f"%{tag}%"], "docstatus": ["<", 2],
	}))


@frappe.whitelist()
def apply_late_fees(as_of=None):
	"""
	Charge late fees on overdue, unpaid rent invoices — automatically (daily
	scheduler). Each fee is a SEPARATE Sales Invoice that is clearly LINKED back to
	the original rent invoice + tenant via a remarks tag `PM-LATEFEE:{orig}:{tier}`,
	so you always know which month's rent + which tenant a fee is for.

	Two tiers, driven by settings:
	  - tier 1 (`late_fee_amount`)      once past due_date + late_fee_grace_days
	  - tier 2 (`second_penalty_amount`) once `second_penalty_days` past the due day

	Idempotent: each (invoice, tier) fee is charged at most once.
	"""
	from property_management.integration.invoicing import create_sales_invoice
	from property_management.api.settings import _effective_settings

	today = getdate(as_of or nowdate())

	# Original monthly rent invoices that are submitted, overdue and still unpaid.
	rent_invoices = frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"outstanding_amount": [">", 0],
			"due_date": ["<", str(today)],
			"remarks": ["like", "%PM-MONTHLY:%"],
		},
		fields=["name", "customer", "due_date", "property_ref", "posting_date"],
	)

	charged, skipped = [], []
	with _as_system_user():
		for si in rent_invoices:
			prop = si.get("property_ref")
			if not prop:
				skipped.append({"invoice": si.name, "reason": "no property_ref"})
				continue
			organization = frappe.db.get_value("Property", prop, "organization")
			settings = _effective_settings(property=prop, organization=organization)

			# Resolve the tenant docname from the customer (customer_name == tenant_name).
			tenant = frappe.db.get_value("Property Tenant", {"tenant_name": si.customer}, "name")
			if not tenant:
				skipped.append({"invoice": si.name, "reason": "tenant not resolved"})
				continue

			due = getdate(si.due_date)
			grace = int(settings.get("late_fee_grace_days") or 0)
			days_over = (today - due).days

			# Tier 1: any amount past due + grace.
			tier1_amt = flt(settings.get("late_fee_amount"))
			if days_over >= grace and tier1_amt > 0 and not _late_fee_exists(si.name, 1):
				try:
					fee_si = create_sales_invoice(
						property_name=prop, tenant=tenant,
						items=[{"item_name": "Late Fee",
								"description": f"Late payment fee for invoice {si.name} (due {si.due_date})",
								"quantity": 1, "rate": tier1_amt}],
						invoice_type="Rent", posting_date=str(today), due_date=str(today), submit=True,
					)
					frappe.db.set_value("Sales Invoice", fee_si, "remarks",
										f"PM-LATEFEE:{si.name}:1", update_modified=False)
					charged.append({"original": si.name, "tier": 1, "late_fee_invoice": fee_si, "amount": tier1_amt})
				except Exception as e:
					frappe.log_error(title="late fee (tier1) failed", message=frappe.get_traceback())
					skipped.append({"invoice": si.name, "tier": 1, "reason": str(e)})

			# Tier 2: second penalty once `second_penalty_days` past the due day.
			tier2_days = int(settings.get("second_penalty_days") or 0)
			tier2_amt = flt(settings.get("second_penalty_amount"))
			if tier2_days and days_over >= tier2_days and tier2_amt > 0 and not _late_fee_exists(si.name, 2):
				try:
					fee_si = create_sales_invoice(
						property_name=prop, tenant=tenant,
						items=[{"item_name": "Second Late Penalty",
								"description": f"Second late penalty for invoice {si.name} (due {si.due_date})",
								"quantity": 1, "rate": tier2_amt}],
						invoice_type="Rent", posting_date=str(today), due_date=str(today), submit=True,
					)
					frappe.db.set_value("Sales Invoice", fee_si, "remarks",
										f"PM-LATEFEE:{si.name}:2", update_modified=False)
					charged.append({"original": si.name, "tier": 2, "late_fee_invoice": fee_si, "amount": tier2_amt})
				except Exception as e:
					frappe.log_error(title="late fee (tier2) failed", message=frappe.get_traceback())
					skipped.append({"invoice": si.name, "tier": 2, "reason": str(e)})

	frappe.db.commit()
	return {"as_of": str(today), "charged": charged, "skipped": skipped, "candidates": len(rent_invoices)}
