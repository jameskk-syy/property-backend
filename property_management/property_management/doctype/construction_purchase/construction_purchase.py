# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ConstructionPurchase(Document):
	def validate(self):
		if not self.project or not self.amount or float(self.amount) <= 0:
			frappe.throw("Construction Project and valid Amount (> 0) are required.")

		if self.project and not self.property:
			self.property = frappe.db.get_value("Construction Project", self.project, "property")

	def approve(self, user=None, comment=None):
		"""Maker-checker validation, funded-budget cap, budget update, and status
		transition to Approved."""
		if not user:
			user = frappe.session.user

		if user != "Administrator" and user == self.owner:
			frappe.throw("Maker-Checker Rule Violation: Creator cannot approve their own construction purchase request.")

		# Cap against actually-funded cash: cannot spend more than what has been
		# transferred into the project's budget account (minus what's spent).
		from property_management.integration import construction_budget
		construction_budget.assert_within_budget(self.project, self.amount)

		self.status = "Approved"
		self.approved_by = user
		self.approved_at = now_datetime()
		self.save(ignore_permissions=True)

		# Update project budget line actual spend + refresh the project's rolled-up
		# actual spend / variance so the list screen reflects reality.
		if self.project:
			project_doc = frappe.get_doc("Construction Project", self.project)
			matched = False
			for line in project_doc.get("budget_lines", []):
				if self.category and line.category == self.category:
					line.actual_amount = float(line.actual_amount or 0.0) + float(self.amount)
					matched = True
					break

			if not matched and project_doc.budget_lines:
				project_doc.budget_lines[0].actual_amount = float(project_doc.budget_lines[0].actual_amount or 0.0) + float(self.amount)

			project_doc.save(ignore_permissions=True)
			self._refresh_project_spend(project_doc.name)

		# Post to native ERPNext when integration is enabled, else legacy ledger.
		if not self.bridge_to_purchase_invoice():
			self.post_purchase_journal_entry()

	def _refresh_project_spend(self, project=None):
		"""Recompute total_actual_spend from approved purchases and update variance."""
		project = project or self.project
		if not project:
			return
		from property_management.integration import construction_budget
		spent = construction_budget.spent_amount(project)
		budget = float(frappe.db.get_value("Construction Project", project, "total_budget") or 0.0)
		frappe.db.set_value("Construction Project", project, {
			"total_actual_spend": spent,
			"budget_variance": budget - spent,
		}, update_modified=False)

	def bridge_to_purchase_invoice(self):
		"""
		Post the approved construction purchase as a native ERPNext Purchase Invoice
		(tagged to the Project). Returns True if bridged, False to fall back to the
		legacy JE. Runs only AFTER the maker-checker check in approve().
		"""
		from property_management.integration.settings import is_enabled

		if self.get("purchase_invoice"):
			return True
		if not is_enabled(self.organization):
			return False

		from property_management.integration.expenses import post_construction_purchase

		# self.name is an int for this Autoincrement-named doctype; the whitelisted
		# post_construction_purchase has a `str` type hint, so stringify to satisfy it.
		pi_name = post_construction_purchase(str(self.name), submit=True)
		self.db_set("purchase_invoice", pi_name, update_modified=False)

		# Pay the PI IN FULL from the project's funded budget wallet via a native
		# Payment Entry. This settles the invoice (status -> Paid, outstanding 0)
		# and draws the cash out of the wallet (available = funded - spent).
		try:
			from property_management.integration import construction_budget
			construction_budget.pay_purchase_invoice_from_budget(self.project, pi_name)
		except Exception:
			frappe.log_error(title="construction budget payment failed",
							 message=frappe.get_traceback())
		return True

	def reject(self, user=None, comment=None):
		if not user:
			user = frappe.session.user

		self.status = "Rejected"
		self.approved_by = user
		self.approved_at = now_datetime()
		self.save(ignore_permissions=True)

	def post_purchase_journal_entry(self):
		from property_management.api.accounting import post_journal_entry

		if not self.property:
			return

		prop = frappe.get_doc("Property", self.property)
		expense_acc = prop.expense_account
		collection_acc = prop.collection_account

		if not expense_acc or not collection_acc:
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
			return

		lines = [
			{"account": expense_acc, "debit": float(self.amount), "credit": 0.0},
			{"account": collection_acc, "debit": 0.0, "credit": float(self.amount)}
		]

		post_journal_entry(
			property_id=self.property,
			lines=lines,
			memo=f"Construction Purchase: {self.item_description} ({self.name})",
			ref_doctype="Construction Purchase",
			ref_name=self.name,
			organization=self.organization
		)
