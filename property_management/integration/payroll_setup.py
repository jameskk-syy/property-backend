# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Phase 5 - Payroll setup (HRMS, Kenya statutory).

Creates the Salary Components (earnings + statutory deductions) with formula-based
amounts, and Salary Structures per employee category. Rates live here as data so
they can be edited without touching business logic elsewhere.

Statutory model (confirm against current KRA/NSSF/SHA circulars):
  PAYE           progressive bands on taxable = gross - NSSF, personal relief 2,400
  NSSF (Phase 4) Tier 1: 6% up to LEL 9,000; Tier 2: 6% from 9,000 up to UEL 108,000
                 -> max employee 6,480/month
  SHIF           2.75% of gross, minimum 300
  Housing Levy   1.5% of gross

The formulas are evaluated by HRMS at Salary Slip time. `base` is the structure
base (we set it to the employee's gross salary).
"""

import frappe

# --- Statutory constants (edit here to change rates) ---------------------------
PAYE_RELIEF = 2400.0
PAYE_BANDS = [
	(24000.0, 0.10),
	(32333.0, 0.25),
	(500000.0, 0.30),
	(800000.0, 0.325),
	(float("inf"), 0.35),
]
NSSF_TIER1_LIMIT = 9000.0
NSSF_TIER2_LIMIT = 108000.0
NSSF_RATE = 0.06
SHIF_RATE = 0.0275
SHIF_MIN = 300.0
HOUSING_LEVY_RATE = 0.015

# Employee categories -> Salary Structure name suffix
EMPLOYEE_CATEGORIES = ["Caretaker", "Office", "Construction Worker"]


# --- Formula strings evaluated by HRMS at slip time -----------------------------
# HRMS eval context exposes `base` (structure base = gross) and prior component
# abbreviations. We express statutory formulas inline so no external code runs.

NSSF_FORMULA = (
	f"(min(base, {NSSF_TIER1_LIMIT}) * {NSSF_RATE}) + "
	f"(min(max(base - {NSSF_TIER1_LIMIT}, 0), {NSSF_TIER2_LIMIT} - {NSSF_TIER1_LIMIT}) * {NSSF_RATE})"
)

SHIF_FORMULA = f"max({SHIF_MIN}, base * {SHIF_RATE})"

HOUSING_FORMULA = f"base * {HOUSING_LEVY_RATE}"

# PAYE depends on taxable = base - NSSF. HRMS _safe_eval allows only arithmetic
# and min/max/round (no lambda, no walrus, no .format). We express the progressive
# tax as a sum of per-band marginal amounts:
#     band_tax = max(0, min(taxable, upper) - lower) * rate
# taxable is written inline as (base - NSSF). This needs no conditionals.
def _build_paye_formula() -> str:
	terms = []
	lower = 0.0
	taxable = "(base - NSSF)"
	for upper, rate in PAYE_BANDS:
		if upper == float("inf"):
			terms.append(f"max(0, {taxable} - {lower}) * {rate}")
		else:
			terms.append(f"max(0, min({taxable}, {upper}) - {lower}) * {rate}")
			lower = upper
	gross_tax = " + ".join(terms)
	return f"max(0, ({gross_tax}) - {PAYE_RELIEF})"


PAYE_FORMULA = _build_paye_formula()


def _statutory_payable_account(company: str, label: str) -> str:
	"""Create/return a payable liability account for a statutory deduction."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	acc_name = f"{label} Payable - {abbr}"
	if frappe.db.exists("Account", acc_name):
		return acc_name

	parent = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Liability"},
		"name",
		order_by="lft asc",
	)
	# HRMS requires the payroll payable account to have NO account_type. Statutory
	# deduction payables use 'Payable'/'Tax'; the Salary payable stays blank.
	if label == "Salary":
		account_type = None
	elif label == "PAYE":
		account_type = "Tax"
	else:
		account_type = "Payable"

	frappe.get_doc({
		"doctype": "Account",
		"account_name": f"{label} Payable",
		"parent_account": parent,
		"company": company,
		"root_type": "Liability",
		"account_type": account_type,
		"is_group": 0,
	}).insert(ignore_permissions=True)
	return acc_name


def _salary_expense_account(company: str) -> str:
	"""Return an expense account used for salary earnings."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	acc_name = f"Salary Expense - {abbr}"
	if frappe.db.exists("Account", acc_name):
		return acc_name
	parent = frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "root_type": "Expense"},
		"name",
		order_by="lft asc",
	)
	frappe.get_doc({
		"doctype": "Account",
		"account_name": "Salary Expense",
		"parent_account": parent,
		"company": company,
		"root_type": "Expense",
		"account_type": "Expense Account",
		"is_group": 0,
	}).insert(ignore_permissions=True)
	return acc_name


def _ensure_component(name: str, abbr: str, ctype: str, formula: str | None, company: str, account: str):
	"""Create or update a Salary Component with a company account mapping."""
	if frappe.db.exists("Salary Component", name):
		comp = frappe.get_doc("Salary Component", name)
	else:
		comp = frappe.get_doc({
			"doctype": "Salary Component",
			"salary_component": name,
			"salary_component_abbr": abbr,
			"type": ctype,
		})

	comp.type = ctype
	if formula:
		comp.amount_based_on_formula = 1
		comp.formula = formula
	comp.depends_on_payment_days = 0

	# Company-specific account mapping (child table 'accounts').
	comp.set("accounts", [a for a in comp.get("accounts", []) if a.company != company])
	comp.append("accounts", {"company": company, "account": account})

	comp.flags.ignore_permissions = True
	comp.save(ignore_permissions=True)
	return comp.name


def setup_salary_components(company: str) -> dict:
	"""Create the earning + statutory deduction components for a company. Idempotent."""
	from property_management.integration import erpnext_setup as setup
	setup.bootstrap_global_masters()

	salary_expense = _salary_expense_account(company)

	comps = {}
	# Earning: Basic (equal to base). `base` needs no min/max so a formula is fine.
	comps["Basic"] = _ensure_component(
		"Basic", "B", "Earning", "base", company, salary_expense
	)

	# Statutory deductions are computed in Python by the Salary Slip validate hook
	# (compute_statutory), because HRMS's _safe_eval does NOT expose min/max/abs and
	# these are piecewise/capped calculations. Components are created WITHOUT a
	# formula; the hook fills their amounts.
	comps["NSSF"] = _ensure_component(
		"NSSF", "NSSF", "Deduction", None, company,
		_statutory_payable_account(company, "NSSF"),
	)
	comps["SHIF"] = _ensure_component(
		"SHIF", "SHIF", "Deduction", None, company,
		_statutory_payable_account(company, "SHIF"),
	)
	comps["Housing Levy"] = _ensure_component(
		"Housing Levy", "HL", "Deduction", None, company,
		_statutory_payable_account(company, "Housing Levy"),
	)
	comps["PAYE"] = _ensure_component(
		"PAYE", "PAYE", "Deduction", None, company,
		_statutory_payable_account(company, "PAYE"),
	)

	frappe.db.commit()
	return comps


def setup_salary_structure(company: str, category: str = "Office") -> str:
	"""Create (or return) a Salary Structure for a company + employee category."""
	setup_salary_components(company)
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"{category} Structure - {abbr}"

	if frappe.db.exists("Salary Structure", name):
		return name

	ss = frappe.get_doc({
		"doctype": "Salary Structure",
		"name": name,
		"company": company,
		"is_active": "Yes",
		"currency": frappe.get_cached_value("Company", company, "default_currency"),
		"salary_slip_based_on_timesheet": 0,
		"payroll_frequency": "Monthly",
		"earnings": [
			{"salary_component": "Basic", "amount_based_on_formula": 1, "formula": "base"},
		],
		# Deductions use a placeholder formula ('1') so HRMS keeps the rows on the
		# slip (zero-amount rows are dropped). The Salary Slip validate hook then
		# overwrites each amount with the Python-computed Kenyan statutory value.
		"deductions": [
			{"salary_component": "NSSF", "amount_based_on_formula": 1, "formula": "1"},
			{"salary_component": "SHIF", "amount_based_on_formula": 1, "formula": "1"},
			{"salary_component": "Housing Levy", "amount_based_on_formula": 1, "formula": "1"},
			{"salary_component": "PAYE", "amount_based_on_formula": 1, "formula": "1"},
		],
	})
	ss.flags.ignore_permissions = True
	ss.insert(ignore_permissions=True)
	ss.submit()
	frappe.db.commit()
	return name


# --- Reference (pure-python) statutory calculator for tests/verification --------

def apply_statutory_deductions(doc, method=None):
	"""
	Salary Slip validate hook. Computes Kenyan statutory deductions in Python
	(NSSF/SHIF/Housing/PAYE) and writes them onto the slip's deduction rows.

	Registered via hooks.py doc_events for Salary Slip. Only acts on slips whose
	deduction components are our statutory set, so non-Kenyan structures are untouched.
	"""
	statutory = {"NSSF", "SHIF", "Housing Levy", "PAYE"}
	rows = {d.salary_component: d for d in doc.get("deductions", [])}
	if not (statutory & set(rows.keys())):
		return

	# Gross for the period = sum of earnings (Basic + any allowances).
	gross = sum(float(e.amount or 0) for e in doc.get("earnings", []))
	if gross <= 0:
		# Fallback to the employee's stored gross.
		gross = float(frappe.db.get_value("Employee", doc.employee, "gross_salary") or 0)
	if gross <= 0:
		return

	calc = compute_statutory(gross)
	mapping = {
		"NSSF": calc["nssf"],
		"SHIF": calc["shif"],
		"Housing Levy": calc["housing_levy"],
		"PAYE": calc["paye"],
	}
	for comp, amount in mapping.items():
		if comp in rows:
			rows[comp].amount = amount

	# Recompute totals so gross/net reflect the new deduction amounts.
	doc.total_deduction = sum(float(d.amount or 0) for d in doc.get("deductions", []))
	doc.net_pay = float(gross) - float(doc.total_deduction)
	doc.rounded_total = round(doc.net_pay)


def compute_statutory(gross: float) -> dict:
	"""
	Pure-python reference implementation of the same statutory model, used to
	verify Salary Slip amounts. Mirrors the formula constants above.
	"""
	nssf = (min(gross, NSSF_TIER1_LIMIT) * NSSF_RATE) + (
		min(max(gross - NSSF_TIER1_LIMIT, 0), NSSF_TIER2_LIMIT - NSSF_TIER1_LIMIT) * NSSF_RATE
	)
	shif = max(SHIF_MIN, gross * SHIF_RATE)
	housing = gross * HOUSING_LEVY_RATE

	taxable = gross - nssf
	tax = 0.0
	lower = 0.0
	for upper, rate in PAYE_BANDS:
		if taxable > lower:
			band = min(taxable, upper) - lower
			tax += band * rate
			lower = upper
		else:
			break
	paye = max(0.0, tax - PAYE_RELIEF)

	total_ded = nssf + shif + housing + paye
	return {
		"gross": round(gross, 2),
		"nssf": round(nssf, 2),
		"shif": round(shif, 2),
		"housing_levy": round(housing, 2),
		"paye": round(paye, 2),
		"total_deductions": round(total_ded, 2),
		"net": round(gross - total_ded, 2),
	}
