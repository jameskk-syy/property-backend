# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Property(Document):
	def validate(self):
		self.setup_property_accounts()

	def setup_property_accounts(self):
		"""
		Ensures isolated financial accounts exist specifically for this property/apartment.
		"""
		prefix = f"{self.property_name} ({self.name})"

		if not self.account_group:
			self.account_group = f"Property Accounts - {prefix}"
		if not self.receivables_account:
			self.receivables_account = f"Debtors - {prefix}"
		if not self.rent_income_account:
			self.rent_income_account = f"Rent Income - {prefix}"
		if not self.utility_income_account:
			self.utility_income_account = f"Utility Income - {prefix}"
		if not self.expense_account:
			self.expense_account = f"Expenses - {prefix}"
		if not self.collection_account:
			self.collection_account = f"Collection Bank/M-Pesa - {prefix}"

	def after_insert(self):
		self.create_ledger_accounts()

	def create_ledger_accounts(self):
		from property_management.api.accounting import setup_property_ledger_accounts
		setup_property_ledger_accounts(self.name)
