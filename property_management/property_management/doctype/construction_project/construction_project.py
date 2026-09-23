# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ConstructionProject(Document):
	def validate(self):
		if not self.project_name:
			frappe.throw("Project Name is required.")

		self.recalculate_totals()

	def recalculate_totals(self):
		lines = self.get("budget_lines", [])
		# The overall budget is the entered figure. Only derive it from budget
		# lines when a line-item breakdown is actually provided (otherwise a new
		# build with no lines would be forced to a 0 budget).
		if lines:
			self.total_budget = sum(float(b.budgeted_amount or 0.0) for b in lines)
		total_a = sum(float(b.actual_amount or 0.0) for b in lines)
		# Keep any actual-spend already computed from approved purchases if it's
		# higher than the (possibly empty) budget-line actuals.
		self.total_actual_spend = max(float(self.total_actual_spend or 0.0), total_a)
		self.budget_variance = float(self.total_budget or 0.0) - float(self.total_actual_spend or 0.0)

	def after_insert(self):
		self.bridge_to_erpnext_project()

	def bridge_to_erpnext_project(self):
		"""Create a native ERPNext Project when integration is enabled for the org."""
		from property_management.integration.settings import is_enabled

		if self.get("erpnext_project"):
			return
		if not self.organization or not is_enabled(self.organization):
			return

		from property_management.integration.construction import ensure_erpnext_project
		ensure_erpnext_project(self.name)
