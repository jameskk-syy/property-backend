# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BankTransaction(Document):
	def validate(self):
		if not self.bank_account or not self.amount or float(self.amount) == 0:
			frappe.throw("Bank Account and non-zero Amount are required.")
