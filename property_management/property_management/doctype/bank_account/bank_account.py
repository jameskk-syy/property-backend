# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BankAccount(Document):
	def validate(self):
		if not self.bank_name or not self.account_number:
			frappe.throw("Bank Name and Account Number are required.")
