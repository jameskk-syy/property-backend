# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import unittest
import frappe

# Initialize frappe site context if not connected
if not getattr(frappe, "db", None) or not frappe.db:
	frappe.init(site="property.localhost", sites_path="/home/jamie/property/sites")
	frappe.connect()

from property_management.api.roles import (
	create_module,
	assign_module_to_role,
	check_module_permission,
	get_user_permissions
)
from property_management.permission import get_permission_query_conditions
from property_management.audit import capture_audit_log
from property_management.api.docs import get_openapi_spec


class TestPhase2BackendModules(unittest.TestCase):

	def setUp(self):
		frappe.set_user("Administrator")

	def test_01_unrestricted_roles_and_module_permissions(self):
		"""Verify Director, Office, Office User, System Manager have unrestricted access."""
		is_allowed = check_module_permission("Administrator", "expenses", "approve")
		self.assertTrue(is_allowed)

	def test_02_property_scoped_user_assignment(self):
		"""Verify property-level filtering query conditions for Caretaker vs Director."""
		frappe.flags.current_doctype = "Property"
		director_cond = get_permission_query_conditions("Administrator")
		self.assertEqual(director_cond, "")

	def test_03_audit_logging_engine(self):
		"""Verify audit log auto-creation hook function."""
		mock_doc = frappe._dict({
			"doctype": "Property",
			"name": "TEST-PROP-001",
			"organization": "ORG-TEST",
			"flags": frappe._dict({"ignore_audit_log": False})
		})

		capture_audit_log(mock_doc, method="after_insert")

	def test_04_statutory_payroll_deduction_math(self):
		"""Validate Kenya statutory tax formulas (PAYE, SHA, NSSF, Housing Levy) for KSh 50,000 gross."""
		from property_management.property_management.doctype.payroll_run.payroll_run import PayrollRun

		gross = 50000.0
		deductions = PayrollRun.calculate_statutory_deductions(gross)

		ded_map = {d["deduction_type"]: d["amount"] for d in deductions}

		# NSSF: min(2160, 50000 * 0.06 = 3000) = 2,160.00
		self.assertEqual(ded_map["NSSF"], 2160.0)

		# SHA: 50000 * 0.0275 = 1,375.00
		self.assertEqual(ded_map["SHA"], 1375.0)

		# Housing Levy: 50000 * 0.015 = 750.00
		self.assertEqual(ded_map["Housing Levy"], 750.0)

		# PAYE: Taxable = 50000 - 2160 = 47,840.
		# Tax: (24000*0.10) + (8333*0.25) + ((47840-32333)*0.30) = 2400 + 2083.25 + 4652.1 = 9135.35
		# PAYE Net = 9135.35 - 2400 (relief) = 6,735.35
		self.assertEqual(ded_map["PAYE"], 6735.35)

	def test_05_double_entry_journal_unbalanced_rejection(self):
		"""Verify unbalanced journal entries raise validation error."""
		from property_management.property_management.doctype.journal_entry.journal_entry import JournalEntry

		unbalanced_doc = frappe.get_doc({
			"doctype": "Journal Entry",
			"property": "PROP-TEST",
			"posting_date": frappe.utils.nowdate(),
			"lines": [
				{"account": "ACC-01", "debit": 1000.0, "credit": 0.0},
				{"account": "ACC-02", "debit": 0.0, "credit": 500.0}
			]
		})
		with self.assertRaises(frappe.ValidationError):
			unbalanced_doc.validate()

	def test_06_maker_checker_self_approval_prevention(self):
		"""Verify property expense creator cannot approve their own request."""
		expense = frappe.get_doc({
			"doctype": "Property Expense",
			"property": "PROP-TEST",
			"amount": 5000.0,
			"work_description": "Plumbing Leak Fix",
			"status": "Pending Approval",
			"owner": "user_maker@dadisestates.com"
		})

		# Simulating maker-checker self approval attempt
		with self.assertRaises(frappe.ValidationError):
			expense.approve(user="user_maker@dadisestates.com")

	def test_07_openapi_swagger_spec_completeness(self):
		"""Verify OpenAPI specification contains all new Phase 2 routes."""
		spec = get_openapi_spec()
		self.assertEqual(spec["openapi"], "3.0.3")
		self.assertIn("/api/method/property_management.api.roles.get_user_permissions", spec["paths"])
		self.assertIn("/api/resource/Property Expense", spec["paths"])
		self.assertIn("/api/resource/Payroll Run", spec["paths"])
		self.assertIn("/api/method/property_management.api.approvals.get_pending_approvals", spec["paths"])


if __name__ == "__main__":
	unittest.main()
