# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 1 - Accounting foundation.

Provisions native ERPNext structures for the property domain:
  - Company per Organization (KES, Kenya chart of accounts)
  - Cost Center per Property (used as the "Property" accounting dimension)
  - Default account resolution helpers (income/expense/receivable/bank)

All functions are idempotent so they can be re-run safely.
"""

import frappe
from frappe.utils import cstr

from property_management.integration.settings import DEFAULT_CURRENCY, COUNTRY


def _company_abbr(name: str) -> str:
	"""Short, unique-ish abbreviation for a company from its name."""
	letters = [w[0] for w in cstr(name).split() if w]
	abbr = "".join(letters).upper()[:5] or cstr(name)[:5].upper()
	return abbr


def bootstrap_global_masters():
	"""
	Create the global ERPNext masters that the setup wizard would normally create,
	so financial documents work on a site where the wizard was never completed.
	Idempotent. Covers: Fiscal Year, UOM 'Nos', Customer Group tree, Territory tree,
	Warehouse Type 'Transit'.
	"""
	from frappe.utils import getdate

	# Warehouse Type (needed by Company default warehouse creation).
	if frappe.db.exists("DocType", "Warehouse Type") and not frappe.db.exists("Warehouse Type", "Transit"):
		frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)

	# UOM
	if not frappe.db.exists("UOM", "Nos"):
		frappe.get_doc({"doctype": "UOM", "uom_name": "Nos", "must_be_whole_number": 1}).insert(ignore_permissions=True)

	# Fiscal Year covering the current calendar year (Kenya uses Jan-Dec by default).
	year = getdate().year
	fy_name = str(year)
	if not frappe.db.exists("Fiscal Year", fy_name):
		fy = frappe.get_doc({
			"doctype": "Fiscal Year",
			"year": fy_name,
			"year_start_date": f"{year}-01-01",
			"year_end_date": f"{year}-12-31",
		})
		fy.flags.ignore_mandatory = True
		fy.insert(ignore_permissions=True)

	# Customer Group tree
	if not frappe.db.exists("Customer Group", "All Customer Groups"):
		frappe.get_doc({
			"doctype": "Customer Group",
			"customer_group_name": "All Customer Groups",
			"is_group": 1,
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Customer Group", "Tenants"):
		frappe.get_doc({
			"doctype": "Customer Group",
			"customer_group_name": "Tenants",
			"parent_customer_group": "All Customer Groups",
			"is_group": 0,
		}).insert(ignore_permissions=True)

	# Territory tree
	if not frappe.db.exists("Territory", "All Territories"):
		frappe.get_doc({
			"doctype": "Territory",
			"territory_name": "All Territories",
			"is_group": 1,
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Territory", "Kenya"):
		frappe.get_doc({
			"doctype": "Territory",
			"territory_name": "Kenya",
			"parent_territory": "All Territories",
			"is_group": 0,
		}).insert(ignore_permissions=True)

	# Supplier Group tree (used later by expenses/vendors).
	if not frappe.db.exists("Supplier Group", "All Supplier Groups"):
		frappe.get_doc({
			"doctype": "Supplier Group",
			"supplier_group_name": "All Supplier Groups",
			"is_group": 1,
		}).insert(ignore_permissions=True)
	if not frappe.db.exists("Supplier Group", "Vendors"):
		frappe.get_doc({
			"doctype": "Supplier Group",
			"supplier_group_name": "Vendors",
			"parent_supplier_group": "All Supplier Groups",
			"is_group": 0,
		}).insert(ignore_permissions=True)

	frappe.db.commit()


def _ensure_prerequisite_masters():
	"""Backwards-compatible alias; delegates to the full bootstrap."""
	bootstrap_global_masters()


def provision_company(organization: str) -> str:
	"""
	Create (or return existing) ERPNext Company for an Organization and link it.
	Idempotent.
	"""
	org = frappe.get_doc("Organization", organization)
	company_name = org.organization_name

	if frappe.db.exists("Company", company_name):
		company = company_name
	else:
		_ensure_prerequisite_masters()
		company_doc = frappe.get_doc({
			"doctype": "Company",
			"company_name": company_name,
			"abbr": _company_abbr(company_name),
			"default_currency": DEFAULT_CURRENCY,
			"country": COUNTRY,
			"create_chart_of_accounts_based_on": "Standard Template",
		})
		company_doc.insert(ignore_permissions=True)
		company = company_doc.name

	# Link back onto the Organization (custom field).
	if org.get("erpnext_company") != company:
		org.db_set("erpnext_company", company, update_modified=False)

	frappe.db.commit()
	return company


def provision_property_cost_center(property_name: str) -> str:
	"""
	Create (or return existing) a Cost Center for a Property under its Company,
	and link it back on the Property. Idempotent.
	"""
	prop = frappe.get_doc("Property", property_name)
	company = require_company_for_property(prop)
	abbr = frappe.get_cached_value("Company", company, "abbr")

	cc_name = f"{prop.property_name} - {abbr}"
	if frappe.db.exists("Cost Center", cc_name):
		cost_center = cc_name
	else:
		# Parent is the company's root cost center group.
		parent = frappe.db.get_value(
			"Cost Center",
			{"company": company, "is_group": 1},
			"name",
			order_by="lft asc",
		)
		cc_doc = frappe.get_doc({
			"doctype": "Cost Center",
			"cost_center_name": prop.property_name,
			"parent_cost_center": parent,
			"company": company,
			"is_group": 0,
		})
		cc_doc.insert(ignore_permissions=True)
		cost_center = cc_doc.name

	if prop.get("cost_center") != cost_center:
		prop.db_set("cost_center", cost_center, update_modified=False)

	frappe.db.commit()
	return cost_center


def require_company_for_property(prop) -> str:
	"""Resolve the Company for a Property via its Organization, provisioning if needed."""
	if isinstance(prop, str):
		prop = frappe.get_doc("Property", prop)
	organization = prop.organization
	if not organization:
		frappe.throw(f"Property '{prop.name}' has no organization; cannot resolve Company.")
	from property_management.integration.settings import company_for_organization
	company = company_for_organization(organization)
	if not company:
		company = provision_company(organization)
	return company


# --- Default account resolution -------------------------------------------------

def get_default_account(company: str, account_type: str) -> str | None:
	"""
	Return a sensible default account for a company by ERPNext account_type.
	account_type e.g. 'Receivable', 'Bank', 'Income Account', 'Expense Account',
	'Payable'.
	"""
	# Company-level defaults first.
	company_defaults = {
		"Receivable": "default_receivable_account",
		"Payable": "default_payable_account",
		"Income Account": "default_income_account",
		"Expense Account": "default_expense_account",
		"Bank": "default_bank_account",
		"Cash": "default_cash_account",
	}
	field = company_defaults.get(account_type)
	if field:
		acc = frappe.get_cached_value("Company", company, field)
		if acc:
			return acc

	# Fallback: first non-group account of that type.
	return frappe.db.get_value(
		"Account",
		{"company": company, "account_type": account_type, "is_group": 0},
		"name",
	)


def ensure_income_accounts(company: str) -> dict:
	"""
	Ensure dedicated 'Rent Income' and 'Utility Income' accounts exist under the
	company's income group, so rent vs utility revenue is tracked separately.
	Returns {'Rent': <acc>, 'Utility': <acc>}. Idempotent.
	"""
	abbr = frappe.get_cached_value("Company", company, "abbr")

	# Find an income parent group (e.g. "Income" / "Direct Income").
	parent = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Income"},
		"name",
		order_by="lft asc",
	)

	result = {}
	for label in ("Rent Income", "Utility Income"):
		acc_name = f"{label} - {abbr}"
		if not frappe.db.exists("Account", acc_name):
			frappe.get_doc({
				"doctype": "Account",
				"account_name": label,
				"parent_account": parent,
				"company": company,
				"root_type": "Income",
				"account_type": "Income Account",
				"is_group": 0,
			}).insert(ignore_permissions=True)
		result["Rent" if label.startswith("Rent") else "Utility"] = acc_name

	frappe.db.commit()
	return result


def _indirect_expense_group(company: str) -> str | None:
	"""
	Return the 'Indirect Expenses' group account for a company, creating it under
	the Expense root if the standard chart of accounts didn't include it. All
	category-driven expense heads are parented here so they read as indirect
	(operating) expenses in the P&L. Idempotent.
	"""
	# 1) An existing group literally named "Indirect Expenses".
	group = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Expense", "account_name": "Indirect Expenses"},
		"name",
	)
	if group:
		return group

	# 2) The Expense root group (top of the expense tree) to parent a new group under.
	root = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Expense"},
		"name",
		order_by="lft asc",
	)
	if not root:
		return None

	# 3) Create the "Indirect Expenses" group under the expense root.
	abbr = frappe.get_cached_value("Company", company, "abbr")
	group_name = f"Indirect Expenses - {abbr}"
	if not frappe.db.exists("Account", group_name):
		frappe.get_doc({
			"doctype": "Account",
			"account_name": "Indirect Expenses",
			"parent_account": root,
			"company": company,
			"root_type": "Expense",
			"is_group": 1,
		}).insert(ignore_permissions=True)
		frappe.db.commit()
	return group_name


def ensure_deposit_liability_account(company: str) -> str | None:
	"""
	Ensure a 'Tenant Security Deposits' liability account exists under the company's
	Liabilities tree. Refundable deposits are posted here (a liability we owe back
	to the tenant), NOT to income. Returns the account name. Idempotent.
	"""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	acc_name = f"Tenant Security Deposits - {abbr}"
	if frappe.db.exists("Account", acc_name):
		return acc_name

	# Parent: a current-liabilities group, else the Liability root group.
	parent = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Liability",
		 "account_name": ["in", ["Current Liabilities", "Accounts Payable"]]},
		"name",
	) or frappe.db.get_value(
		"Account", {"company": company, "is_group": 1, "root_type": "Liability"},
		"name", order_by="lft asc",
	)
	if not parent:
		return None

	frappe.get_doc({
		"doctype": "Account",
		"account_name": "Tenant Security Deposits",
		"parent_account": parent,
		"company": company,
		"root_type": "Liability",
		# NOTE: intentionally NOT 'Payable' — a Payable/Receivable account forces a
		# Party on every GL line. A deposit-held control account is a plain liability
		# so the onboarding Journal Entry can credit it without a Customer/Supplier.
		"account_type": "",
		"is_group": 0,
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return acc_name


def get_expense_account_for_category(company: str, category: str | None) -> str | None:
	"""
	Resolve (creating if needed) the expense account for an Expense Category within
	a specific company's chart of accounts. This is what makes an expense category
	an *accounting* category: each category maps to a dedicated expense head, and
	because it is resolved per company it respects multi-org tenancy.

	Falls back to the company default Expense Account when no category is given or
	the category has no account label.
	"""
	if not category:
		return get_default_account(company, "Expense Account")

	account_label = frappe.db.get_value("Expense Category", category, "account_name") or category
	abbr = frappe.get_cached_value("Company", company, "abbr")
	acc_name = f"{account_label} - {abbr}"

	if frappe.db.exists("Account", acc_name):
		return acc_name

	parent = _indirect_expense_group(company)
	if not parent:
		# No expense tree on this company yet; fall back to the default head.
		return get_default_account(company, "Expense Account")

	frappe.get_doc({
		"doctype": "Account",
		"account_name": account_label,
		"parent_account": parent,
		"company": company,
		"root_type": "Expense",
		"account_type": "Expense Account",
		"is_group": 0,
	}).insert(ignore_permissions=True)
	frappe.db.commit()
	return acc_name


def get_income_account(company: str, invoice_type: str | None = None) -> str | None:
	"""
	Resolve income account. Rent vs Utility routing by name convention if present,
	otherwise the company default income account.
	"""
	wanted = "Rent"
	if invoice_type:
		it = invoice_type.lower()
		if any(k in it for k in ("water", "electric", "utility")):
			wanted = "Utility"

	# Ensure the dedicated accounts exist, then pick.
	accounts = ensure_income_accounts(company)
	if wanted in accounts:
		return accounts[wanted]

	return get_default_account(company, "Income Account")


def register_property_dimension():
	"""
	Ensure a Cost Center-based accounting dimension is active. ERPNext treats
	Cost Center as a built-in dimension, so this mainly validates configuration.
	Kept as a hook point; no-op if Cost Center dimension is already native.
	"""
	# Cost Center is a native dimension in ERPNext; nothing to create.
	# If a custom "Property" dimension is desired later, create Accounting Dimension here.
	return True


@frappe.whitelist()
def provision_all():
	"""
	Admin utility: provision Companies for all Organizations and Cost Centers for
	all Properties. Requires System Manager.
	"""
	if "System Manager" not in frappe.get_roles(frappe.session.user):
		frappe.throw("Only System Manager can provision companies.")

	result = {"companies": [], "cost_centers": []}
	for org in frappe.get_all("Organization", pluck="name"):
		result["companies"].append(provision_company(org))
	for prop in frappe.get_all("Property", pluck="name"):
		result["cost_centers"].append(provision_property_cost_center(prop))
	return result
