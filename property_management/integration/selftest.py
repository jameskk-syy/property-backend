# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Self-test helpers for the integration layer, runnable via a piped console script:
    printf 'from property_management.integration.selftest import phase1\\nphase1()\\n' > /tmp/r.py
    bench --site <site> console < /tmp/r.py

These create ZZ-prefixed throwaway records so they are easy to identify and remove.
Not part of the production flow.
"""

import frappe


def reset_payroll_test_artifacts():
	"""Remove test salary structures/assignments so corrected setup recreates them."""
	frappe.set_user("Administrator")
	# Cancel + delete assignments for the test employee.
	emp = frappe.db.get_value("Employee", {"employee_name": "ZZ Test Caretaker"}, "name")
	if emp:
		for ssa in frappe.get_all("Salary Structure Assignment", filters={"employee": emp}, pluck="name"):
			doc = frappe.get_doc("Salary Structure Assignment", ssa)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Salary Structure Assignment", ssa, force=1, ignore_permissions=True)
	# Salary Slips + Payroll Entries for the test company.
	for ss in frappe.get_all("Salary Slip", filters={"company": "ZZ Test Org Ltd"}, pluck="name"):
		doc = frappe.get_doc("Salary Slip", ss)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Salary Slip", ss, force=1, ignore_permissions=True)
	for pe in frappe.get_all("Payroll Entry", filters={"company": "ZZ Test Org Ltd"}, pluck="name"):
		doc = frappe.get_doc("Payroll Entry", pe)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Payroll Entry", pe, force=1, ignore_permissions=True)
	# Cancel + delete test salary structures (names contain 'Structure - ZTOL').
	for ss in frappe.get_all("Salary Structure", filters={"name": ["like", "%- ZTOL"]}, pluck="name"):
		doc = frappe.get_doc("Salary Structure", ss)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Salary Structure", ss, force=1, ignore_permissions=True)
	# Construction test artifacts.
	for cp in frappe.get_all("Construction Purchase", filters={"project": ["like", "ZZ%"]}, pluck="name"):
		frappe.delete_doc("Construction Purchase", cp, force=1, ignore_permissions=True)
	for cp in frappe.get_all("Construction Project", filters={"project_name": ["like", "ZZ%"]}, pluck="name"):
		erp = frappe.db.get_value("Construction Project", cp, "erpnext_project")
		frappe.delete_doc("Construction Project", cp, force=1, ignore_permissions=True)
		if erp and frappe.db.exists("Project", erp):
			# Cancel/delete PIs tagged to the project first.
			for pi in frappe.get_all("Purchase Invoice", filters={"project": erp}, pluck="name"):
				doc = frappe.get_doc("Purchase Invoice", pi)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc("Purchase Invoice", pi, force=1, ignore_permissions=True)
			frappe.delete_doc("Project", erp, force=1, ignore_permissions=True)

	# Fix the Salary Payable account type (HRMS requires it blank).
	sal_payable = frappe.db.get_value("Account", {"account_name": "Salary Payable", "company": "ZZ Test Org Ltd"}, "name")
	if sal_payable:
		frappe.db.set_value("Account", sal_payable, "account_type", None, update_modified=False)
	frappe.db.commit()
	print("reset_done")


def check_direct_slip():
	"""Create a Salary Slip directly to surface any formula/eval error."""
	frappe.set_user("Administrator")
	pe_name = frappe.db.get_value("Payroll Entry", {"company": "ZZ Test Org Ltd"}, "name", order_by="creation desc")
	pe = frappe.get_doc("Payroll Entry", pe_name)
	emp = pe.employees[0].employee
	print("creating slip for", emp, "via PE", pe_name)
	slip = frappe.get_doc({
		"doctype": "Salary Slip",
		"employee": emp,
		"payroll_entry": pe_name,
		"payroll_frequency": pe.payroll_frequency,
		"start_date": pe.start_date,
		"end_date": pe.end_date,
		"company": pe.company,
		"posting_date": pe.posting_date,
		"currency": pe.currency,
	})
	slip.insert(ignore_permissions=True)
	print("slip_created =", slip.name)
	print("gross =", slip.gross_pay, "net =", slip.net_pay)
	print("deductions =", [(d.salary_component, d.amount) for d in slip.deductions])


def check_pe_state():
	"""Inspect the last Payroll Entry: employee rows and any slips."""
	frappe.set_user("Administrator")
	pe_name = frappe.db.get_value("Payroll Entry", {"company": "ZZ Test Org Ltd"}, "name", order_by="creation desc")
	print("payroll_entry =", pe_name)
	if not pe_name:
		return
	pe = frappe.get_doc("Payroll Entry", pe_name)
	print("pe_docstatus =", pe.docstatus)
	print("pe_employee_rows =", len(pe.get("employees") or []))
	print("pe_employees =", [(e.employee, e.employee_name) for e in pe.get("employees") or []][:5])
	slips = frappe.get_all("Salary Slip", filters={"payroll_entry": pe_name}, fields=["name", "docstatus", "net_pay"])
	print("slips_for_pe =", slips)
	allslips = frappe.get_all("Salary Slip", filters={"company": "ZZ Test Org Ltd"}, fields=["name", "payroll_entry", "docstatus"])
	print("all_company_slips =", allslips)


def check_assignment_only():
	"""Isolate: try to create a Salary Structure Assignment and report success/failure."""
	frappe.set_user("Administrator")
	from property_management.integration import payroll, erpnext_setup as setup
	org_name = "ZZ Test Org Ltd"
	company = setup.provision_company(org_name)
	emp = frappe.db.get_value("Employee", {"employee_name": "ZZ Test Caretaker"}, "name")
	print("employee =", emp, "doj =", frappe.db.get_value("Employee", emp, "date_of_joining"))
	try:
		name = payroll.assign_salary_structure(emp, company, 50000.0, "Caretaker", from_date=frappe.db.get_value("Employee", emp, "date_of_joining"))
		frappe.db.commit()
		print("assignment_created =", name)
	except Exception as e:
		print("assignment_error =", repr(e)[:300])
	ssa = frappe.get_all("Salary Structure Assignment", filters={"employee": emp}, fields=["name", "from_date", "base", "docstatus"])
	print("assignments =", ssa)


def check_payroll_debug():
	"""Diagnose why Payroll Entry finds no employees."""
	frappe.set_user("Administrator")
	emp = frappe.db.get_value("Employee", {"employee_name": "ZZ Test Caretaker"}, "name")
	print("employee =", emp)
	print("employee_company =", frappe.db.get_value("Employee", emp, "company"))
	print("employee_status =", frappe.db.get_value("Employee", emp, "status"))
	print("employee_doj =", frappe.db.get_value("Employee", emp, "date_of_joining"))
	ssa = frappe.get_all(
		"Salary Structure Assignment",
		filters={"employee": emp},
		fields=["name", "salary_structure", "from_date", "base", "docstatus", "company"],
	)
	print("assignments =", ssa)


def check_employee_meta():
	"""Report the Employee doctype schema after HRMS install."""
	m = frappe.get_meta("Employee")
	print("reqd =", [f.fieldname for f in m.fields if f.reqd])
	print("has_company =", bool(m.get_field("company")))
	print("has_date_of_joining =", bool(m.get_field("date_of_joining")))
	print("has_date_of_birth =", bool(m.get_field("date_of_birth")))
	print("has_organization =", bool(m.get_field("organization")))
	dfield = m.get_field("designation")
	print("designation_fieldtype =", dfield.fieldtype if dfield else None)
	print("module =", m.module)


def check_openapi():
	"""Validate the OpenAPI spec still generates and includes v2 endpoints."""
	import json
	from property_management.api.docs import get_openapi_spec
	s = get_openapi_spec()
	v2 = [p for p in s["paths"] if "api.v2" in p]
	print("openapi =", s["openapi"])
	print("v2_paths =", len(v2))
	print("json_ok =", bool(json.dumps(s)))


def check_site():
	"""Report which setup-wizard masters exist on the site."""
	print("setup_complete =", frappe.db.get_single_value("System Settings", "setup_complete"))
	print("fiscal_years =", frappe.get_all("Fiscal Year", pluck="name"))
	print("uom_nos =", frappe.db.exists("UOM", "Nos"))
	print("customer_groups =", frappe.get_all("Customer Group", pluck="name"))
	print("territories =", frappe.get_all("Territory", pluck="name"))
	print("companies =", frappe.get_all("Company", pluck="name"))


def phase1():
	frappe.set_user("Administrator")

	from property_management.integration import erpnext_setup as setup
	from property_management.integration.custom_fields import setup_integration_custom_fields

	out = {}
	setup_integration_custom_fields()
	out["cf_org_company"] = bool(
		frappe.db.exists("Custom Field", {"dt": "Organization", "fieldname": "erpnext_company"})
	)
	out["cf_prop_cc"] = bool(
		frappe.db.exists("Custom Field", {"dt": "Property", "fieldname": "cost_center"})
	)

	org_name = "ZZ Test Org Ltd"
	if not frappe.db.exists("Organization", org_name):
		frappe.get_doc(
			{"doctype": "Organization", "organization_name": org_name, "status": "Active"}
		).insert(ignore_permissions=True)

	company = setup.provision_company(org_name)
	out["company"] = company
	out["company_currency"] = frappe.db.get_value("Company", company, "default_currency")
	out["org_link"] = frappe.db.get_value("Organization", org_name, "erpnext_company")

	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)
	if not prop_name:
		p = frappe.get_doc(
			{
				"doctype": "Property",
				"organization": org_name,
				"property_name": "ZZ Test Block A",
				"property_code": "ZZTBA",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		prop_name = p.name

	cc = setup.provision_property_cost_center(prop_name)
	out["cost_center"] = cc
	out["prop_link"] = frappe.db.get_value("Property", prop_name, "cost_center")
	out["cc_company"] = frappe.db.get_value("Cost Center", cc, "company")

	out["default_receivable"] = setup.get_default_account(company, "Receivable")
	out["default_income"] = setup.get_default_account(company, "Income Account")
	out["default_expense"] = setup.get_default_account(company, "Expense Account")

	frappe.db.commit()
	for k, v in out.items():
		print(f"{k} = {v}")
	print("PHASE1_OK")
	return out


def phase2():
	"""Verify invoicing bridge: create a submitted Sales Invoice that posts to GL."""
	frappe.set_user("Administrator")
	from property_management.integration import invoicing

	org_name = "ZZ Test Org Ltd"
	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)
	assert prop_name, "run phase1 first"

	tenant_name = "ZZ Test Tenant"
	tenant = frappe.db.get_value("Property Tenant", {"tenant_name": tenant_name}, "name")
	if not tenant:
		t = frappe.get_doc({
			"doctype": "Property Tenant",
			"tenant_name": tenant_name,
			"organization": org_name,
			"property": prop_name,
		})
		t.flags.ignore_mandatory = True
		t.insert(ignore_permissions=True)
		tenant = t.name

	si_name = invoicing.create_sales_invoice(
		property_name=prop_name,
		tenant=tenant,
		items=[{"item_name": "Monthly Rent", "quantity": 1, "rate": 25000}],
		invoice_type="Rent",
		submit=True,
	)
	si = frappe.get_doc("Sales Invoice", si_name)
	print("sales_invoice =", si_name)
	print("docstatus =", si.docstatus)
	print("grand_total =", si.grand_total)
	print("company =", si.company)
	print("cost_center =", si.cost_center)
	print("property_ref =", si.get("property_ref"))
	print("income_account =", si.items[0].income_account)

	gl_count = frappe.db.count("GL Entry", {"voucher_type": "Sales Invoice", "voucher_no": si_name})
	print("gl_entry_count =", gl_count)

	si2 = invoicing.create_sales_invoice(
		property_name=prop_name,
		tenant=tenant,
		items=[{"item_name": "Water", "quantity": 1, "rate": 1500}],
		invoice_type="Water",
		submit=True,
	)
	util_acc = frappe.db.get_value("Sales Invoice Item", {"parent": si2}, "income_account")
	print("utility_income_account =", util_acc)

	assert si.docstatus == 1
	assert gl_count >= 2
	assert "Rent" in si.items[0].income_account
	assert "Utility" in util_acc
	frappe.db.commit()
	print("PHASE2_OK")


def phase2_bridge():
	"""Verify a Property Invoice auto-creates a linked Sales Invoice via on_update."""
	frappe.set_user("Administrator")
	from property_management.integration.custom_fields import setup_integration_custom_fields
	setup_integration_custom_fields()

	org_name = "ZZ Test Org Ltd"
	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)
	tenant = frappe.db.get_value("Property Tenant", {"tenant_name": "ZZ Test Tenant"}, "name")
	assert prop_name and tenant, "run phase1 + phase2 first"

	pi = frappe.get_doc({
		"doctype": "Property Invoice",
		"organization": org_name,
		"property": prop_name,
		"tenant": tenant,
		"invoice_type": "Rent",
		"posting_date": frappe.utils.nowdate(),
		"due_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
		"items": [{"item_name": "Monthly Rent", "quantity": 1, "rate": 30000}],
	})
	pi.flags.ignore_mandatory = True
	pi.insert(ignore_permissions=True)

	pi.reload()
	print("property_invoice =", pi.name)
	print("linked_sales_invoice =", pi.get("sales_invoice"))
	assert pi.get("sales_invoice"), "Property Invoice did not bridge to a Sales Invoice"
	si = frappe.get_doc("Sales Invoice", pi.get("sales_invoice"))
	print("si_docstatus =", si.docstatus)
	print("si_grand_total =", si.grand_total)
	print("si_property_invoice_ref =", si.get("property_invoice_ref"))
	assert si.docstatus == 1
	assert si.get("property_invoice_ref") == pi.name
	frappe.db.commit()
	print("PHASE2_BRIDGE_OK")


def phase3():
	"""Verify payment bridge: Property Payment -> Payment Entry allocated to Sales Invoice, GL posts, cancel reverses."""
	frappe.set_user("Administrator")
	from property_management.integration.custom_fields import setup_integration_custom_fields
	from property_management.integration import invoicing
	setup_integration_custom_fields()

	org_name = "ZZ Test Org Ltd"
	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)
	tenant = frappe.db.get_value("Property Tenant", {"tenant_name": "ZZ Test Tenant"}, "name")
	assert prop_name and tenant, "run phase1/phase2 first"

	# Fresh Property Invoice (auto-bridges to a Sales Invoice).
	pi = frappe.get_doc({
		"doctype": "Property Invoice",
		"organization": org_name,
		"property": prop_name,
		"tenant": tenant,
		"invoice_type": "Rent",
		"posting_date": frappe.utils.nowdate(),
		"due_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
		"items": [{"item_name": "Monthly Rent", "quantity": 1, "rate": 20000}],
	})
	pi.flags.ignore_mandatory = True
	pi.insert(ignore_permissions=True)
	pi.reload()
	si_name = pi.get("sales_invoice")
	print("property_invoice =", pi.name, "sales_invoice =", si_name)
	si_before = frappe.db.get_value("Sales Invoice", si_name, "outstanding_amount")
	print("si_outstanding_before =", si_before)

	# Pay it via a Property Payment (should bridge to a Payment Entry).
	pp = frappe.get_doc({
		"doctype": "Property Payment",
		"organization": org_name,
		"property": prop_name,
		"invoice": pi.name,
		"tenant": tenant,
		"payment_date": frappe.utils.nowdate(),
		"payment_method": "M-Pesa",
		"transaction_reference": "ZZTESTMPESA1",
		"amount_paid": 20000,
	})
	pp.flags.ignore_mandatory = True
	pp.insert(ignore_permissions=True)
	pp.submit()
	pp.reload()

	pe_name = pp.get("payment_entry")
	print("payment_entry =", pe_name)
	assert pe_name, "Property Payment did not bridge to a Payment Entry"
	pe = frappe.get_doc("Payment Entry", pe_name)
	print("pe_docstatus =", pe.docstatus)
	print("pe_paid_amount =", pe.paid_amount)
	print("pe_party =", pe.party)
	print("pe_property_ref =", pe.get("property_ref"))
	print("pe_allocations =", [(r.reference_doctype, r.reference_name, r.allocated_amount) for r in pe.references])

	gl_count = frappe.db.count("GL Entry", {"voucher_type": "Payment Entry", "voucher_no": pe_name})
	print("pe_gl_entry_count =", gl_count)
	si_after = frappe.db.get_value("Sales Invoice", si_name, "outstanding_amount")
	print("si_outstanding_after =", si_after)

	assert pe.docstatus == 1
	assert gl_count >= 2
	assert flt(si_after) < flt(si_before)

	# Cancel the Property Payment -> should cancel the Payment Entry (reverse GL).
	pp.cancel()
	pe.reload()
	print("pe_docstatus_after_cancel =", pe.docstatus)
	assert pe.docstatus == 2, "Payment Entry was not cancelled/reversed"

	frappe.db.commit()
	print("PHASE3_OK")


# flt helper import for the assertions above
from frappe.utils import flt  # noqa: E402


def phase4():
	"""Verify expense bridge + maker-checker rule.

	1. Creator cannot approve their own expense (maker-checker) -> must raise.
	2. A different approver -> approves, posts a native Purchase Invoice with GL.
	"""
	frappe.set_user("Administrator")
	from property_management.integration.custom_fields import setup_integration_custom_fields
	setup_integration_custom_fields()

	org_name = "ZZ Test Org Ltd"
	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)
	assert prop_name, "run phase1 first"

	# Two throwaway users: a maker (creator) and a checker (approver).
	maker = "zz_maker@example.com"
	checker = "zz_checker@example.com"
	for u in (maker, checker):
		if not frappe.db.exists("User", u):
			frappe.get_doc({
				"doctype": "User", "email": u, "first_name": u.split("@")[0],
				"send_welcome_email": 0, "roles": [{"role": "System Manager"}],
			}).insert(ignore_permissions=True)

	# --- Maker creates an expense (owner = maker) ---
	frappe.set_user(maker)
	exp = frappe.get_doc({
		"doctype": "Property Expense",
		"organization": org_name,
		"property": prop_name,
		"vendor_name_manual": "ZZ Test Plumber",
		"expense_category": "Plumbing",
		"amount": 7500,
		"work_description": "Fix burst pipe in Block A",
		"status": "Pending Approval",
	})
	exp.insert(ignore_permissions=True)
	print("expense =", exp.name, "owner =", exp.owner)

	# --- Maker-checker: creator tries to approve own expense -> must fail ---
	maker_blocked = False
	try:
		exp.approve(user=maker)
	except frappe.ValidationError as e:
		maker_blocked = True
		print("maker_self_approve_blocked =", True, "| msg:", str(e)[:60])
	assert maker_blocked, "MAKER-CHECKER FAILED: creator was able to approve own expense!"

	# --- Checker (different user) approves -> bridges to Purchase Invoice ---
	frappe.set_user(checker)
	exp.reload()
	exp.approve(user=checker)
	exp.reload()
	print("status =", exp.status)
	print("approved_by =", exp.approved_by)
	print("purchase_invoice =", exp.get("purchase_invoice"))
	assert exp.status == "Approved"
	assert exp.approved_by == checker
	assert exp.get("purchase_invoice"), "expense did not bridge to a Purchase Invoice"

	frappe.set_user("Administrator")
	pi = frappe.get_doc("Purchase Invoice", exp.get("purchase_invoice"))
	print("pi_docstatus =", pi.docstatus)
	print("pi_grand_total =", pi.grand_total)
	print("pi_supplier =", pi.supplier)
	print("pi_cost_center =", pi.cost_center)
	print("pi_property_ref =", pi.get("property_ref"))
	gl_count = frappe.db.count("GL Entry", {"voucher_type": "Purchase Invoice", "voucher_no": pi.name})
	print("pi_gl_entry_count =", gl_count)
	assert pi.docstatus == 1
	assert flt(pi.grand_total) == 7500.0
	assert gl_count >= 2

	frappe.db.commit()
	print("PHASE4_OK")


def phase5():
	"""Verify HRMS payroll: run payroll, check a slip's statutory deductions vs the reference calc."""
	frappe.set_user("Administrator")
	from property_management.integration import payroll, payroll_setup
	from property_management.integration import erpnext_setup as setup

	org_name = "ZZ Test Org Ltd"
	company = setup.provision_company(org_name)
	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)

	# Reference computation for a known gross.
	gross = 50000.0
	ref = payroll_setup.compute_statutory(gross)
	print("reference =", ref)

	# Ensure designations exist (HRMS makes Employee.designation a Link).
	payroll.ensure_designations()

	# Create an active caretaker employee with that gross (HRMS Employee).
	emp_name = "ZZ Test Caretaker"
	emp = frappe.db.get_value(
		"Employee", {"employee_name": emp_name, "organization_ref": org_name}, "name"
	)
	if not emp:
		emp = payroll.create_employee(
			employee_name=emp_name,
			company=company,
			gross_salary=gross,
			organization=org_name,
			property_name=prop_name,
			designation="Caretaker",
			mpesa_phone="254700000000",
		)
	print("employee =", emp)

	result = payroll.run_payroll(company=company, organization=org_name, period=frappe.utils.nowdate()[:7], property_name=prop_name)
	print("payroll_status =", result.get("status"))
	print("payroll_entry =", result.get("payroll_entry"))
	print("slips =", result.get("slips"))

	# Find our employee's slip.
	slip_name = frappe.db.get_value(
		"Salary Slip", {"employee": emp, "payroll_entry": result.get("payroll_entry")}, "name"
	)
	assert slip_name, "no salary slip generated for the test employee"
	slip = frappe.get_doc("Salary Slip", slip_name)
	print("slip =", slip_name, "docstatus =", slip.docstatus)
	print("gross_pay =", slip.gross_pay, "total_deduction =", slip.total_deduction, "net_pay =", slip.net_pay)

	ded = {d.salary_component: flt(d.amount) for d in slip.deductions}
	print("deductions =", ded)

	# Compare each statutory line to the reference (within 1 KES for rounding).
	def close(a, b):
		return abs(flt(a) - flt(b)) <= 1.0

	assert close(ded.get("NSSF"), ref["nssf"]), f"NSSF {ded.get('NSSF')} != {ref['nssf']}"
	assert close(ded.get("SHIF"), ref["shif"]), f"SHIF {ded.get('SHIF')} != {ref['shif']}"
	assert close(ded.get("Housing Levy"), ref["housing_levy"]), f"HL {ded.get('Housing Levy')} != {ref['housing_levy']}"
	assert close(ded.get("PAYE"), ref["paye"]), f"PAYE {ded.get('PAYE')} != {ref['paye']}"
	assert close(slip.net_pay, ref["net"]), f"net {slip.net_pay} != {ref['net']}"

	# Salary Slip should have posted to the GL on submit.
	gl_count = frappe.db.count("GL Entry", {"voucher_type": "Journal Entry", "against_voucher": slip_name}) \
		or frappe.db.count("GL Entry", {"voucher_no": slip_name})
	print("slip_gl_entries (best-effort) =", gl_count)

	frappe.db.commit()
	print("PHASE5_OK")


def phase6():
	"""Verify construction costing: materials (PI) + wages (JE) roll up per ERPNext Project."""
	frappe.set_user("Administrator")
	from property_management.integration.custom_fields import setup_integration_custom_fields
	from property_management.integration import construction
	setup_integration_custom_fields()

	org_name = "ZZ Test Org Ltd"
	prop_name = frappe.db.get_value(
		"Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name"
	)
	assert prop_name, "run phase1 first"

	# Construction project (auto-bridges to an ERPNext Project via after_insert).
	cp = frappe.get_doc({
		"doctype": "Construction Project",
		"project_name": "ZZ Test Build",
		"organization": org_name,
		"property": prop_name,
		"status": "In Progress",
		"budget_lines": [
			{"category": "Materials", "budgeted_amount": 500000, "actual_amount": 0},
			{"category": "Labour", "budgeted_amount": 200000, "actual_amount": 0},
		],
	})
	cp.flags.ignore_mandatory = True
	cp.insert(ignore_permissions=True)
	cp.reload()
	print("construction_project =", cp.name)
	print("erpnext_project =", cp.get("erpnext_project"))
	assert cp.get("erpnext_project"), "did not bridge to an ERPNext Project"

	# Materials purchase via a Construction Purchase (approved by a checker -> PI tagged to project).
	maker = "zz_maker@example.com"
	checker = "zz_checker@example.com"

	frappe.set_user(maker)
	purchase = frappe.get_doc({
		"doctype": "Construction Purchase",
		"organization": org_name,
		"project": cp.name,
		"property": prop_name,
		"category": "Materials",
		"item_description": "Cement 100 bags",
		"amount": 120000,
		"status": "Pending Approval",
	})
	purchase.flags.ignore_mandatory = True
	purchase.insert(ignore_permissions=True)

	# Maker cannot approve own purchase.
	blocked = False
	try:
		purchase.approve(user=maker)
	except frappe.ValidationError:
		blocked = True
	assert blocked, "MAKER-CHECKER FAILED on construction purchase!"

	frappe.set_user(checker)
	purchase.reload()
	purchase.approve(user=checker)
	purchase.reload()
	print("construction_purchase_pi =", purchase.get("purchase_invoice"))
	assert purchase.get("purchase_invoice"), "construction purchase did not bridge to a PI"

	# Worker wage via JE tagged to project.
	frappe.set_user("Administrator")
	je = construction.record_worker_wage(cp.name, employee="Casual Mason", amount=15000, description="Week 1 labour")
	print("wage_journal =", je)

	# Roll up cost.
	cost = construction.construction_cost(cp.name)
	print("cost =", cost)
	assert cost["source"] == "erpnext_project"
	# Both materials (120k) and casual wage (15k) post as Purchase Invoices tagged to the project.
	assert flt(cost["breakdown"]["materials_and_expenses (Purchase Invoices)"]) == 135000.0
	assert flt(cost["total_actual"]) == 135000.0
	assert flt(cost["total_budget"]) == 700000.0

	frappe.db.commit()
	print("PHASE6_OK")


def phase8():
	"""Verify api/v2 endpoints return the standard envelope and pull from native GL."""
	frappe.set_user("Administrator")
	from property_management.api.v2 import finance, reports, payroll as v2payroll

	company = "DADIS Estates Ltd"
	prop = frappe.db.get_value("Property", {"organization": company, "property_name": "Riverside Apartments"}, "name")
	print("company =", company, "property =", prop)

	# Statutory preview (pure calc).
	prev = v2payroll.preview_statutory(50000)
	print("preview_statutory =", prev["status"], prev["data"])
	assert prev["status"] == "success"
	assert prev["data"]["net"] == 38391.65

	# P&L from GL (seed created income + expense).
	pl = reports.profit_and_loss(company=company)
	print("pl_status =", pl["status"])
	print("pl_income =", pl["data"]["total_income"], "pl_expense =", pl["data"]["total_expense"], "net =", pl["data"]["net_profit"])
	assert pl["status"] == "success"
	assert pl["data"]["total_income"] > 0

	# Trial balance.
	tb = reports.trial_balance(company=company)
	print("tb_status =", tb["status"], "accounts =", len(tb["data"]))
	assert tb["status"] == "success" and len(tb["data"]) > 0

	# Landlord remittance.
	rem = reports.landlord_remittance(company=company)
	print("remittance =", rem["data"])
	assert rem["status"] == "success"

	# List invoices.
	inv = finance.list_invoices(company=company)
	print("invoices_listed =", len(inv["data"]))
	assert inv["status"] == "success"

	# Create an invoice through the API contract.
	tenant = frappe.db.get_value("Property Tenant", {"tenant_name": "John Mwangi"}, "name")
	created = finance.create_invoice(property=prop, tenant=tenant,
		items=[{"item_name": "Service Charge", "quantity": 1, "rate": 5000}], invoice_type="Rent")
	print("create_invoice =", created["status"], created["data"])
	assert created["status"] == "success" and created["data"]["grand_total"] == 5000.0

	print("PHASE8_OK")


def phase9():
	"""Verify money endpoints reject Guest, allow authenticated, and maker-checker holds."""
	import inspect
	# Import all API modules so their whitelist decorators register in guest_methods.
	from property_management.api import accounting, reports, mpesa  # noqa: F401

	# 1. No money endpoint in accounting/reports should be guest-accessible.
	def is_guest(fn):
		wl = getattr(fn, "__func__", fn)
		# frappe stores whitelist flags on the function via frappe.whitelisted set;
		# check the source instead for allow_guest.
		src = inspect.getsource(fn)
		return "allow_guest=True" in src.splitlines()[0] if src else False

	guest_funcs = []
	for mod in (accounting, reports):
		for name, obj in inspect.getmembers(mod, inspect.isfunction):
			if obj.__module__ == mod.__name__:
				# Inspect the decorator line just above def.
				try:
					src = inspect.getsource(obj)
				except Exception:
					continue
				if "allow_guest=True" in src:
					guest_funcs.append(f"{mod.__name__}.{name}")
	print("guest_money_endpoints =", guest_funcs)
	assert not guest_funcs, f"These money endpoints are still guest-accessible: {guest_funcs}"

	# 2. Confirm the money endpoints are NOT in Frappe's guest-allowed whitelist.
	#    (The HTTP request layer enforces this using frappe.whitelisted[guest]; a
	#    console session bypasses that layer, so we assert the registration instead.)
	# Compare by fully-qualified name (decorators can rebind object identity).
	guest_names = {f"{m.__module__}.{m.__name__}" for m in frappe.guest_methods}
	money_names = {
		"property_management.api.accounting.get_trial_balance",
		"property_management.api.accounting.get_profit_and_loss",
		"property_management.api.accounting.post_journal_entry",
		"property_management.api.reports.landlord_remittance_report",
	}
	leaked = sorted(money_names & guest_names)
	print("leaked_into_guest_whitelist =", leaked)
	assert not leaked, f"Money endpoints leaked into guest whitelist: {leaked}"

	# Positive control: the M-Pesa webhook SHOULD still be guest-accessible.
	mpesa_guest = "property_management.api.mpesa.c2b_callback" in guest_names
	print("mpesa_c2b_still_guest =", mpesa_guest)
	assert mpesa_guest, "M-Pesa webhook lost guest access (gateways can't call it)!"

	# 3. Maker-checker still enforced (re-affirm on Property Expense).
	org_name = "ZZ Test Org Ltd"
	prop_name = frappe.db.get_value("Property", {"organization": org_name, "property_name": "ZZ Test Block A"}, "name")
	maker = "zz_maker@example.com"
	exp = frappe.get_doc({
		"doctype": "Property Expense", "organization": org_name, "property": prop_name,
		"vendor_name_manual": "ZZ Sec Vendor", "expense_category": "General",
		"amount": 1000, "work_description": "Security check", "status": "Pending Approval",
	})
	frappe.set_user(maker)
	exp.insert(ignore_permissions=True)
	mc = False
	try:
		exp.approve(user=maker)
	except frappe.ValidationError:
		mc = True
	frappe.set_user("Administrator")
	print("maker_checker_enforced =", mc)
	assert mc, "Maker-checker regression!"

	print("PHASE9_OK")


def time_endpoints():
	"""Time the key report endpoints to find bottlenecks."""
	import time
	frappe.set_user("Administrator")
	from property_management.api import reports, accounting

	calls = [
		("get_properties_summary", lambda: reports.get_properties_summary()),
		("get_dashboard_summary", lambda: reports.get_dashboard_summary()),
		("rent_collection_report", lambda: reports.rent_collection_report()),
		("rent_arrears_report", lambda: reports.rent_arrears_report()),
		("revenue_and_expense_trend", lambda: reports.revenue_and_expense_trend()),
		("landlord_remittance_report", lambda: reports.landlord_remittance_report()),
		("expense_report", lambda: reports.expense_report()),
		("profit_and_loss", lambda: reports.profit_and_loss()),
		("get_trial_balance", lambda: accounting.get_trial_balance()),
	]
	for name, fn in calls:
		t0 = time.time()
		try:
			fn()
			dt = (time.time() - t0) * 1000
			print(f"{name}: {dt:.0f} ms")
		except Exception as e:
			print(f"{name}: ERROR {str(e)[:60]}")
