# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ProjectTask(Document):
	def validate(self):
		if not self.project or not self.task_name:
			frappe.throw("Project and Task Name are required.")
