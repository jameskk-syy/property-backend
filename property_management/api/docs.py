# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe


@frappe.whitelist(allow_guest=True)
def get_openapi_spec():
	"""
	Generates complete OpenAPI 3.0 specification for the Property Management System.
	Documents all core REST resources, custom whitelisted APIs, integrations, and reports.
	"""
	host_url = frappe.utils.get_url()

	spec = {
		"openapi": "3.0.3",
		"info": {
			"title": "Property Management System API",
			"description": "Multi-Tenant Property Management System with per-apartment accounting isolation, M-Pesa C2B/B2C, WhatsApp notifications, statutory payroll, utility billing, double-entry ledgers, and vacancy marketplace.",
			"version": "2.0.0",
			"contact": {
				"name": "DADIS Estates Engineering",
				"email": "admin@dadisestates.com"
			}
		},
		"servers": [
			{"url": host_url, "description": "Current Environment Server"}
		],
		"components": {
			"securitySchemes": {
				"FrappeToken": {
					"type": "apiKey",
					"in": "header",
					"name": "Authorization",
					"description": "Format: token api_key:api_secret"
				},
				"BearerAuth": {
					"type": "http",
					"scheme": "bearer",
					"bearerFormat": "JWT"
				}
			}
		},
		"security": [
			{"FrappeToken": []},
			{"BearerAuth": []}
		],
		"paths": {
			# --- AUTH & USERS ---
			"/api/method/frappe.auth.get_logged_user": {
				"get": {
					"summary": "Get Currently Logged In User",
					"tags": ["Authentication & Access"],
					"responses": {"200": {"description": "Current user details"}}
				}
			},
			"/api/resource/User": {
				"get": {
					"summary": "List System Users",
					"tags": ["Authentication & Access"],
					"responses": {"200": {"description": "User list"}}
				},
				"post": {
					"summary": "Create System User",
					"tags": ["Authentication & Access"],
					"responses": {"201": {"description": "User created"}}
				}
			},

			# --- CUSTOM MODULES & PERMISSIONS ---
			"/api/method/property_management.api.roles.get_user_permissions": {
				"get": {
					"summary": "Get User Permission Matrix",
					"tags": ["Custom Roles & Permissions"],
					"responses": {"200": {"description": "Module x Action permission map for current user"}}
				}
			},
			"/api/method/property_management.api.roles.assign_module_to_role": {
				"post": {
					"summary": "Attach Module Permission to Role",
					"tags": ["Custom Roles & Permissions"],
					"responses": {"200": {"description": "Permission rule assigned"}}
				}
			},
			"/api/method/property_management.api.roles.assign_user_to_property": {
				"post": {
					"summary": "Scope User to Specific Property",
					"tags": ["Custom Roles & Permissions"],
					"responses": {"200": {"description": "Property assignment created"}}
				}
			},

			# --- AUDIT LOGS ---
			"/api/resource/Audit Log": {
				"get": {
					"summary": "List Audit Logs",
					"tags": ["Audit Trail"],
					"responses": {"200": {"description": "List of system audit logs"}}
				}
			},

			# --- ORGANIZATIONS ---
			"/api/resource/Organization": {
				"get": {
					"summary": "List Tenant Organizations",
					"tags": ["Organization Management"],
					"responses": {"200": {"description": "List of tenant organizations"}}
				},
				"post": {
					"summary": "Register New Organization",
					"tags": ["Organization Management"],
					"responses": {"201": {"description": "Organization registered"}}
				}
			},

			# --- PROPERTIES & UNITS ---
			"/api/resource/Property": {
				"get": {
					"summary": "List Properties / Apartment Buildings",
					"tags": ["Properties & Units"],
					"responses": {"200": {"description": "List of properties"}}
				},
				"post": {
					"summary": "Create Property (Auto-Creates Isolated Accounts)",
					"tags": ["Properties & Units"],
					"responses": {"201": {"description": "Property created with per-apartment ledger accounts"}}
				}
			},
			"/api/resource/Property Unit": {
				"get": {
					"summary": "List Apartment Units",
					"tags": ["Properties & Units"],
					"responses": {"200": {"description": "List of units"}}
				}
			},

			# --- EXPENSES & VENDORS ---
			"/api/resource/Expense Vendor": {
				"get": {"summary": "List Expense Vendors", "tags": ["Expenses & Vendors"], "responses": {"200": {"description": "Vendor directory"}}},
				"post": {"summary": "Create Expense Vendor", "tags": ["Expenses & Vendors"], "responses": {"201": {"description": "Vendor created"}}}
			},
			"/api/resource/Property Expense": {
				"get": {"summary": "List Property Expenses", "tags": ["Expenses & Vendors"], "responses": {"200": {"description": "Expense records"}}},
				"post": {"summary": "Record Property Expense Request", "tags": ["Expenses & Vendors"], "responses": {"201": {"description": "Expense submitted for approval"}}}
			},

			# --- PAYROLL ---
			"/api/resource/Employee": {
				"get": {"summary": "List Employees", "tags": ["Payroll & Statutory Tax"], "responses": {"200": {"description": "Employee directory"}}},
				"post": {"summary": "Add Employee Record", "tags": ["Payroll & Statutory Tax"], "responses": {"201": {"description": "Employee created"}}}
			},
			"/api/resource/Payroll Run": {
				"get": {"summary": "List Payroll Runs", "tags": ["Payroll & Statutory Tax"], "responses": {"200": {"description": "Payroll runs"}}},
				"post": {"summary": "Create Monthly Payroll Run", "tags": ["Payroll & Statutory Tax"], "responses": {"201": {"description": "Payroll run initialized"}}}
			},

			# --- CONSTRUCTION PROJECTS ---
			"/api/resource/Construction Project": {
				"get": {"summary": "List Construction Projects", "tags": ["Construction Management"], "responses": {"200": {"description": "Projects"}}},
				"post": {"summary": "Create Construction Project", "tags": ["Construction Management"], "responses": {"201": {"description": "Project created"}}}
			},
			"/api/resource/Construction Purchase": {
				"get": {"summary": "List Construction Purchases", "tags": ["Construction Management"], "responses": {"200": {"description": "Purchases"}}},
				"post": {"summary": "Record Construction Purchase", "tags": ["Construction Management"], "responses": {"201": {"description": "Purchase submitted"}}}
			},

			# --- DOUBLE-ENTRY ACCOUNTING LEDGERS ---
			"/api/resource/Ledger Account": {
				"get": {"summary": "Chart of Accounts per Property", "tags": ["Double-Entry Ledgers"], "responses": {"200": {"description": "Ledger accounts"}}}
			},
			"/api/resource/Journal Entry": {
				"get": {"summary": "List Journal Entries", "tags": ["Double-Entry Ledgers"], "responses": {"200": {"description": "Journal entries"}}}
			},
			"/api/method/property_management.api.accounting.post_journal_entry": {
				"post": {"summary": "Post Balanced Journal Entry", "tags": ["Double-Entry Ledgers"], "responses": {"200": {"description": "Journal entry posted"}}}
			},
			"/api/method/property_management.api.accounting.reconcile_bank_transactions": {
				"post": {"summary": "Reconcile Bank Transactions", "tags": ["Double-Entry Ledgers"], "responses": {"200": {"description": "Transactions auto-matched"}}}
			},

			# --- CENTRALIZED APPROVAL INBOX ---
			"/api/method/property_management.api.approvals.get_pending_approvals": {
				"get": {"summary": "Get Pending Approvals Inbox", "tags": ["Centralized Approvals"], "responses": {"200": {"description": "Pending maker-checker requests"}}}
			},
			"/api/method/property_management.api.approvals.approve_request": {
				"post": {"summary": "Approve Pending Request", "tags": ["Centralized Approvals"], "responses": {"200": {"description": "Request approved"}}}
			},
			"/api/method/property_management.api.approvals.reject_request": {
				"post": {"summary": "Reject Pending Request", "tags": ["Centralized Approvals"], "responses": {"200": {"description": "Request rejected"}}}
			},

			# --- FINANCIAL REPORTS ---
			"/api/method/property_management.api.reports.rent_collection_report": {
				"get": {"summary": "Rent Collection Report", "tags": ["Financial Reports"], "responses": {"200": {"description": "Collection summary"}}}
			},
			"/api/method/property_management.api.reports.rent_arrears_report": {
				"get": {"summary": "Rent Arrears Report", "tags": ["Financial Reports"], "responses": {"200": {"description": "Overdue invoices summary"}}}
			},
			"/api/method/property_management.api.reports.profit_and_loss": {
				"get": {"summary": "Profit and Loss Statement", "tags": ["Financial Reports"], "responses": {"200": {"description": "P&L summary"}}}
			},

			# --- VACANCY MARKETPLACE ---
			"/api/resource/Prospective Tenant": {
				"get": {"summary": "List Prospective Tenants", "tags": ["Vacancy Marketplace"], "responses": {"200": {"description": "Leads list"}}},
				"post": {"summary": "Register Prospective Tenant", "tags": ["Vacancy Marketplace"], "responses": {"201": {"description": "Lead registered"}}}
			},
			"/api/resource/Viewing Request": {
				"get": {"summary": "List Viewing Requests", "tags": ["Vacancy Marketplace"], "responses": {"200": {"description": "Viewing requests"}}},
				"post": {"summary": "Book Physical Viewing", "tags": ["Vacancy Marketplace"], "responses": {"201": {"description": "Viewing booked"}}}
			},

			# --- API v2 (ERPNext/HRMS-backed, frontend contract) ---
			# Envelope: { "status": "success|error", "data": ..., "message": "" }
			"/api/method/property_management.api.v2.finance.create_invoice": {
				"post": {"summary": "Create tenant Sales Invoice (rent/utility)", "tags": ["API v2 - Finance"],
						"responses": {"200": {"description": "Sales Invoice created + posted to GL"}}}
			},
			"/api/method/property_management.api.v2.finance.record_payment": {
				"post": {"summary": "Record tenant payment (Payment Entry, allocates to invoice)", "tags": ["API v2 - Finance"],
						"responses": {"200": {"description": "Payment Entry created"}}}
			},
			"/api/method/property_management.api.v2.finance.record_expense": {
				"post": {"summary": "Record property expense (Purchase Invoice)", "tags": ["API v2 - Finance"],
						"responses": {"200": {"description": "Purchase Invoice created"}}}
			},
			"/api/method/property_management.api.v2.finance.list_invoices": {
				"get": {"summary": "List Sales Invoices (filter by company/property/status)", "tags": ["API v2 - Finance"],
						"responses": {"200": {"description": "Invoice list"}}}
			},
			"/api/method/property_management.api.v2.payroll.run": {
				"post": {"summary": "Run monthly HRMS payroll (Kenya statutory)", "tags": ["API v2 - Payroll"],
						"responses": {"200": {"description": "Payroll run summary with Salary Slips"}}}
			},
			"/api/method/property_management.api.v2.payroll.create_employee": {
				"post": {"summary": "Create HRMS Employee (caretaker/office/construction)", "tags": ["API v2 - Payroll"],
						"responses": {"200": {"description": "Employee created"}}}
			},
			"/api/method/property_management.api.v2.payroll.preview_statutory": {
				"get": {"summary": "Preview PAYE/NSSF/SHIF/Housing Levy for a gross", "tags": ["API v2 - Payroll"],
						"responses": {"200": {"description": "Statutory breakdown"}}}
			},
			"/api/method/property_management.api.v2.payroll.disburse": {
				"post": {"summary": "M-Pesa B2C net-pay disbursement for a Salary Slip", "tags": ["API v2 - Payroll"],
						"responses": {"200": {"description": "Disbursement queued"}}}
			},
			"/api/method/property_management.api.v2.reports.profit_and_loss": {
				"get": {"summary": "P&L from native GL (by company/property)", "tags": ["API v2 - Reports"],
						"responses": {"200": {"description": "Income/expense/net"}}}
			},
			"/api/method/property_management.api.v2.reports.trial_balance": {
				"get": {"summary": "Trial balance from native GL", "tags": ["API v2 - Reports"],
						"responses": {"200": {"description": "Per-account balances"}}}
			},
			"/api/method/property_management.api.v2.reports.construction_cost": {
				"get": {"summary": "True construction project cost (materials+labour+other)", "tags": ["API v2 - Reports"],
						"responses": {"200": {"description": "Budget vs actual + breakdown"}}}
			},
			"/api/method/property_management.api.v2.reports.landlord_remittance": {
				"get": {"summary": "Landlord net remittance", "tags": ["API v2 - Reports"],
						"responses": {"200": {"description": "Gross - commission - expenses"}}}
			},
			"/api/method/property_management.api.v2.admin.provision_all": {
				"post": {"summary": "Provision Companies + Cost Centers for all orgs/properties", "tags": ["API v2 - Admin"],
						"responses": {"200": {"description": "Provisioning summary"}}}
			}
		}
	}

	return spec
