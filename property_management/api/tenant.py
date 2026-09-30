# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Tenant self-service API.

Every endpoint here resolves the CURRENTLY LOGGED-IN user to their Property Tenant
record and returns ONLY that tenant's data — their lease/unit, payment history,
invoices and documents. A tenant can never see another tenant's records.
"""

import frappe
from frappe.utils import flt, getdate, nowdate


def _current_tenant():
	"""
	Resolve the logged-in user to their Property Tenant docname.

	Matches on the tenant's linked portal `user`, falling back to email. Raises a
	permission error if the session isn't a tenant.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw("Please sign in.", frappe.PermissionError)

	name = frappe.db.get_value("Property Tenant", {"user": user}, "name")
	if not name:
		# Fallback: match by email (portal user email == tenant email).
		name = frappe.db.get_value("Property Tenant", {"email": user}, "name")
	if not name:
		frappe.throw("No tenant profile is linked to your account.", frappe.PermissionError)
	return name


def _active_lease(tenant):
	"""The tenant's current lease (most recent Active, else most recent any)."""
	leases = frappe.get_all(
		"Lease Agreement", filters={"tenant": tenant},
		fields=["name", "property", "unit", "status", "rent_amount", "deposit_amount",
				"start_date", "end_date", "agreement_pdf", "signed_on",
				"tenant_signature", "caretaker_signature", "initial_payment_status", "creation"],
		order_by="creation desc",
	)
	if not leases:
		return None
	active = [l for l in leases if l.status == "Active"]
	return (active[0] if active else leases[0])


def _tenant_name(tenant):
	return frappe.db.get_value("Property Tenant", tenant, "tenant_name") or tenant


@frappe.whitelist()
def my_profile():
	"""The logged-in tenant's own profile."""
	tenant = _current_tenant()
	t = frappe.db.get_value(
		"Property Tenant", tenant,
		["name", "tenant_name", "phone", "email", "national_id", "income_range", "status"],
		as_dict=True,
	) or {}
	return {
		"id": t.get("name"),
		"name": t.get("tenant_name") or t.get("name"),
		"phone": t.get("phone") or "",
		"email": t.get("email") or "",
		"national_id": t.get("national_id") or "",
		"income_range": t.get("income_range") or "",
		"status": t.get("status") or "Active",
	}


@frappe.whitelist()
def my_lease():
	"""The logged-in tenant's lease + unit + landlord/caretaker details."""
	tenant = _current_tenant()
	le = _active_lease(tenant)
	if not le:
		return None

	property_name = frappe.db.get_value("Property", le.property, "property_name") or le.property
	unit_number = frappe.db.get_value("Property Unit", le.unit, "unit_number") or le.unit

	landlord = caretaker = ""
	prop = frappe.db.get_value("Property", le.property, ["landlord", "caretaker"], as_dict=True) or {}
	if prop.get("landlord"):
		landlord = frappe.db.get_value("Landlord", prop.landlord, "landlord_name") or prop.landlord
	if prop.get("caretaker"):
		caretaker = frappe.db.get_value("Caretaker", prop.caretaker, "caretaker_name") or prop.caretaker

	return {
		"lease": le.name,
		"tenant": tenant,
		"tenant_name": _tenant_name(tenant),
		"property": le.property,
		"property_name": property_name,
		"unit": unit_number,
		"landlord": landlord,
		"caretaker": caretaker,
		"lease_start": str(le.start_date) if le.start_date else None,
		"lease_end": str(le.end_date) if le.end_date else None,
		"rent": flt(le.rent_amount),
		"deposit": flt(le.deposit_amount),
		"status": le.status,
		"initial_payment_status": le.initial_payment_status or "Pending",
		"is_signed": bool(le.signed_on) or bool((le.tenant_signature or "").strip()),
		"has_lease_pdf": bool(le.agreement_pdf) or bool(
			frappe.db.get_value("File", {"attached_to_doctype": "Lease Agreement",
										  "attached_to_name": le.name, "file_name": ["like", "%.pdf"]}, "name")
		),
	}


def _tenant_balance(tenant_name):
	"""Outstanding across the tenant's submitted Sales Invoices (customer = tenant_name)."""
	rows = frappe.get_all(
		"Sales Invoice",
		filters={"docstatus": 1, "customer": tenant_name, "outstanding_amount": [">", 0]},
		fields=["sum(outstanding_amount) as bal"],
	)
	return flt(rows[0].bal) if rows and rows[0].bal else 0.0


@frappe.whitelist()
def my_invoices():
	"""The logged-in tenant's Sales Invoices (customer keyed to their name)."""
	tenant = _current_tenant()
	name = _tenant_name(tenant)
	today = getdate(nowdate())

	invoices = frappe.get_all(
		"Sales Invoice",
		filters={"docstatus": ["<", 2], "customer": name},
		fields=["name", "posting_date", "due_date", "grand_total", "outstanding_amount",
				"status", "docstatus"],
		order_by="posting_date desc",
	)
	out = []
	for inv in invoices:
		paid = flt(inv.grand_total) - flt(inv.outstanding_amount)
		overdue = bool(inv.due_date and getdate(inv.due_date) < today and flt(inv.outstanding_amount) > 0)
		print_url = f"/api/method/property_management.api.v2.finance.invoice_pdf?doctype=Sales%20Invoice&name={inv.name}"
		out.append({
			"id": inv.name,
			"posting_date": str(inv.posting_date) if inv.posting_date else None,
			"due_date": str(inv.due_date) if inv.due_date else None,
			"total": flt(inv.grand_total),
			"paid": paid,
			"outstanding": flt(inv.outstanding_amount),
			"status": ("Overdue" if overdue else inv.status),
			"is_draft": inv.docstatus == 0,
			"printUrl": print_url,
		})
	return {
		"invoices": out,
		"total_billed": sum(i["total"] for i in out),
		"total_outstanding": sum(i["outstanding"] for i in out),
		"count": len(out),
	}


@frappe.whitelist()
def my_payments(limit=50):
	"""
	The logged-in tenant's payment history: paid M-Pesa Transactions tied to their
	leases, plus any submitted Payment Entries under their customer.
	"""
	tenant = _current_tenant()
	name = _tenant_name(tenant)

	lease_names = frappe.get_all("Lease Agreement", filters={"tenant": tenant}, pluck="name")
	payments = []

	# M-Pesa receipts referencing the tenant's leases.
	if lease_names:
		txns = frappe.get_all(
			"Mpesa Transaction",
			filters={"status": "Paid", "reference_doctype": "Lease Agreement",
					 "reference_name": ["in", lease_names]},
			fields=["name", "kind", "amount", "mpesa_receipt", "phone", "modified"],
			order_by="modified desc", limit_page_length=int(limit),
		)
		for t in txns:
			payments.append({
				"id": t.mpesa_receipt or t.name,
				"date": str(t.modified)[:10],
				"amount": flt(t.amount),
				"method": f"M-Pesa ({t.kind})" if t.kind else "M-Pesa",
				"status": "Paid",
			})

	# Native Payment Entries under this customer (invoice settlements).
	pes = frappe.get_all(
		"Payment Entry",
		filters={"docstatus": 1, "party_type": "Customer", "party": name},
		fields=["name", "paid_amount", "posting_date", "mode_of_payment"],
		order_by="posting_date desc", limit_page_length=int(limit),
	)
	for p in pes:
		payments.append({
			"id": p.name,
			"date": str(p.posting_date)[:10] if p.posting_date else "",
			"amount": flt(p.paid_amount),
			"method": p.mode_of_payment or "Bank",
			"status": "Paid",
		})

	payments.sort(key=lambda x: x["date"], reverse=True)
	return payments[: int(limit)]


@frappe.whitelist()
def my_dashboard():
	"""Everything the tenant landing page needs in one call."""
	tenant = _current_tenant()
	lease = my_lease()
	name = _tenant_name(tenant)
	balance = _tenant_balance(name)
	payments = my_payments(limit=5)
	return {
		"tenant_name": name,
		"lease": lease,
		"balance": balance,
		"recent_payments": payments,
	}


@frappe.whitelist()
def my_documents():
	"""
	The logged-in tenant's own documents: signed lease PDF(s) plus their national
	ID images (returned as base64 data URLs so they render without a separate
	permission-guarded file request).
	"""
	tenant = _current_tenant()
	leases = frappe.get_all(
		"Lease Agreement", filters={"tenant": tenant},
		fields=["name", "agreement_pdf", "signed_on", "tenant_signature",
				"caretaker_signature", "status", "start_date", "creation"],
		order_by="creation desc",
	)
	out = []
	for le in leases:
		pdf = le.agreement_pdf or frappe.db.get_value(
			"File", {"attached_to_doctype": "Lease Agreement", "attached_to_name": le.name,
					 "file_name": ["like", "%.pdf"]}, "file_url")
		out.append({
			"lease": le.name,
			"title": f"Tenancy Agreement — {le.name}",
			"status": le.status,
			"signed_on": str(le.signed_on)[:19] if le.signed_on else None,
			"is_signed": bool(le.signed_on) or bool((le.tenant_signature or "").strip()),
			"has_lease_pdf": bool(pdf),
			"lease_pdf_url": pdf,
		})
	return out


@frappe.whitelist()
def my_id_documents():
	"""The logged-in tenant's national ID images (front/back) as base64 data URLs."""
	from property_management.api.documents import file_to_data_url
	tenant = _current_tenant()
	t = frappe.db.get_value("Property Tenant", tenant,
							["national_id", "national_id_front", "national_id_back"], as_dict=True) or frappe._dict()
	return {
		"national_id": t.get("national_id") or "",
		"id_front": file_to_data_url(t.get("national_id_front")),
		"id_back": file_to_data_url(t.get("national_id_back")),
		"has_id_front": bool(t.get("national_id_front")),
		"has_id_back": bool(t.get("national_id_back")),
	}


@frappe.whitelist()
def download_my_lease(lease):
	"""
	Stream the tenant's OWN lease PDF. Verifies the lease belongs to the logged-in
	tenant before serving (a tenant can only download their own agreement).
	"""
	tenant = _current_tenant()
	if not frappe.db.exists("Lease Agreement", lease):
		frappe.throw("Lease not found")
	if frappe.db.get_value("Lease Agreement", lease, "tenant") != tenant:
		frappe.throw("You can only download your own lease.", frappe.PermissionError)

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


# --------------------------------------------------------------------------
# Feedback — tenant or caretaker raises feedback; admin/caretaker can respond
# --------------------------------------------------------------------------

@frappe.whitelist()
def raise_complaint(subject=None, description=None, category=None, priority="Medium", photo=None, feedback=None):
	"""
	Raise feedback for the logged-in tenant. Property/unit are derived from the
	tenant's active lease, and organization auto-sets via the before_insert hook.
	
	Accepts either old format (subject+description) or new format (feedback).
	"""
	tenant = _current_tenant()
	
	# Support both old format (subject+description) and new format (feedback)
	feedback_text = feedback or ""
	if not feedback_text and subject:
		feedback_text = f"{subject}\n\n{description or ''}" if description else subject
	
	if not feedback_text.strip():
		frappe.throw("Please provide your feedback.")

	le = _active_lease(tenant)
	tenant_doc = frappe.get_doc("Property Tenant", tenant)
	
	doc = frappe.get_doc({
		"doctype": "Tenant Complaint",
		"raised_by_type": "Tenant",
		"raised_by_user": frappe.session.user,
		"tenant": tenant,
		"tenant_name": tenant_doc.tenant_name or tenant,
		"mobile_number": tenant_doc.phone or "",
		"property": le.property if le else None,
		"unit": le.unit if le else None,
		"category": category or "General",
		"priority": priority or "Medium",
		"feedback": feedback_text.strip(),
		"photo": photo or None,
		"status": "Open",
	})
	doc.insert(ignore_permissions=True)
	return {"feedback": doc.name, "status": doc.status}


@frappe.whitelist()
def my_complaints():
	"""All feedback raised by the logged-in tenant, newest first."""
	tenant = _current_tenant()
	rows = frappe.get_all(
		"Tenant Complaint", filters={"tenant": tenant},
		fields=["name", "feedback", "category", "priority", "status",
				"property", "unit", "admin_response", "responded_at", "creation"],
		order_by="creation desc",
	)
	out = []
	for r in rows:
		prop_name = frappe.db.get_value("Property", r.property, "property_name") if r.property else ""
		unit_no = frappe.db.get_value("Property Unit", r.unit, "unit_number") if r.unit else ""
		out.append({
			"id": r.name,
			"feedback": r.feedback or "",
			"category": r.category or "General",
			"priority": r.priority or "Medium",
			"status": r.status or "Open",
			"property": prop_name or r.property or "",
			"unit": unit_no or r.unit or "",
			"response": r.admin_response or "",
			"responded_at": str(r.responded_at)[:19] if r.responded_at else None,
			"date": str(r.creation)[:10] if r.creation else "",
		})
	return out
