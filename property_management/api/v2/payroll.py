# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""API v2 - Payroll endpoints (HRMS)."""

import frappe

from property_management.api.v2 import envelope


@frappe.whitelist()
@envelope
def run(company=None, organization=None, period=None, property=None):
	"""
	Run monthly payroll (HRMS). period = 'YYYY-MM' or a date. Returns the run summary
	with per-employee Salary Slip gross/deductions/net.

	Scope resolution when neither company nor organization is passed:
	  - a property implies its organization;
	  - otherwise resolve_organization() (user assignment, then the single/first
	    organization) is used, consistent with the rest of the API.
	"""
	if not company and not organization:
		if property:
			organization = frappe.db.get_value("Property", property, "organization")
		if not organization:
			from property_management.api.utils import resolve_organization
			organization = resolve_organization()
		if not organization:
			frappe.throw("No organization found; provision an Organization before running payroll.")

	from property_management.integration.payroll import run_payroll
	return run_payroll(company=company, organization=organization, period=period, property_name=property)


@frappe.whitelist()
@envelope
def list_employees(organization=None, property=None, search=None, page=1, page_size=8):
	"""
	List HRMS Employees with pagination and the app's custom fields (property_ref, gross_salary,
	mpesa_phone). Runs server-side so it is not blocked by /resource field
	validation, and only requests custom fields that actually exist yet (they are
	created on migrate), so it degrades gracefully before provisioning.
	"""
	# Sanitize pagination inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	
	meta = frappe.get_meta("Employee")
	fields = ["name", "employee_name", "designation", "status"]
	for f in ("property_ref", "organization", "gross_salary", "mpesa_phone", "cell_number",
			  "personal_email", "national_id", "bonus_deposit"):
		if meta.has_field(f):
			fields.append(f)

	filters = {}
	if property and meta.has_field("property_ref"):
		filters["property_ref"] = property
	if organization and meta.has_field("organization"):
		filters["organization"] = organization

	rows = frappe.get_all(
		"Employee", filters=filters, fields=fields,
		order_by="modified desc",
	)
	
	out = []
	for r in rows:
		# Normalise to stable keys the frontend expects.
		r["property"] = r.get("property_ref") or ""
		r["phone"] = r.get("mpesa_phone") or r.get("cell_number") or ""
		r["email"] = r.get("personal_email") or ""
		
		# Apply search filter
		if search:
			s = str(search).lower()
			hay = f"{r.get('employee_name', '')} {r.get('designation', '')} {r.get('phone', '')} {r.get('email', '')}".lower()
			if s not in hay:
				continue
		out.append(r)
	
	# Apply pagination
	total = len(out)
	offset = (page - 1) * page_size
	paginated = out[offset:offset + page_size]
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': paginated,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


def _resolve_company(company=None, organization=None, property=None):
	"""Resolve the payroll Company from an explicit company, an organization, or a
	property's organization; fall back to the single/first Company."""
	if company:
		return company, organization
	if not organization and property:
		organization = frappe.db.get_value("Property", property, "organization")
	if organization:
		from property_management.integration.settings import company_for_organization
		company = company_for_organization(organization)
	if not company:
		company = frappe.db.get_value("Company", {}, "name")
	return company, organization


def _create_one_employee(employee_name, gross_salary, company, organization=None, property=None,
						  designation="Office Staff", mpesa_phone=None, national_id=None,
						  bonus_deposit=None):
	"""Create the HRMS Employee and set the app's extra fields (national_id,
	bonus_deposit). Returns the employee name."""
	from property_management.integration.payroll import create_employee as _create
	name = _create(
		employee_name=employee_name, company=company, gross_salary=float(gross_salary or 0),
		organization=organization, property_name=property, designation=designation,
		mpesa_phone=mpesa_phone,
	)
	# Set the extra fields directly (not part of the integration helper signature).
	updates = {}
	if national_id:
		updates["national_id"] = str(national_id).strip()
	if bonus_deposit not in (None, ""):
		try:
			updates["bonus_deposit"] = float(bonus_deposit)
		except (TypeError, ValueError):
			pass
	if updates:
		frappe.db.set_value("Employee", name, updates, update_modified=False)
	return name


@frappe.whitelist()
@envelope
def create_employee(employee_name, gross_salary, company=None, organization=None, property=None,
					designation="Office Staff", mpesa_phone=None, national_id=None,
					bonus_deposit=None):
	"""Create an HRMS Employee for a caretaker / office / construction worker.

	Company may be omitted and is then resolved from the organization (or the
	property's organization), so the frontend only needs to pass organization.
	"""
	company, organization = _resolve_company(company, organization, property)
	if not company:
		frappe.throw("No company found; provision an Organization first.")

	name = _create_one_employee(
		employee_name=employee_name, gross_salary=gross_salary, company=company,
		organization=organization, property=property, designation=designation,
		mpesa_phone=mpesa_phone, national_id=national_id, bonus_deposit=bonus_deposit,
	)
	return {"employee": name}


@frappe.whitelist()
@envelope
def bulk_create_employees(employees, company=None, organization=None):
	"""
	Bulk-create HRMS Employees (payroll-enrolled) from an imported list.

	Each row: {employee_name|name, designation, gross_salary|salary, national_id,
	bonus_deposit, mpesa_phone|phone, property}. The Company is resolved once from
	the organization (or the single/first Company). Rows with no name or salary are
	reported as failures. Returns {created, failed, total}.
	"""
	if isinstance(employees, str):
		employees = frappe.parse_json(employees) if employees else []
	employees = employees or []

	company, organization = _resolve_company(company, organization, None)
	if not company:
		frappe.throw("No company found; provision an Organization first.")

	created, failed = [], []
	for row in employees:
		name = (row.get("employee_name") or row.get("name") or "").strip()
		salary = row.get("gross_salary")
		if salary in (None, ""):
			salary = row.get("salary")
		if not name:
			failed.append({"name": name or "(blank)", "error": "Missing staff name"})
			continue
		if salary in (None, ""):
			failed.append({"name": name, "error": "Missing salary"})
			continue
		try:
			emp = _create_one_employee(
				employee_name=name,
				gross_salary=salary,
				company=company,
				organization=organization,
				property=row.get("property") or None,
				designation=(row.get("designation") or "Office Staff").strip() or "Office Staff",
				mpesa_phone=row.get("mpesa_phone") or row.get("phone") or None,
				national_id=row.get("national_id") or None,
				bonus_deposit=row.get("bonus_deposit") if row.get("bonus_deposit") not in (None, "") else None,
			)
			created.append(emp)
		except Exception as e:
			failed.append({"name": name, "error": str(e)})

	frappe.db.commit()
	return {"created": created, "failed": failed, "total": len(employees)}


@frappe.whitelist()
@envelope
def disburse(salary_slip):
	"""Trigger M-Pesa B2C net-pay disbursement for a submitted Salary Slip."""
	from property_management.integration.payroll import disburse_salary_slip
	return disburse_salary_slip(salary_slip)


@frappe.whitelist()
@envelope
def preview_statutory(gross):
	"""Preview Kenyan statutory deductions for a given gross (no records created)."""
	from property_management.integration.payroll_setup import compute_statutory
	return compute_statutory(float(gross))
