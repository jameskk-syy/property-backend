# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Custom fields that link property-domain DocTypes to their native ERPNext/HRMS
counterparts. Created idempotently on migrate (see hooks: after_migrate).

We use Custom Fields instead of editing the core doctype JSON so the mapping is
additive and easy to remove during rollback.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


INTEGRATION_CUSTOM_FIELDS = {
	"Property Invoice": [
		{
			"fieldname": "sales_invoice",
			"label": "ERPNext Sales Invoice",
			"fieldtype": "Data",
			"insert_after": "outstanding_amount",
			"read_only": 1,
			"description": "Linked native ERPNext Sales Invoice created by the integration layer.",
		}
	],
	"Property Expense": [
		{
			"fieldname": "purchase_invoice",
			"label": "ERPNext Purchase Invoice",
			"fieldtype": "Data",
			"insert_after": "approved_at",
			"read_only": 1,
		}
	],
	"Construction Purchase": [
		{
			"fieldname": "purchase_invoice",
			"label": "ERPNext Purchase Invoice",
			"fieldtype": "Data",
			"insert_after": "approved_at",
			"read_only": 1,
		}
	],
	"Construction Project": [
		{
			"fieldname": "erpnext_project",
			"label": "ERPNext Project",
			"fieldtype": "Link",
			"options": "Project",
			"insert_after": "status",
			"read_only": 1,
		}
	],
	"Property Payment": [
		{
			"fieldname": "payment_entry",
			"label": "ERPNext Payment Entry",
			"fieldtype": "Data",
			"insert_after": "amount_paid",
			"read_only": 1,
		}
	],
	"Organization": [
		{
			"fieldname": "erpnext_company",
			"label": "ERPNext Company",
			"fieldtype": "Link",
			"options": "Company",
			"insert_after": "status",
			"read_only": 1,
			"description": "Provisioned native ERPNext Company for this organization.",
		}
	],
	"Property": [
		{
			"fieldname": "cost_center",
			"label": "ERPNext Cost Center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"insert_after": "collection_account",
			"read_only": 1,
			"description": "Native Cost Center / accounting dimension for this property.",
		}
	],
	# Tag native transactions back to the property-domain records.
	"Sales Invoice": [
		{
			"fieldname": "property_ref",
			"label": "Property",
			"fieldtype": "Link",
			"options": "Property",
			"insert_after": "cost_center",
		},
		{
			"fieldname": "property_invoice_ref",
			"label": "Property Invoice",
			"fieldtype": "Data",
			"insert_after": "property_ref",
			"read_only": 1,
		},
	],
	"Payment Entry": [
		{
			"fieldname": "property_ref",
			"label": "Property",
			"fieldtype": "Link",
			"options": "Property",
			"insert_after": "cost_center",
		}
	],
	"Purchase Invoice": [
		{
			"fieldname": "property_ref",
			"label": "Property",
			"fieldtype": "Link",
			"options": "Property",
			"insert_after": "cost_center",
		},
		{
			"fieldname": "property_expense_ref",
			"label": "Property Expense",
			"fieldtype": "Data",
			"insert_after": "property_ref",
			"read_only": 1,
		},
	],
	"Employee": [
		{
			"fieldname": "property_ref",
			"label": "Assigned Property",
			"fieldtype": "Link",
			"options": "Property",
			"insert_after": "department",
		},
		{
			"fieldname": "organization_ref",
			"label": "Organization",
			"fieldtype": "Link",
			"options": "Organization",
			"insert_after": "property_ref",
		},
		{
			"fieldname": "gross_salary",
			"label": "Gross Salary (KES)",
			"fieldtype": "Currency",
			"insert_after": "organization_ref",
		},
		{
			"fieldname": "mpesa_phone",
			"label": "M-Pesa Phone",
			"fieldtype": "Data",
			"insert_after": "gross_salary",
		},
		{
			"fieldname": "national_id",
			"label": "National ID",
			"fieldtype": "Data",
			"insert_after": "mpesa_phone",
		},
		{
			"fieldname": "bonus_deposit",
			"label": "Bonus / Deposits (KES)",
			"fieldtype": "Currency",
			"insert_after": "national_id",
		},
	],
	"Project": [
		{
			"fieldname": "property_ref",
			"label": "Property",
			"fieldtype": "Link",
			"options": "Property",
			"insert_after": "company",
		},
		{
			"fieldname": "construction_project_ref",
			"label": "Construction Project",
			"fieldtype": "Data",
			"insert_after": "property_ref",
			"read_only": 1,
		},
	],
}


def setup_integration_custom_fields():
	"""Idempotently create all integration custom fields. Safe to run on every migrate."""
	# Only add native-doctype fields if the target doctype exists (erpnext/hrms installed).
	fields = {dt: defs for dt, defs in INTEGRATION_CUSTOM_FIELDS.items() if frappe.db.exists("DocType", dt)}
	create_custom_fields(fields, ignore_validate=True)
	frappe.db.commit()
