# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Tests for the ERPNext/HRMS integration layer.

Covers the two most safety-critical behaviours:
  1. Maker-checker: the creator of a Property Expense cannot approve it.
  2. Kenyan statutory payroll math (PAYE/NSSF/SHIF/Housing Levy) is exact.
Plus a guard that money API endpoints are not guest-accessible.
"""

import unittest

import frappe

if not getattr(frappe, "db", None) or not frappe.db:
	frappe.init(site="property.localhost", sites_path="/home/jamie/property/sites")
	frappe.connect()

from property_management.integration import erpnext_setup, payroll_setup, payroll


TEST_ORG = "ZZ IT Test Org"


def _ensure_org_company():
	if not frappe.db.exists("Organization", TEST_ORG):
		frappe.get_doc({"doctype": "Organization", "organization_name": TEST_ORG, "status": "Active"}).insert(
			ignore_permissions=True
		)
	return erpnext_setup.provision_company(TEST_ORG)


class TestMakerChecker(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.company = _ensure_org_company()
		self.prop = frappe.db.get_value(
			"Property", {"organization": TEST_ORG, "property_name": "ZZ IT Block"}, "name"
		)
		if not self.prop:
			p = frappe.get_doc({
				"doctype": "Property", "organization": TEST_ORG,
				"property_name": "ZZ IT Block", "property_code": "ZZIT", "status": "Active",
			})
			p.flags.ignore_mandatory = True
			p.insert(ignore_permissions=True)
			self.prop = p.name
		erpnext_setup.provision_property_cost_center(self.prop)

		self.maker = "zz_it_maker@example.com"
		if not frappe.db.exists("User", self.maker):
			frappe.get_doc({
				"doctype": "User", "email": self.maker, "first_name": "IT Maker",
				"send_welcome_email": 0, "roles": [{"role": "System Manager"}],
			}).insert(ignore_permissions=True)

	def test_creator_cannot_approve_own_expense(self):
		frappe.set_user(self.maker)
		exp = frappe.get_doc({
			"doctype": "Property Expense", "organization": TEST_ORG, "property": self.prop,
			"vendor_name_manual": "IT Vendor", "expense_category": "General",
			"amount": 5000, "work_description": "Test maker-checker", "status": "Pending Approval",
		})
		exp.insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			exp.approve(user=self.maker)
		frappe.set_user("Administrator")


class TestKenyaStatutory(unittest.TestCase):
	def test_statutory_math_50000(self):
		r = payroll_setup.compute_statutory(50000.0)
		self.assertEqual(r["nssf"], 3000.0)
		self.assertEqual(r["shif"], 1375.0)
		self.assertEqual(r["housing_levy"], 750.0)
		self.assertEqual(r["paye"], 6483.35)
		self.assertEqual(r["net"], 38391.65)

	def test_statutory_math_low_income(self):
		# Gross 20,000: NSSF 6% = 1200; SHIF 550; Housing 300; taxable 18800*10%=1880 -relief 2400 -> PAYE 0.
		r = payroll_setup.compute_statutory(20000.0)
		self.assertEqual(r["nssf"], 1200.0)
		self.assertEqual(r["shif"], 550.0)
		self.assertEqual(r["housing_levy"], 300.0)
		self.assertEqual(r["paye"], 0.0)


class TestApiSecurity(unittest.TestCase):
	def test_money_endpoints_not_guest(self):
		from property_management.api import accounting, reports, mpesa  # noqa: F401
		guest_names = {f"{m.__module__}.{m.__name__}" for m in frappe.guest_methods}
		money = {
			"property_management.api.accounting.get_trial_balance",
			"property_management.api.accounting.post_journal_entry",
			"property_management.api.reports.landlord_remittance_report",
		}
		self.assertEqual(money & guest_names, set())
		# M-Pesa webhook must remain guest-accessible.
		self.assertIn("property_management.api.mpesa.c2b_callback", guest_names)


if __name__ == "__main__":
	unittest.main()
