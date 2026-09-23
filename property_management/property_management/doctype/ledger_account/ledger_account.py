# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LedgerAccount(Document):
	def validate(self):
		if not self.account_name or not self.account_type:
			frappe.throw("Account Name and Account Type are required.")
