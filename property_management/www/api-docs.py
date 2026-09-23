# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe


def get_context(context):
	context.no_cache = 1
	context.title = "Property Management API Documentation"
