# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Landlord self-service API.

Every endpoint resolves the CURRENTLY LOGGED-IN user to their Landlord record and
returns ONLY data for the properties that landlord owns — their portfolio,
tenants/billing, financial payout reports (optionally filtered to one property),
and the signed lease agreements of tenants on their properties. A landlord can
never see another landlord's data.
"""

import frappe
from frappe.utils import flt, getdate, nowdate, get_first_day, get_last_day, add_months, formatdate


def _current_landlord():
	"""Resolve the logged-in user to their Landlord docname (by user link, then email)."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Please sign in.", frappe.PermissionError)
	name = frappe.db.get_value("Landlord", {"user": user}, "name")
	if not name:
		name = frappe.db.get_value("Landlord", {"email": user}, "name")
	if not name:
		frappe.throw("No landlord profile is linked to your account.", frappe.PermissionError)
	return name


def _landlord_property_names(landlord):
	return frappe.get_all("Property", filters={"landlord": landlord}, pluck="name")


def _assert_owns(landlord, property_name):
	"""Guard: raise unless `property_name` belongs to this landlord."""
	if not property_name:
		return
	if frappe.db.get_value("Property", property_name, "landlord") != landlord:
		frappe.throw("You can only view your own properties.", frappe.PermissionError)


def _scope_properties(landlord, property_filter=None):
	"""
	The property names in scope for a report: a single property (validated as the
	landlord's) or all of the landlord's properties.
	"""
	if property_filter:
		_assert_owns(landlord, property_filter)
		return [property_filter]
	return _landlord_property_names(landlord)


# --------------------------------------------------------------------------
# Portfolio
# --------------------------------------------------------------------------

@frappe.whitelist()
def my_properties():
	"""The landlord's properties with unit counts + caretaker."""
	landlord = _current_landlord()
	props = frappe.get_all(
		"Property", filters={"landlord": landlord},
		fields=["name", "property_name", "address", "caretaker", "status", "cost_center"],
		order_by="creation desc",
	)
	# Unit counts grouped once.
	prop_names = [p.name for p in props]
	counts = {}
	if prop_names:
		for row in frappe.get_all(
			"Property Unit", filters={"property": ["in", prop_names]},
			fields=["property", "status", "count(name) as n"], group_by="property,status",
		):
			d = counts.setdefault(row["property"], {"total": 0, "Occupied": 0, "Vacant": 0})
			d["total"] += row["n"]
			if row["status"] in ("Occupied", "Vacant"):
				d[row["status"]] += row["n"]

	out = []
	for p in props:
		c = counts.get(p.name, {"total": 0, "Occupied": 0, "Vacant": 0})
		caretaker_name = frappe.db.get_value("Caretaker", p.caretaker, "caretaker_name") if p.caretaker else ""
		out.append({
			"id": p.name,
			"name": p.property_name or p.name,
			"location": p.address or "",
			"caretaker": caretaker_name or "—",
			"status": p.status or "Active",
			"units": c["total"],
			"occupied": c["Occupied"],
			"vacant": c["Vacant"],
		})
	return out


@frappe.whitelist()
def my_property_options():
	"""Lightweight list for the report property-filter dropdown."""
	landlord = _current_landlord()
	props = frappe.get_all("Property", filters={"landlord": landlord},
						   fields=["name", "property_name"], order_by="property_name asc")
	return [{"id": p.name, "name": p.property_name or p.name} for p in props]


@frappe.whitelist()
def my_property_detail(property=None):
	"""
	Full detail for ONE of the landlord's properties: master fields, unit counts,
	uploaded images (URLs + cover) and the units list. Ownership is enforced so a
	landlord can only ever open their own property.
	"""
	landlord = _current_landlord()
	if not property or not frappe.db.exists("Property", property):
		frappe.throw("Property not found")
	_assert_owns(landlord, property)

	p = frappe.get_doc("Property", property)

	# Unit counts + the unit rows themselves (for the detail table).
	unit_rows = frappe.get_all(
		"Property Unit", filters={"property": property},
		fields=["name", "unit_number", "unit_type", "floor", "base_rent", "status"],
		order_by="unit_number asc",
	)
	total = len(unit_rows)
	occupied = sum(1 for u in unit_rows if u.status == "Occupied")
	vacant = sum(1 for u in unit_rows if u.status == "Vacant")
	units = [{
		"id": u.name,
		"number": u.unit_number or u.name,
		"type": u.unit_type or "1 Bedroom",
		"floor": u.floor or "Ground",
		"rent": flt(u.base_rent),
		"status": u.status or "Vacant",
	} for u in unit_rows]

	# Images from the child table (cover first).
	images = []
	for img in (p.get("images") or []):
		if img.image:
			images.append({"url": img.image, "caption": img.caption or "", "is_cover": bool(img.is_cover)})
	images.sort(key=lambda x: 0 if x["is_cover"] else 1)
	cover = p.get("cover_image") or (images[0]["url"] if images else None)

	caretaker_name = frappe.db.get_value("Caretaker", p.caretaker, "caretaker_name") if p.caretaker else None

	return {
		"id": p.name,
		"name": p.property_name or p.name,
		"property_code": p.property_code,
		"type": p.property_type or "Residential",
		"status": p.status or "Active",
		"location": p.address or "",
		"county": p.county or "",
		"sub_county": p.sub_county or "",
		"year_built": p.year_built or "",
		"floors": p.floors or "",
		"description": p.description or "",
		"amenities": p.amenities or "",
		"caretaker": caretaker_name or "—",
		"units": total,
		"occupied": occupied,
		"vacant": vacant,
		"cover_image": cover,
		"images": [i["url"] for i in images],
		"unit_list": units,
	}


# --------------------------------------------------------------------------
# GL helpers scoped to a set of property cost centers
# --------------------------------------------------------------------------

def _cost_centers(prop_names):
	ccs = []
	for p in prop_names:
		cc = frappe.db.get_value("Property", p, "cost_center")
		if cc:
			ccs.append(cc)
	return ccs


def _gl_sum(root_type, cost_centers, from_date=None, to_date=None):
	"""Signed GL total for a root_type across the given cost centers."""
	if not cost_centers:
		return 0.0
	accounts = frappe.get_all("Account", filters={"root_type": root_type, "is_group": 0}, pluck="name")
	if not accounts:
		return 0.0
	filters = {"account": ["in", accounts], "cost_center": ["in", cost_centers], "is_cancelled": 0}
	if from_date and to_date:
		filters["posting_date"] = ["between", [str(from_date), str(to_date)]]
	rows = frappe.get_all("GL Entry", filters=filters, fields=["sum(debit) as d", "sum(credit) as c"])
	if not rows:
		return 0.0
	d, c = flt(rows[0].d), flt(rows[0].c)
	return (c - d) if root_type == "Income" else (d - c)


# --------------------------------------------------------------------------
# Financials / payout report
# --------------------------------------------------------------------------

@frappe.whitelist()
def my_financials(property=None, commission_rate=10.0):
	"""
	Landlord payout report from the GL, scoped to the landlord's properties (or a
	single chosen property). Net payout = gross rent collected - management
	commission - property expenses. Includes a 6-month payout trend.
	"""
	landlord = _current_landlord()
	prop_names = _scope_properties(landlord, property)
	ccs = _cost_centers(prop_names)
	rate = flt(commission_rate)

	gross = _gl_sum("Income", ccs)
	expenses = _gl_sum("Expense", ccs)
	commission = gross * (rate / 100.0)
	net = max(0.0, gross - commission - expenses)

	# 6-month rent-collection trend. `revenue` == rent collected (the metric the
	# reports/dashboard chart plot); `gross`/`payout` kept for back-compat.
	today = getdate(nowdate())
	trend = []
	for i in range(5, -1, -1):
		m_ref = add_months(today, -i)
		ms, me = get_first_day(m_ref), get_last_day(m_ref)
		g = _gl_sum("Income", ccs, ms, me)
		e = _gl_sum("Expense", ccs, ms, me)
		payout = max(0.0, g - g * (rate / 100.0) - e)
		trend.append({"month": formatdate(m_ref, "MMM"), "full_month": formatdate(m_ref, "MMM YYYY"),
					  "revenue": g, "gross": g, "expenses": e, "payout": payout})

	# Rent paid by tenant (what the landlord asked to see, not the payout).
	rent_by_tenant = [
		{"tenant": t["tenant"], "property": t["property"], "unit": t["unit"],
		 "rent": t["rent"], "billed": t["billed"], "paid": t["paid"], "outstanding": t["outstanding"]}
		for t in _landlord_tenant_rows(prop_names)
	]

	return {
		"property": property,
		"property_count": len(prop_names),
		"gross_collected": gross,
		"total_income": gross,
		"total_expenses": expenses,
		"commission_rate": rate,
		"management_fee": commission,
		"property_expenses": expenses,
		"net_payout": net,
		"trend": trend,
		"rent_by_tenant": rent_by_tenant,
	}


# --------------------------------------------------------------------------
# Tenants & billing across the landlord's properties
# --------------------------------------------------------------------------

def _tenant_invoice_totals(customer):
	"""Billed / paid / outstanding for a tenant from their submitted Sales Invoices."""
	rows = frappe.get_all(
		"Sales Invoice",
		filters={"docstatus": 1, "customer": customer},
		fields=["sum(grand_total) as billed", "sum(outstanding_amount) as outstanding"],
	)
	billed = flt(rows[0].billed) if rows and rows[0].billed else 0.0
	outstanding = flt(rows[0].outstanding) if rows and rows[0].outstanding else 0.0
	return billed, max(0.0, billed - outstanding), outstanding


def _landlord_tenant_rows(prop_names):
	"""
	One row per active lease on the landlord's properties, enriched with
	billed/paid/outstanding from Sales Invoices. Shared by billing, financial
	reports and the dashboard so the numbers always agree.
	"""
	if not prop_names:
		return []
	leases = frappe.get_all(
		"Lease Agreement", filters={"property": ["in", prop_names], "status": "Active"},
		fields=["name", "tenant", "property", "unit", "rent_amount"],
	)
	rows = []
	for le in leases:
		t = frappe.db.get_value("Property Tenant", le.tenant,
								["tenant_name", "phone", "status"], as_dict=True) or frappe._dict()
		tname = t.get("tenant_name") or le.tenant
		unit_no = frappe.db.get_value("Property Unit", le.unit, "unit_number") or le.unit
		prop_name = frappe.db.get_value("Property", le.property, "property_name") or le.property
		billed, paid, outstanding = _tenant_invoice_totals(tname)
		rows.append({
			"id": le.tenant,
			"tenant": tname,
			"name": tname,
			"phone": t.get("phone") or "",
			"unit": unit_no,
			"property": prop_name,
			"rent": flt(le.rent_amount),
			"billed": billed,
			"paid": paid,
			"outstanding": outstanding,
			"balance": outstanding,
			"status": "Overdue" if outstanding > 0 else (t.get("status") or "Active"),
		})
	return rows


@frappe.whitelist()
def my_billing(property=None):
	"""Tenants on the landlord's properties with rent, billed/paid and balance."""
	landlord = _current_landlord()
	prop_names = _scope_properties(landlord, property)
	tenants = _landlord_tenant_rows(prop_names)
	total_rent = sum(t["rent"] for t in tenants)
	total_out = sum(t["outstanding"] for t in tenants)
	total_paid = sum(t["paid"] for t in tenants)
	return {
		"tenants": tenants,
		"tenant_count": len(tenants),
		"monthly_rent_roll": total_rent,
		"total_rent": total_rent,
		"total_paid": total_paid,
		"total_outstanding": total_out,
	}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@frappe.whitelist()
def my_dashboard():
	"""Landlord landing page: portfolio KPIs + payout trend in one call."""
	landlord = _current_landlord()
	props = my_properties()
	prop_names = [p["id"] for p in props]
	ccs = _cost_centers(prop_names)

	today = getdate(nowdate())
	month_income = _gl_sum("Income", ccs, get_first_day(today), get_last_day(today))

	total_units = sum(p["units"] for p in props)
	occupied = sum(p["occupied"] for p in props)

	fin = my_financials()  # portfolio-wide payout + trend

	# Tenants in arrears across the portfolio (balance > 0).
	arrears = [
		{"id": t["id"], "name": t["name"], "property": t["property"],
		 "unit": t["unit"], "balance": t["outstanding"]}
		for t in _landlord_tenant_rows(prop_names) if t["outstanding"] > 0
	]
	arrears.sort(key=lambda a: a["balance"], reverse=True)

	return {
		"landlord_name": frappe.db.get_value("Landlord", landlord, "landlord_name") or landlord,
		"property_count": len(props),
		"total_units": total_units,
		"occupied_units": occupied,
		"occupancy_rate": round((occupied / total_units * 100) if total_units else 0),
		"revenue_this_month": month_income,
		"net_payout": fin["net_payout"],
		"properties": props,
		"trend": fin["trend"],
		"arrears": arrears,
	}


# --------------------------------------------------------------------------
# Documents — signed leases of tenants on the landlord's properties
# --------------------------------------------------------------------------

@frappe.whitelist()
def my_documents(property=None):
	"""
	Lease agreements for tenants on the landlord's properties. One row per lease
	with tenant + signed status + PDF availability, for a table with view/download.
	"""
	landlord = _current_landlord()
	prop_names = _scope_properties(landlord, property)
	if not prop_names:
		return []

	leases = frappe.get_all(
		"Lease Agreement", filters={"property": ["in", prop_names]},
		fields=["name", "tenant", "property", "unit", "status", "agreement_pdf",
				"signed_on", "tenant_signature", "start_date", "end_date", "creation"],
		order_by="creation desc",
	)
	out = []
	for le in leases:
		t = frappe.db.get_value("Property Tenant", le.tenant, ["tenant_name", "national_id"], as_dict=True) or frappe._dict()
		prop_name = frappe.db.get_value("Property", le.property, "property_name") or le.property
		unit_no = frappe.db.get_value("Property Unit", le.unit, "unit_number") or le.unit
		pdf = le.agreement_pdf or frappe.db.get_value(
			"File", {"attached_to_doctype": "Lease Agreement", "attached_to_name": le.name,
					 "file_name": ["like", "%.pdf"]}, "file_url")
		out.append({
			"lease": le.name,
			"tenant_name": t.get("tenant_name") or le.tenant,
			"national_id": t.get("national_id") or "",
			"property_name": prop_name,
			"unit": unit_no,
			"status": le.status,
			"signed_on": str(le.signed_on)[:19] if le.signed_on else None,
			"is_signed": bool(le.signed_on) or bool((le.tenant_signature or "").strip()),
			"has_lease_pdf": bool(pdf),
		})
	return out


@frappe.whitelist()
def download_lease(lease):
	"""
	Stream a lease PDF, but only if the lease is on one of the landlord's
	properties (ownership enforced).
	"""
	landlord = _current_landlord()
	if not frappe.db.exists("Lease Agreement", lease):
		frappe.throw("Lease not found")
	prop = frappe.db.get_value("Lease Agreement", lease, "property")
	_assert_owns(landlord, prop)

	from property_management.api.documents import _lease_pdf_url
	agreement_pdf = frappe.db.get_value("Lease Agreement", lease, "agreement_pdf")
	file_url = _lease_pdf_url(lease, agreement_pdf)
	if not file_url:
		frappe.throw("No signed lease document is available yet.")

	file_doc = frappe.get_all("File", filters={"file_url": file_url}, fields=["name"], limit=1)
	if file_doc:
		content = frappe.get_doc("File", file_doc[0].name).get_content()
	else:
		import os
		from frappe.utils import get_files_path
		rel = file_url.split("/files/")[-1]
		base = get_files_path(is_private=("/private/" in file_url))
		with open(os.path.join(base, rel), "rb") as fh:
			content = fh.read()

	frappe.local.response.filename = f"Lease-{lease}.pdf"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "pdf"
