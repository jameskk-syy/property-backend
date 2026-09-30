app_name = "property_management"
app_title = "Property Management"
app_publisher = "DADIS Estates"
app_description = "Multi-Tenant Property Management System with per-apartment accounting isolation and Swagger API docs"
app_email = "admin@dadisestates.com"
app_license = "mit"

# Permissions
# -----------
permission_query_conditions = {
	"Property": "property_management.permission.get_permission_query_conditions",
	"Property Unit": "property_management.permission.get_permission_query_conditions",
	"Property Tenant": "property_management.permission.get_permission_query_conditions",
	"Lease Agreement": "property_management.permission.get_permission_query_conditions",
	"Property Invoice": "property_management.permission.get_permission_query_conditions",
	"Property Payment": "property_management.permission.get_permission_query_conditions",
	"Meter Reading": "property_management.permission.get_permission_query_conditions",
	"WhatsApp Message Log": "property_management.permission.get_permission_query_conditions",
	"Module Def": "property_management.permission.get_permission_query_conditions",
	"Module Permission": "property_management.permission.get_permission_query_conditions",
	"User Property Assignment": "property_management.permission.get_permission_query_conditions",
	"Audit Log": "property_management.permission.get_permission_query_conditions",
	"Expense Vendor": "property_management.permission.get_permission_query_conditions",
	"Property Expense": "property_management.permission.get_permission_query_conditions",
	"Employee": "property_management.permission.get_permission_query_conditions",
	"Tax Template": "property_management.permission.get_permission_query_conditions",
	"Payroll Run": "property_management.permission.get_permission_query_conditions",
	"Payroll Item": "property_management.permission.get_permission_query_conditions",
	"Construction Project": "property_management.permission.get_permission_query_conditions",
	"Project Task": "property_management.permission.get_permission_query_conditions",
	"Construction Purchase": "property_management.permission.get_permission_query_conditions",
	"Ledger Account": "property_management.permission.get_permission_query_conditions",
	"Journal Entry": "property_management.permission.get_permission_query_conditions",
	"Bank Account": "property_management.permission.get_permission_query_conditions",
	"Bank Transaction": "property_management.permission.get_permission_query_conditions",
	"Approval Request": "property_management.permission.get_permission_query_conditions",
	"Prospective Tenant": "property_management.permission.get_permission_query_conditions",
	"Viewing Request": "property_management.permission.get_permission_query_conditions",
	"Contact Access Transaction": "property_management.permission.get_permission_query_conditions",
	"Tenant Complaint": "property_management.permission.get_permission_query_conditions",
	"Held Tenant Item": "property_management.permission.get_permission_query_conditions",
}

# Document Events
# ---------------
doc_events = {
	"*": {
		"before_validate": "property_management.permission.set_default_organization",
		"before_insert": "property_management.permission.set_default_organization",
		"after_insert": "property_management.audit.capture_audit_log",
		"on_update": "property_management.audit.capture_audit_log",
		"on_trash": "property_management.audit.capture_audit_log",
	},
	# Kenyan statutory deductions computed in Python (HRMS safe_eval lacks min/max).
	"Salary Slip": {
		"validate": "property_management.integration.payroll_setup.apply_statutory_deductions",
	},
	# Ensure phone numbers are unique across users
	"User": {
		"validate": "property_management.validation.validate_unique_phone",
	},
}

# Scheduled Tasks
# ---------------
scheduler_events = {
	"daily": [
		"property_management.tasks.daily"
	],
	"monthly": [
		"property_management.tasks.monthly"
	]
}

# Fixtures
# --------
# Committed with the app and imported on every `bench migrate`, so the app's
# roles and integration custom fields are never lost on a reinstall.
fixtures = [
	{
		"dt": "Role",
		"filters": [["name", "in", ["Caretaker", "Landlord", "Tenant",
									 "Organization Admin", "Director", "Office User"]]],
	},
	# Granular access-module catalog (Module Def per key) owned by this app.
	{"dt": "Module Def", "filters": [["app_name", "=", "property_management"]]},
	{
		"dt": "Custom Field",
		"filters": [["name", "in", [
			"Property Invoice-sales_invoice",
			"Property Expense-purchase_invoice",
			"Construction Purchase-purchase_invoice",
			"Construction Project-erpnext_project",
			"Property Payment-payment_entry",
			"Organization-erpnext_company",
			"Property-cost_center",
			"Sales Invoice-property_ref", "Sales Invoice-property_invoice_ref",
			"Payment Entry-property_ref",
			"Purchase Invoice-property_ref", "Purchase Invoice-property_expense_ref",
			"Employee-property_ref", "Employee-organization_ref",
			"Employee-gross_salary", "Employee-mpesa_phone",
			"Employee-national_id", "Employee-bonus_deposit",
			"Employee-apply_deductions",
			"Project-property_ref", "Project-construction_project_ref",
		]]],
	},
]

# Install / Migrate hooks
# -----------------------
# On fresh install: seed the admin-side access modules + role permissions AND
# create the integration custom fields on native ERPNext/HRMS doctypes (which
# exist by the time this app installs, since it depends on erpnext/hrms).
after_install = [
	"property_management.install.setup_site",
]

# On migrate/reinstall: create ERPNext/HRMS integration custom fields AND
# re-seed the access modules (both idempotent).
after_migrate = [
	"property_management.integration.custom_fields.setup_integration_custom_fields",
	"property_management.setup.seed_access_modules",
]
