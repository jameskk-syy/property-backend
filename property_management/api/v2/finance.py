# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Finance endpoints (invoicing, payments, expenses).

All endpoints are authenticated (no allow_guest) and return the standard envelope.
They wrap the integration layer which posts to native ERPNext.
"""

import frappe
from frappe.utils import flt

from property_management.api.v2 import envelope


@frappe.whitelist()
@envelope
def create_invoice(property, tenant, items, invoice_type="Rent", due_date=None):
	"""
	Create + submit a native Sales Invoice for a tenant.

	items: JSON list of {"item_name","quantity","rate"}.
	Returns the Sales Invoice name + totals.
	"""
	if isinstance(items, str):
		items = frappe.parse_json(items)

	from property_management.integration.invoicing import create_sales_invoice
	si_name = create_sales_invoice(
		property_name=property, tenant=tenant, items=items,
		invoice_type=invoice_type, due_date=due_date, submit=True,
	)
	si = frappe.db.get_value(
		"Sales Invoice", si_name,
		["name", "grand_total", "outstanding_amount", "status", "customer"], as_dict=True,
	)
	return si


@frappe.whitelist()
@envelope
def record_payment(property, amount, sales_invoice=None, tenant=None, payment_method="M-Pesa", reference_no=None):
	"""Record a tenant payment as a native Payment Entry allocated to the invoice."""
	from property_management.integration.payments import create_payment_entry
	pe_name = create_payment_entry(
		property_name=property, amount=float(amount), sales_invoice=sales_invoice,
		tenant=tenant, payment_method=payment_method, reference_no=reference_no, submit=True,
	)
	pe = frappe.db.get_value(
		"Payment Entry", pe_name, ["name", "paid_amount", "party", "mode_of_payment"], as_dict=True
	)
	return pe


@frappe.whitelist()
@envelope
def record_expense(property, vendor_name, amount, description, submit=True):
	"""
	Record a property expense as a native Purchase Invoice.

	NOTE: maker-checker (creator cannot approve own) is enforced on the Property
	Expense doctype workflow. This direct endpoint is for already-authorized posting;
	the approval route is via approvals.approve_request which calls the doctype's
	approve() with the maker-checker guard.
	"""
	from property_management.integration.expenses import create_purchase_invoice
	pi_name = create_purchase_invoice(
		property_name=property, supplier_name=vendor_name, amount=float(amount),
		description=description, submit=bool(submit),
	)
	pi = frappe.db.get_value(
		"Purchase Invoice", pi_name, ["name", "grand_total", "supplier", "status"], as_dict=True
	)
	return pi


@frappe.whitelist()
@envelope
def generate_monthly_invoices(period=None, organization=None, property=None):
	"""
	Generate monthly invoices for every active lease in scope. Rent and utilities
	are billed on SEPARATE invoices: a rent invoice (PM-MONTHLY) and a utility
	invoice combining garbage + metered water/electricity (PM-UTILITY). period =
	'YYYY-MM' (defaults to current month).
	"""
	from property_management.integration.billing import generate_monthly_invoices as _gen
	return _gen(period=period, organization=organization, property=property)


@frappe.whitelist()
@envelope
def list_expense_categories(active_only=True):
	"""List Expense Categories for the raise-expense form."""
	filters = {}
	if active_only in (True, "true", "1", 1):
		filters["is_active"] = 1
	return frappe.get_all(
		"Expense Category", filters=filters,
		fields=["name", "category_name", "account_name"], order_by="category_name asc",
	)


@frappe.whitelist()
@envelope
def list_expenses(property=None, organization=None, status=None, mine=None, page=1, page_size=8, search=None):
	"""
	List Property Expenses with pagination for the UI.
	"""
	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	offset = (page - 1) * page_size
	
	filters = {}
	if property:
		filters["property"] = property
	if organization:
		filters["organization"] = organization
	if status:
		filters["status"] = status
	if mine in (1, "1", True, "true"):
		filters["owner"] = frappe.session.user
	
	# Search filter
	or_filters = None
	if search:
		search_term = f'%{search}%'
		or_filters = [
			['Property Expense', 'vendor_name', 'like', search_term],
			['Property Expense', 'work_description', 'like', search_term],
		]
	
	# Get total count
	if or_filters:
		total = len(frappe.get_all('Property Expense', filters=filters, or_filters=or_filters, pluck='name'))
	else:
		total = frappe.db.count('Property Expense', filters)
	
	# Get paginated data
	if or_filters:
		data = frappe.get_all(
			"Property Expense", filters=filters, or_filters=or_filters,
			fields=["name", "property", "organization", "vendor_name", "expense_category",
					"amount", "work_description", "status", "approved_at", "creation", "owner",
					"expense_scope", "unit", "deduct_from_deposit", "deposit_deducted", "deposit_shortfall"],
			order_by="creation desc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	else:
		data = frappe.get_all(
			"Property Expense", filters=filters,
			fields=["name", "property", "organization", "vendor_name", "expense_category",
					"amount", "work_description", "status", "approved_at", "creation", "owner",
					"expense_scope", "unit", "deduct_from_deposit", "deposit_deducted", "deposit_shortfall"],
			order_by="creation desc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': data,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


@frappe.whitelist()
@envelope
def raise_expense(property, amount, work_description, expense_category=None, vendor=None,
				  vendor_name=None, vendor_phone=None, unit=None, expense_scope=None,
				  deduct_from_deposit=None):
	"""
	Raise a property expense for approval (Path A). Creates a Property Expense in
	'Pending Approval' plus a maker-checker Approval Request. Nothing posts to the
	ledger here; posting happens when a Director/Admin approves the request, which
	triggers Property Expense.approve() -> bridge_to_purchase_invoice().

	The creator (typically a caretaker) cannot approve their own request.
	vendor_phone is the payee M-Pesa number (used later for B2C disbursement).

	expense_scope: "Property" (whole property) or "Unit / Tenant". When Unit/Tenant
	with a unit, the expense is attached to that unit; if deduct_from_deposit is set
	the repair is taken from the unit tenant's deposit on approval.
	"""
	prop = frappe.get_doc("Property", property)
	organization = prop.organization

	# Normalise scope + validate the unit belongs to the property.
	scope = expense_scope if expense_scope in ("Property", "Unit / Tenant") else ("Unit / Tenant" if unit else "Property")
	if scope != "Unit / Tenant":
		unit = None
	if unit:
		if not frappe.db.exists("Property Unit", unit):
			frappe.throw("Selected unit does not exist.")
		unit_property = frappe.db.get_value("Property Unit", unit, "property")
		if unit_property != property:
			frappe.throw("Selected unit does not belong to the chosen property.")

	deduct = 1 if str(deduct_from_deposit).lower() in ("1", "true", "yes", "on") else 0
	if not unit:
		deduct = 0

	exp = frappe.get_doc({
		"doctype": "Property Expense",
		"organization": organization,
		"property": property,
		"expense_scope": scope,
		"unit": unit,
		"deduct_from_deposit": deduct,
		"expense_category": expense_category,
		"vendor": vendor,
		"vendor_name_manual": vendor_name,
		"vendor_phone": vendor_phone,
		"amount": float(amount),
		"work_description": work_description,
		"status": "Pending Approval",
	})
	exp.insert()

	# The approver_role is a Link to Role, so it must point to a role that
	# actually exists. Prefer Director, then fall back to roles that ship with
	# every site so the Approval Request is always created and visible in the
	# inbox. (If none somehow exist, leave it blank rather than fail the insert.)
	approver_role = None
	for candidate in ("Director", "System Manager", "Administrator"):
		if frappe.db.exists("Role", candidate):
			approver_role = candidate
			break

	req = frappe.get_doc({
		"doctype": "Approval Request",
		"organization": organization,
		"request_type": "Expense",
		"reference_doctype": "Property Expense",
		"reference_name": exp.name,
		"requested_by": frappe.session.user,
		"approver_role": approver_role,
		"status": "Pending",
	})
	req.insert(ignore_permissions=True)

	return {
		"property_expense": exp.name,
		"approval_request": req.name,
		"status": exp.status,
	}


@frappe.whitelist()
@envelope
def list_invoices(company=None, property=None, status=None, page=1, page_size=8, search=None):
	"""List Sales Invoices with pagination."""
	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	offset = (page - 1) * page_size
	
	filters = {"docstatus": ["<", 2]}
	if company:
		filters["company"] = company
	if property:
		filters["property_ref"] = property
	if status:
		filters["status"] = status
	
	# Search filter
	or_filters = None
	if search:
		search_term = f'%{search}%'
		or_filters = [
			['Sales Invoice', 'customer', 'like', search_term],
			['Sales Invoice', 'name', 'like', search_term],
		]
	
	# Get total count
	if or_filters:
		total = len(frappe.get_all('Sales Invoice', filters=filters, or_filters=or_filters, pluck='name'))
	else:
		total = frappe.db.count('Sales Invoice', filters)
	
	# Get paginated data
	if or_filters:
		data = frappe.get_all(
			"Sales Invoice", filters=filters, or_filters=or_filters,
			fields=["name", "customer", "company", "property_ref", "posting_date",
					"grand_total", "outstanding_amount", "status"],
			order_by="posting_date desc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	else:
		data = frappe.get_all(
			"Sales Invoice", filters=filters,
			fields=["name", "customer", "company", "property_ref", "posting_date",
					"grand_total", "outstanding_amount", "status"],
			order_by="posting_date desc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': data,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


@frappe.whitelist()
@envelope
def list_all_invoices(kind=None, status=None, search=None, page=1, page_size=8, property=None):
	"""
	Unified invoice register for admin with pagination: Sales Invoices (money in) + Purchase
	Invoices (money out), scoped to the caller's organization Company.

	property: optional Property filter. Both Sales and Purchase Invoices carry a
	`property_ref` custom field, so we filter on that when set.
	"""
	from property_management.api.utils import resolve_organization

	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))

	org = resolve_organization()
	company = frappe.db.get_value("Organization", org, "erpnext_company") if org else None
	if not company:
		from property_management.integration.settings import company_for_organization
		company = company_for_organization(org) if org else None

	def _print_url(doctype, name):
		from urllib.parse import quote
		return (f"/api/method/property_management.api.v2.finance.invoice_pdf"
				f"?doctype={quote(doctype)}&name={quote(name)}")

	rows = []
	common_filters = {"docstatus": ["<", 2]}
	if company:
		common_filters["company"] = company
	if status:
		common_filters["status"] = status
	if property:
		common_filters["property_ref"] = property

	if kind in (None, "", "sales"):
		for si in frappe.get_all(
			"Sales Invoice", filters=common_filters,
			fields=["name", "customer", "posting_date", "due_date", "grand_total",
					"outstanding_amount", "status"],
			order_by="posting_date desc",
		):
			rows.append({
				"id": si.name,
				"type": "Sales",
				"party": si.customer,
				"date": str(si.posting_date) if si.posting_date else "",
				"dueDate": str(si.due_date) if si.due_date else "",
				"total": flt(si.grand_total),
				"outstanding": flt(si.outstanding_amount),
				"status": si.status,
				"printUrl": _print_url("Sales Invoice", si.name),
			})

	if kind in (None, "", "purchase"):
		for pi in frappe.get_all(
			"Purchase Invoice", filters=common_filters,
			fields=["name", "supplier", "posting_date", "due_date", "grand_total",
					"outstanding_amount", "status"],
			order_by="posting_date desc",
		):
			rows.append({
				"id": pi.name,
				"type": "Purchase",
				"party": pi.supplier,
				"date": str(pi.posting_date) if pi.posting_date else "",
				"dueDate": str(pi.due_date) if pi.due_date else "",
				"total": flt(pi.grand_total),
				"outstanding": flt(pi.outstanding_amount),
				"status": pi.status,
				"printUrl": _print_url("Purchase Invoice", pi.name),
			})

	# Optional client-driven text filter across id/party.
	if search:
		s = str(search).lower()
		rows = [r for r in rows if s in (r["id"] or "").lower() or s in (r["party"] or "").lower()]

	rows.sort(key=lambda r: r["date"], reverse=True)

	# Apply pagination
	total = len(rows)
	offset = (page - 1) * page_size
	paginated_rows = rows[offset:offset + page_size]
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1

	return {
		'data': paginated_rows,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


# --------------------------------------------------------------------------
# Invoice PDF — self-contained, no Frappe desk shell / no login redirect
# --------------------------------------------------------------------------

def _invoice_company(doctype, name):
	"""Company on the invoice (used for the permission check + branding)."""
	return frappe.db.get_value(doctype, name, "company")


def _assert_can_view_invoice(doctype, name):
	"""Allow the caller only if the invoice belongs to their organization's
	Company (or they are an unrestricted admin role). Raises PermissionError."""
	from property_management.api.roles import UNRESTRICTED_ROLES
	from property_management.api.utils import resolve_organization

	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or (roles & UNRESTRICTED_ROLES):
		return

	inv_company = _invoice_company(doctype, name)
	org = resolve_organization()
	org_company = None
	if org:
		org_company = frappe.db.get_value("Organization", org, "erpnext_company")
		if not org_company:
			from property_management.integration.settings import company_for_organization
			org_company = company_for_organization(org)
	if inv_company and org_company and inv_company == org_company:
		return
	raise frappe.PermissionError("You are not permitted to view this invoice.")


def _money(v):
	return f"KSh {flt(v):,.2f}"


def _invoice_pdf_html(doctype, name):
	"""Render a clean branded invoice to HTML (no desk shell)."""
	doc = frappe.get_doc(doctype, name)
	company = doc.company
	is_sales = doctype == "Sales Invoice"
	party_label = "Bill To" if is_sales else "Supplier"
	party = getattr(doc, "customer", None) if is_sales else getattr(doc, "supplier", None)

	# Branding (company name + optional org display name).
	company_name = company or "Invoice"
	address_line = frappe.db.get_value("Company", company, "email") or ""

	def esc(v):
		if v in (None, ""):
			return "—"
		return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

	# Line items.
	item_rows = ""
	for it in (doc.get("items") or []):
		item_rows += (
			f'<tr><td>{esc(it.get("item_name") or it.get("item_code") or it.get("description"))}</td>'
			f'<td class="num">{flt(it.get("qty")):,.2f}</td>'
			f'<td class="num">{_money(it.get("rate"))}</td>'
			f'<td class="num">{_money(it.get("amount"))}</td></tr>'
		)
	if not item_rows:
		item_rows = '<tr><td colspan="4" class="muted">No line items.</td></tr>'

	status = esc(doc.get("status"))
	posting = esc(doc.get("posting_date"))
	due = esc(doc.get("due_date"))
	grand = _money(doc.get("grand_total"))
	outstanding = _money(doc.get("outstanding_amount"))
	paid = _money(flt(doc.get("grand_total")) - flt(doc.get("outstanding_amount")))
	title = "TAX INVOICE" if is_sales else "PURCHASE INVOICE"

	return f"""
	<html><head><style>
		body {{ font-family: Helvetica, Arial, sans-serif; color:#1e293b; padding:32px; font-size:12px; }}
		.top {{ display:flex; justify-content:space-between; align-items:flex-start; border-bottom:2px solid #0f172a; padding-bottom:14px; margin-bottom:20px; }}
		h1 {{ font-size:20px; margin:0; }}
		.muted {{ color:#64748b; }}
		.doc-title {{ font-size:16px; font-weight:bold; text-align:right; }}
		.doc-no {{ text-align:right; color:#475569; margin-top:2px; }}
		.meta {{ width:100%; margin:6px 0 20px; border-collapse:collapse; }}
		.meta td {{ padding:3px 0; font-size:12px; }}
		.meta .k {{ color:#64748b; width:120px; }}
		table.items {{ width:100%; border-collapse:collapse; margin-top:8px; }}
		table.items th {{ text-align:left; background:#f1f5f9; padding:8px 10px; font-size:11px; color:#475569; border-bottom:1px solid #e2e8f0; }}
		table.items td {{ padding:8px 10px; border-bottom:1px solid #eef2f7; }}
		.num {{ text-align:right; }}
		.totals {{ width:280px; margin-left:auto; margin-top:14px; border-collapse:collapse; }}
		.totals td {{ padding:6px 10px; }}
		.totals .k {{ color:#64748b; }}
		.totals .grand {{ font-weight:bold; font-size:14px; border-top:2px solid #0f172a; }}
		.badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:11px; font-weight:bold; background:#f1f5f9; color:#334155; }}
		.footer {{ margin-top:30px; font-size:10px; color:#94a3b8; border-top:1px solid #e2e8f0; padding-top:8px; }}
	</style></head><body>
		<div class="top">
			<div>
				<h1>{esc(company_name)}</h1>
				<div class="muted">{esc(address_line)}</div>
			</div>
			<div>
				<div class="doc-title">{title}</div>
				<div class="doc-no"># {esc(doc.name)}</div>
				<div class="doc-no"><span class="badge">{status}</span></div>
			</div>
		</div>

		<table class="meta">
			<tr><td class="k">{party_label}</td><td>{esc(party)}</td></tr>
			<tr><td class="k">Invoice Date</td><td>{posting}</td></tr>
			<tr><td class="k">Due Date</td><td>{due}</td></tr>
		</table>

		<table class="items">
			<thead><tr><th>Description</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">Amount</th></tr></thead>
			<tbody>{item_rows}</tbody>
		</table>

		<table class="totals">
			<tr><td class="k">Grand Total</td><td class="num">{grand}</td></tr>
			<tr><td class="k">Paid</td><td class="num">{paid}</td></tr>
			<tr class="grand"><td>Balance Due</td><td class="num">{outstanding}</td></tr>
		</table>

		<div class="footer">Generated by Nest Property Management System for {esc(company_name)}.</div>
	</body></html>
	"""


@frappe.whitelist()
def invoice_pdf(doctype=None, name=None):
	"""
	Stream a clean invoice PDF through the authenticated API — no Frappe desk
	shell, no print-format dependency, no login redirect. Works for Sales and
	Purchase Invoices, including drafts. The frontend fetches this with the
	session cookie and shows the blob.
	"""
	if doctype not in ("Sales Invoice", "Purchase Invoice"):
		frappe.throw("Unsupported document type.")
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw("Invoice not found.")

	_assert_can_view_invoice(doctype, name)

	from frappe.utils.pdf import get_pdf
	html = _invoice_pdf_html(doctype, name)
	pdf_bytes = get_pdf(html)

	frappe.local.response.filename = f"{name}.pdf"
	frappe.local.response.filecontent = pdf_bytes
	frappe.local.response.type = "pdf"
