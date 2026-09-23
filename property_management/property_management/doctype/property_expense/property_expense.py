# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class PropertyExpense(Document):
	def validate(self):
		if not self.property or not self.amount or float(self.amount) <= 0:
			frappe.throw("Property and valid Expense Amount (> 0) are required.")

		# Default vendor name display if manual
		if not self.vendor and self.vendor_name_manual:
			self.vendor_name = self.vendor_name_manual
		elif self.vendor:
			self.vendor_name = frappe.db.get_value("Expense Vendor", self.vendor, "vendor_name")

	def approve(self, user=None, comment=None):
		"""Maker-checker validation and status transition to Approved."""
		if not user:
			user = frappe.session.user

		if user != "Administrator" and user == self.owner:
			frappe.throw("Maker-Checker Rule Violation: Creator cannot approve their own expense request.")

		self.status = "Approved"
		self.approved_by = user
		self.approved_at = now_datetime()
		self.save(ignore_permissions=True)

		# Post to native ERPNext when integration is enabled, else legacy ledger.
		if not self.bridge_to_purchase_invoice():
			self.post_expense_journal_entry()

	def bridge_to_purchase_invoice(self):
		"""
		Post the approved expense as a native ERPNext Purchase Invoice. Returns True
		if bridged (or already bridged), False to fall back to legacy JE posting.
		Runs only AFTER the maker-checker check in approve().
		"""
		from property_management.integration.settings import is_enabled

		if self.get("purchase_invoice"):
			return True
		if not is_enabled(self.organization):
			return False

		from property_management.integration.expenses import post_property_expense

		# self.name is an int for this Autoincrement-named doctype; post_property_expense
		# is whitelisted with a `str` type hint, so Frappe's strict typing rejects an int.
		pi_name = post_property_expense(str(self.name), submit=True)
		self.db_set("purchase_invoice", pi_name, update_modified=False)

		# Expenses are paid on approval FROM the Cash account, settling the PI
		# (status -> Paid, outstanding 0).
		try:
			from property_management.integration.expenses import pay_purchase_invoice
			pay_purchase_invoice(pi_name, reference_no=f"EXPENSE-{self.name}")
		except Exception:
			frappe.log_error(title="expense PI payment failed", message=frappe.get_traceback())
		return True

	def reject(self, user=None, comment=None):
		if not user:
			user = frappe.session.user

		self.status = "Rejected"
		self.approved_by = user
		self.approved_at = now_datetime()
		self.save(ignore_permissions=True)

	def post_expense_journal_entry(self):
		"""Creates double-entry posting: Debit Expense Account, Credit Collection Account."""
		from property_management.api.accounting import post_journal_entry

		prop = frappe.get_doc("Property", self.property)
		expense_acc = prop.expense_account
		collection_acc = prop.collection_account

		if not expense_acc or not collection_acc:
			# Fallback ledger accounts lookup
			expense_acc = frappe.db.get_value(
				"Ledger Account",
				{"property": self.property, "account_type": "Expense", "is_group": 0},
				"name"
			)
			collection_acc = frappe.db.get_value(
				"Ledger Account",
				{"property": self.property, "account_type": "Asset", "is_group": 0},
				"name"
			)

		if not expense_acc or not collection_acc:
			return  # Ledgers not configured yet

		lines = [
			{"account": expense_acc, "debit": float(self.amount), "credit": 0.0},
			{"account": collection_acc, "debit": 0.0, "credit": float(self.amount)}
		]

		post_journal_entry(
			property_id=self.property,
			lines=lines,
			memo=f"Expense Auto-Posting: {self.work_description} ({self.name})",
			ref_doctype="Property Expense",
			ref_name=self.name,
			organization=self.organization
		)
