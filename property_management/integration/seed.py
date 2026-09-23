# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 7 - ERPNext-native demo seed (no real-data migration).

Provisions two demo companies (organizations), each with properties, tenants,
vendors, employees, and a full sample of every flow wired to ERPNext/HRMS:
invoice -> payment, expense approval, payroll run, and a construction project
with costs. Idempotent-ish: safe to run on a fresh site; re-running creates
additional transactional documents.

Run:
    printf 'from property_management.integration.seed import seed_all\\nseed_all()\\n' > /tmp/seed.py
    bench --site <site> console < /tmp/seed.py
"""

import frappe
from frappe.utils import nowdate, add_days, add_months, get_first_day

from property_management.integration import erpnext_setup as setup
from property_management.integration import invoicing, payments, expenses, payroll, construction


DEMO = [
	{
		"organization": "DADIS Estates Ltd",
		"properties": [
			{"name": "Riverside Apartments", "code": "RIVER"},
			{"name": "Garden Court", "code": "GARDEN"},
		],
		"tenants": ["John Mwangi", "Grace Achieng"],
		"vendors": ["Nairobi Water Co.", "Alpha Security Services"],
		"employees": [
			{"name": "Peter Kamau", "designation": "Caretaker", "gross": 35000, "phone": "254711000001"},
			{"name": "Mary Wanjiku", "designation": "Office Staff", "gross": 80000, "phone": "254711000002"},
		],
	},
	{
		"organization": "Skyline Properties Ltd",
		"properties": [
			{"name": "Skyline Towers", "code": "SKY"},
		],
		"tenants": ["David Otieno"],
		"vendors": ["BuildRite Hardware"],
		"employees": [
			{"name": "James Odhiambo", "designation": "Construction Worker", "gross": 28000, "phone": "254711000003"},
		],
	},
]


def _ensure_org(name):
	if not frappe.db.exists("Organization", name):
		frappe.get_doc({"doctype": "Organization", "organization_name": name, "status": "Active"}).insert(
			ignore_permissions=True
		)


def _ensure_property(org, prop):
	existing = frappe.db.get_value("Property", {"organization": org, "property_name": prop["name"]}, "name")
	if existing:
		return existing
	p = frappe.get_doc({
		"doctype": "Property",
		"organization": org,
		"property_name": prop["name"],
		"property_code": prop["code"],
		"status": "Active",
	})
	p.flags.ignore_mandatory = True
	p.insert(ignore_permissions=True)
	return p.name


def _ensure_tenant(org, prop, tenant_name):
	existing = frappe.db.get_value("Property Tenant", {"tenant_name": tenant_name}, "name")
	if existing:
		return existing
	t = frappe.get_doc({
		"doctype": "Property Tenant",
		"tenant_name": tenant_name,
		"organization": org,
		"property": prop,
	})
	t.flags.ignore_mandatory = True
	t.insert(ignore_permissions=True)
	return t.name


@frappe.whitelist()
def seed_all():
	"""Provision demo companies + properties + a full sample of each flow."""
	frappe.set_user("Administrator")
	setup.bootstrap_global_masters()
	summary = {"companies": [], "invoices": [], "payments": [], "expenses": [], "payroll": [], "construction": []}

	for block in DEMO:
		org = block["organization"]
		_ensure_org(org)
		company = setup.provision_company(org)
		summary["companies"].append(company)

		props = [_ensure_property(org, p) for p in block["properties"]]
		for pr in props:
			setup.provision_property_cost_center(pr)

		first_prop = props[0]
		tenants = [_ensure_tenant(org, first_prop, t) for t in block["tenants"]]

		# 1. Invoice + payment for the first tenant.
		si = invoicing.create_sales_invoice(
			property_name=first_prop, tenant=tenants[0],
			items=[{"item_name": "Monthly Rent", "quantity": 1, "rate": 45000}],
			invoice_type="Rent", submit=True,
		)
		summary["invoices"].append(si)
		pe = payments.create_payment_entry(
			property_name=first_prop, amount=45000, sales_invoice=si,
			tenant=tenants[0], payment_method="M-Pesa", reference_no="SEEDMPESA", submit=True,
		)
		summary["payments"].append(pe)

		# A utility invoice too (routes to Utility Income).
		summary["invoices"].append(invoicing.create_sales_invoice(
			property_name=first_prop, tenant=tenants[0],
			items=[{"item_name": "Water", "quantity": 1, "rate": 2000}],
			invoice_type="Water", submit=True,
		))

		# 2. Expense (posted directly as a Purchase Invoice; maker-checker applies via UI).
		for vendor in block["vendors"]:
			pi = expenses.create_purchase_invoice(
				property_name=first_prop, supplier_name=vendor, amount=25000,
				description=f"{vendor} monthly service", submit=True,
			)
			summary["expenses"].append(pi)

		# 3. Employees + payroll.
		for emp in block["employees"]:
			existing = frappe.db.get_value(
				"Employee", {"employee_name": emp["name"], "organization_ref": org}, "name"
			)
			if not existing:
				payroll.create_employee(
					employee_name=emp["name"], company=company, gross_salary=emp["gross"],
					organization=org, property_name=first_prop, designation=emp["designation"],
					mpesa_phone=emp["phone"], date_of_joining=get_first_day(add_months(nowdate(), -1)),
				)
		summary["payroll"].append(
			payroll.run_payroll(company=company, organization=org, period=nowdate()[:7])
		)

		# 4. Construction project with a couple of costs.
		cp_name = frappe.db.get_value(
			"Construction Project", {"project_name": f"{block['organization']} New Wing"}, "name"
		)
		if not cp_name:
			cp = frappe.get_doc({
				"doctype": "Construction Project",
				"project_name": f"{block['organization']} New Wing",
				"organization": org,
				"property": first_prop,
				"status": "In Progress",
				"budget_lines": [
					{"category": "Materials", "budgeted_amount": 800000, "actual_amount": 0},
					{"category": "Labour", "budgeted_amount": 300000, "actual_amount": 0},
				],
			})
			cp.flags.ignore_mandatory = True
			cp.insert(ignore_permissions=True)
			cp_name = cp.name
		construction.ensure_erpnext_project(cp_name)
		expenses.create_purchase_invoice(
			property_name=first_prop, supplier_name="BuildRite Hardware", amount=150000,
			description="Cement + steel", project=frappe.db.get_value("Construction Project", cp_name, "erpnext_project"),
			submit=True,
		)
		construction.record_worker_wage(cp_name, employee="Casual Crew", amount=45000, description="Foundation labour")
		summary["construction"].append(construction.construction_cost(cp_name))

	frappe.db.commit()
	for k, v in summary.items():
		print(f"{k}: {v if not isinstance(v, list) else len(v)} -> {v}")
	print("SEED_OK")
	return summary
