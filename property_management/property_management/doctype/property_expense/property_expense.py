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

		# Unit/tenant repair funded from the deposit: reduce the tenant's deposit
		# balance (capped at what's available) and reflect it in the GL. Done before
		# the PI/JE posting so the expense record already carries the deducted amount.
		self.apply_deposit_deduction()

		# Post to native ERPNext when integration is enabled, else legacy ledger.
		if not self.bridge_to_purchase_invoice():
			self.post_expense_journal_entry()

	def apply_deposit_deduction(self):
		"""
		When an approved expense is attached to a unit whose active lease has a
		tenant, and it's flagged to deduct from deposit, reduce that lease's
		deposit_balance (never below zero) and post a Journal Entry that debits the
		'Tenant Security Deposits' liability (reducing it) against the property's
		expense account. Any amount above the available balance is recorded as a
		shortfall the tenant owes.
		"""
		if self.get("expense_scope") != "Unit / Tenant":
			return
		if not self.get("unit") or not self.get("deduct_from_deposit"):
			return

		# Resolve the active lease for this unit (holds tenant + deposit balance).
		lease_name = frappe.db.get_value(
			"Lease Agreement",
			{"unit": self.unit, "status": "Active"},
			"name",
			order_by="start_date desc",
		)
		if not lease_name:
			frappe.msgprint("No active lease on this unit; deposit not deducted.")
			return

		lease = frappe.get_doc("Lease Agreement", lease_name)
		# Fall back to deposit_amount if the running balance hasn't been initialised.
		available = float(lease.get("deposit_balance") or 0)
		if not available and lease.get("deposit_amount"):
			available = float(lease.deposit_amount)

		amount = float(self.amount or 0)
		deducted = min(amount, max(available, 0.0))
		shortfall = round(amount - deducted, 2)

		# Track on both the lease (running balance) and the expense (audit trail).
		lease.db_set("deposit_balance", round(available - deducted, 2), update_modified=False)
		self.db_set("deposit_deducted", round(deducted, 2), update_modified=False)
		self.db_set("deposit_shortfall", shortfall, update_modified=False)

		if deducted > 0:
			self._post_deposit_deduction_journal(lease, deducted)

	def _post_deposit_deduction_journal(self, lease, deducted):
		"""JE: Debit 'Tenant Security Deposits' (reduce liability), Credit property expense account."""
		try:
			from property_management.api.accounting import create_journal_entry
			from property_management.integration.erpnext_setup import ensure_deposit_liability_account
			from property_management.integration.settings import company_for_organization

			company = company_for_organization(self.organization) if self.organization else None
			if not company:
				company = frappe.db.get_value("Company", {}, "name")
			deposit_acc = ensure_deposit_liability_account(company) if company else None

			prop = frappe.get_doc("Property", self.property)
			offset_acc = prop.expense_account or frappe.db.get_value(
				"Ledger Account",
				{"property": self.property, "account_type": "Expense", "is_group": 0},
				"name",
			)
			if not deposit_acc or not offset_acc:
				frappe.log_error(title="deposit deduction JE skipped",
								 message=f"Missing accounts deposit={deposit_acc} offset={offset_acc}")
				return

			# Debit the deposit liability (reduces what we owe the tenant); credit the
			# property expense account (the repair is funded by the deposit).
			lines = [
				{"account": deposit_acc, "debit": float(deducted), "credit": 0.0},
				{"account": offset_acc, "debit": 0.0, "credit": float(deducted)},
			]
			create_journal_entry(
				lines=lines,
				remark=f"Deposit deduction for repair: {self.work_description} ({self.name}) - tenant {lease.tenant}",
				company=company,
				property=self.property,
			)
		except Exception:
			frappe.log_error(title="deposit deduction JE failed", message=frappe.get_traceback())

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
