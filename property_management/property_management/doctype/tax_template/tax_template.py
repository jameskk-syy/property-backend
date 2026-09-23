# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.model.document import Document


class TaxTemplate(Document):
	def validate(self):
		if not self.template_name or not self.template_type:
			frappe.throw("Template Name and Template Type are required.")

		if self.config_json:
			try:
				json.loads(self.config_json)
			except Exception:
				frappe.throw("Invalid JSON formatting in Config JSON.")
