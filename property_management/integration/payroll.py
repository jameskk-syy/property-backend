# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 5 - Payroll runner (HRMS).

Maps custom Employees to HRMS Employees, assigns a Salary Structure (base = gross),
and runs a Payroll Entry to generate Salary Slips that post to the GL. Net pay is
disbursed via the existing M-Pesa B2C mechanism, referencing the Salary Slip.
"""

import contextlib

import frappe
from frappe.utils import flt, nowdate, getdate, get_first_day, get_last_day

from property_management.integration import erpnext_setup as setup
from property_management.integration import payroll_setup
from property_management.integration.settings import require_company


@contextlib.contextmanager
def _as_system_user():
	original = frappe.session.user
	frappe.set_user("Administrator")
	try:
		yield
	finally:
		frappe.set_user(original)


def _category_for(emp) -> str:
	"""Infer a salary-structure category from the employee's designation."""
	desig = (getattr(emp, "designation", "") or "").lower()
	if "caretaker" in desig:
		return "Caretaker"
	if any(k in desig for k in ("mason", "fundi", "worker", "labour", "labor", "construction")):
		return "Construction Worker"
	return "Office"


def ensure_designations():
	"""Ensure the employee-category Designations exist (HRMS turns designation into a Link)."""
	for d in ("Caretaker", "Office Staff", "Construction Worker", "Mason", "Fundi", "Labourer"):
		if not frappe.db.exists("Designation", d):
			frappe.get_doc({"doctype": "Designation", "designation_name": d}).insert(ignore_permissions=True)


def ensure_holiday_list(company: str) -> str:
	"""Ensure a default Holiday List exists and is set as the company default."""
	year = getdate().year
	name = f"KE Holidays {year} - {frappe.get_cached_value('Company', company, 'abbr')}"
	if not frappe.db.exists("Holiday List", name):
		hl = frappe.get_doc({
			"doctype": "Holiday List",
			"holiday_list_name": name,
			"from_date": f"{year}-01-01",
			"to_date": f"{year}-12-31",
			"weekly_off": "Sunday",
		})
		hl.flags.ignore_mandatory = True
		hl.insert(ignore_permissions=True)
		# Populate weekly-off holidays so the list is valid.
		try:
			hl.get_weekly_off_dates()
			hl.save(ignore_permissions=True)
		except Exception:
			pass
	# Set as company default.
	if not frappe.get_cached_value("Company", company, "default_holiday_list"):
		frappe.db.set_value("Company", company, "default_holiday_list", name, update_modified=False)
	return name


def _ensure_hr_prerequisites(company: str):
	"""Ensure a default HRMS Department/Designation/Holiday List exist for the company."""
	ensure_designations()
	ensure_holiday_list(company)
	if not frappe.db.exists("Department", {"company": company}):
		# HRMS ships a root 'All Departments'; create a company department under it.
		parent = frappe.db.get_value("Department", {"is_group": 1}, "name")
		dept = frappe.get_doc({
			"doctype": "Department",
			"department_name": "Operations",
			"company": company,
			"parent_department": parent,
		})
		dept.flags.ignore_mandatory = True
		dept.insert(ignore_permissions=True)


def _ensure_gender(preferred="Other"):
	"""Return a valid Gender name. HRMS requires a linked Gender; some sites don't
	ship 'Other'. Use the preferred one if it exists, else any existing Gender,
	else create the preferred one."""
	if preferred and frappe.db.exists("Gender", preferred):
		return preferred
	existing = frappe.db.get_value("Gender", {}, "name")
	if existing:
		return existing
	name = preferred or "Other"
	try:
		g = frappe.get_doc({"doctype": "Gender", "gender": name})
		g.insert(ignore_permissions=True)
		return g.name
	except Exception:
		return existing or name


def create_employee(
	employee_name: str,
	company: str,
	gross_salary: float,
	organization: str = None,
	property_name: str = None,
	designation: str = "Office Staff",
	mpesa_phone: str = None,
	gender: str = "Other",
	date_of_birth: str = "1990-01-01",
	date_of_joining: str = None,
) -> str:
	"""
	Create an HRMS Employee with the mandatory native fields plus the integration
	custom fields (organization_ref, property_ref, gross_salary, mpesa_phone).

	NOTE: installing HRMS overrode the app's original custom 'Employee' doctype with
	HRMS's Employee (module 'Setup'). So this IS the HRMS Employee.
	"""
	ensure_designations()
	gender = _ensure_gender(gender)
	first, *rest = employee_name.strip().split(" ", 1)
	last = rest[0] if rest else ""

	emp = frappe.get_doc({
		"doctype": "Employee",
		"first_name": first,
		"last_name": last,
		"employee_name": employee_name,
		"company": company,
		"gender": gender,
		"date_of_birth": date_of_birth,
		"date_of_joining": date_of_joining or nowdate(),
		"status": "Active",
		"designation": designation,
		"organization_ref": organization,
		"property_ref": property_name,
		"gross_salary": flt(gross_salary),
		"mpesa_phone": mpesa_phone or "",
	})
	emp.flags.ignore_mandatory = True
	emp.insert(ignore_permissions=True)
	return emp.name


def assign_salary_structure(
	employee: str,
	company: str,
	gross: float,
	category: str,
	from_date: str | None = None,
	payroll_payable_account: str | None = None,
) -> str:
	"""Create a Salary Structure Assignment (base = gross) for an employee."""
	structure = payroll_setup.setup_salary_structure(company, category)
	from_date = from_date or get_first_day(nowdate())

	# Assignment cannot pre-date the employee's joining date.
	doj = frappe.db.get_value("Employee", employee, "date_of_joining")
	if doj and getdate(from_date) < getdate(doj):
		from_date = doj

	# The payable account MUST match the Payroll Entry's payable account, otherwise
	# HRMS excludes the employee from the run (get_filtered_employees join).
	payroll_payable_account = payroll_payable_account or payroll_setup._statutory_payable_account(company, "Salary")

	existing = frappe.db.get_value(
		"Salary Structure Assignment",
		{"employee": employee, "salary_structure": structure, "from_date": from_date, "docstatus": ["<", 2]},
		"name",
	)
	if existing:
		return existing

	ssa = frappe.get_doc({
		"doctype": "Salary Structure Assignment",
		"employee": employee,
		"salary_structure": structure,
		"company": company,
		"from_date": from_date,
		"base": flt(gross),
		"payroll_payable_account": payroll_payable_account,
	})
	ssa.flags.ignore_mandatory = True
	ssa.insert(ignore_permissions=True)
	ssa.submit()
	return ssa.name


@frappe.whitelist()
def run_payroll(company: str = None, organization: str = None, period: str = None, property_name: str = None):
	"""
	Run payroll for a company (or organization) for a period ('YYYY-MM' or a date).
	Ensures each active employee has a Salary Structure Assignment, then creates a
	Payroll Entry and generates + submits Salary Slips (which post to the GL).

	Returns a summary dict.
	"""
	with _as_system_user():
		if organization and not company:
			company = require_company(organization)
		if not company:
			frappe.throw("company or organization is required")

		payroll_setup.setup_salary_components(company)
		_ensure_hr_prerequisites(company)

		# Resolve the payroll month.
		if period and len(period) == 7:  # 'YYYY-MM'
			ref_date = getdate(period + "-01")
		elif period:
			ref_date = getdate(period)
		else:
			ref_date = getdate(nowdate())
		start = get_first_day(ref_date)
		end = get_last_day(ref_date)

		# Active employees for this company (+ optional org/property via custom fields).
		emp_filters = {"status": "Active", "company": company}
		if organization:
			emp_filters["organization_ref"] = organization
		if property_name:
			emp_filters["property_ref"] = property_name

		employees = frappe.get_all(
			"Employee", filters=emp_filters,
			fields=["name", "employee_name", "gross_salary", "designation", "property_ref as property"],
		)
		if not employees:
			return {"status": "No active employees", "company": company, "slips": 0}

		# Idempotency: if this company already has Salary Slips for the period, return
		# that existing run instead of erroring with a duplicate-entry exception.
		emp_names = [e.name for e in employees]
		existing = frappe.get_all(
			"Salary Slip",
			filters={
				"company": company,
				"start_date": start,
				"end_date": end,
				"employee": ["in", emp_names],
				"docstatus": ["<", 2],
			},
			fields=["name", "employee", "employee_name", "gross_pay", "total_deduction", "net_pay", "docstatus"],
		)
		if existing:
			return {
				"status": "Already processed",
				"company": company,
				"period": f"{start} to {end}",
				"slips": len(existing),
				"salary_slips": existing,
				"total_gross": sum(flt(s.gross_pay) for s in existing),
				"total_net": sum(flt(s.net_pay) for s in existing),
				"message": "Salary slips already exist for this period; returning the existing run.",
			}

		# Resolve the payroll payable account once; SSA and Payroll Entry must agree.
		payable_account = payroll_setup._statutory_payable_account(company, "Salary")

		# Ensure each employee has a structure assignment effective by the period start.
		assigned = 0
		for emp in employees:
			gross = flt(emp.gross_salary)
			if gross <= 0:
				continue
			category = _category_for(frappe._dict(emp))
			doj = frappe.db.get_value("Employee", emp.name, "date_of_joining")
			eff = start
			if doj and getdate(doj) > getdate(start):
				eff = getdate(doj) if getdate(doj) <= getdate(end) else start
			assign_salary_structure(
				emp.name, company, gross, category,
				from_date=eff, payroll_payable_account=payable_account,
			)
			assigned += 1
		frappe.db.commit()
		if not assigned:
			return {"status": "No payable employees", "company": company, "slips": 0}

		# Create the Payroll Entry.
		pe = frappe.get_doc({
			"doctype": "Payroll Entry",
			"company": company,
			"posting_date": end,
			"payroll_frequency": "Monthly",
			"start_date": start,
			"end_date": end,
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"payroll_payable_account": payable_account,
		})
		pe.flags.ignore_mandatory = True
		pe.insert(ignore_permissions=True)

		# Pull eligible employees into the child table + persist.
		pe.fill_employee_details()
		pe.save(ignore_permissions=True)
		frappe.db.commit()
		emp_rows = len(pe.get("employees") or [])

		# Submit the Payroll Entry, then create + submit the Salary Slips.
		pe.submit()
		frappe.db.commit()
		pe.reload()
		pe.create_salary_slips()
		frappe.db.commit()
		pe.reload()
		pe.submit_salary_slips()
		frappe.db.commit()

		frappe.db.commit()

		slips = frappe.get_all(
			"Salary Slip",
			filters={"payroll_entry": pe.name},
			fields=["name", "employee", "employee_name", "gross_pay", "total_deduction", "net_pay", "docstatus"],
		)
		return {
			"status": "Success",
			"company": company,
			"payroll_entry": pe.name,
			"period": f"{start} to {end}",
			"employees_in_entry": emp_rows,
			"slips": len(slips),
			"salary_slips": slips,
			"total_gross": sum(flt(s.gross_pay) for s in slips),
			"total_net": sum(flt(s.net_pay) for s in slips),
		}


@frappe.whitelist()
def disburse_salary_slip(salary_slip: str):
	"""
	Trigger M-Pesa B2C net-pay disbursement for a submitted Salary Slip.
	Delegates to the existing mpesa.disburse_b2c mechanism.
	"""
	slip = frappe.get_doc("Salary Slip", salary_slip)
	if slip.docstatus != 1:
		frappe.throw("Salary Slip must be submitted before disbursement.")

	phone = frappe.db.get_value("Employee", slip.employee, "mpesa_phone")
	from property_management.api.mpesa import disburse_b2c
	return disburse_b2c(amount=flt(slip.net_pay), phone_number=phone)
