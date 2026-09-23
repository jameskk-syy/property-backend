# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
M-Pesa (Daraja) endpoints: STK push for onboarding (rent+deposit) and for
monthly invoices, plus the STK callback that posts to the GL.

Pending STK requests are tracked in the `Mpesa Transaction` log keyed by
CheckoutRequestID so the async callback can resolve what was being paid.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from property_management.integration import mpesa_daraja
from property_management.integration import payments as pay
from property_management.api.utils import resolve_organization


def _log_transaction(checkout_id, merchant_id, organization, kind, ref_doctype, ref_name,
					 amount, phone, rent_amount=0, deposit_amount=0):
	"""Record a pending STK request so the callback can resolve it."""
	doc = frappe.get_doc({
		"doctype": "Mpesa Transaction",
		"organization": organization,
		"checkout_request_id": checkout_id,
		"merchant_request_id": merchant_id,
		"kind": kind,                      # 'Onboarding' | 'Invoice'
		"reference_doctype": ref_doctype,
		"reference_name": ref_name,
		"amount": flt(amount),
		"rent_amount": flt(rent_amount),
		"deposit_amount": flt(deposit_amount),
		"phone": phone,
		"status": "Pending",
	})
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


@frappe.whitelist()
def initiate_onboarding_payment(lease, phone=None):
	"""
	Caretaker triggers the initial rent+deposit STK push for a lease. The tenant
	approves on their phone; the callback posts the split (rent->income,
	deposit->liability) and marks the lease paid.
	"""
	lease_doc = frappe.get_doc("Lease Agreement", lease)
	rent = flt(lease_doc.rent_amount)
	deposit = flt(lease_doc.deposit_amount)
	total = rent + deposit
	if total <= 0:
		frappe.throw(_("Lease has no rent/deposit amount to charge."))

	if not phone:
		phone = frappe.db.get_value("Property Tenant", lease_doc.tenant, "phone")
	if not phone:
		frappe.throw(_("No tenant phone number on file for the STK push."))

	account_ref = f"RENT{lease_doc.name.split('-')[-1]}"
	resp = mpesa_daraja.stk_push(
		organization=lease_doc.organization, phone=phone, amount=total,
		account_reference=account_ref, description="Rent+Deposit",
	)
	_log_transaction(
		resp.get("CheckoutRequestID"), resp.get("MerchantRequestID"), lease_doc.organization,
		"Onboarding", "Lease Agreement", lease_doc.name, total, phone,
		rent_amount=rent, deposit_amount=deposit,
	)
	lease_doc.db_set("initial_payment_status", "Initiated", update_modified=False)
	return {"status": "STK push sent", "checkout_request_id": resp.get("CheckoutRequestID"), "amount": total}


@frappe.whitelist()
def initiate_invoice_payment(invoice, phone=None, amount=None):
	"""
	Tenant portal 'Pay' button: STK push for an invoice's outstanding amount.

	Accepts a native ERPNext Sales Invoice (the real monthly invoice) or a legacy
	Property Invoice. Guards against paying twice:
	  - refuses if the invoice has nothing outstanding (already fully paid);
	  - refuses if an STK push is already Pending for this invoice (no double prompt);
	  - never lets the paid amount exceed the true outstanding.
	"""
	# Resolve the invoice across both models to the fields we need.
	ref_doctype = None
	if frappe.db.exists("Sales Invoice", invoice):
		ref_doctype = "Sales Invoice"
		si = frappe.db.get_value(
			"Sales Invoice", invoice,
			["name", "customer", "outstanding_amount", "grand_total", "docstatus", "property_ref"],
			as_dict=True,
		)
		if si.docstatus != 1:
			frappe.throw(_("This invoice is not finalized yet."))
		outstanding = flt(si.outstanding_amount)
		organization = None
		if si.get("property_ref"):
			organization = frappe.db.get_value("Property", si.property_ref, "organization")
		if not organization:
			organization = resolve_organization()
		ref_name = si.name
		# Tenant phone via the Customer name (== tenant_name).
		tenant_doc = frappe.db.get_value("Property Tenant", {"tenant_name": si.customer}, "name")
		phone_lookup = frappe.db.get_value("Property Tenant", tenant_doc, "phone") if tenant_doc else None
	elif frappe.db.exists("Property Invoice", invoice):
		ref_doctype = "Property Invoice"
		inv = frappe.get_doc("Property Invoice", invoice)
		# Prefer the linked Sales Invoice's real outstanding when present.
		linked_si = frappe.db.get_value("Property Invoice", inv.name, "sales_invoice")
		outstanding = flt(
			frappe.db.get_value("Sales Invoice", linked_si, "outstanding_amount")
			if linked_si else inv.outstanding_amount
		)
		organization = inv.organization
		ref_name = inv.name
		phone_lookup = frappe.db.get_value("Property Tenant", inv.tenant, "phone")
	else:
		frappe.throw(_("Invoice not found."))

	# Guard 1: nothing left to pay.
	if outstanding <= 0:
		frappe.throw(_("This invoice is already fully paid."))

	# Guard 2: an STK push is already in flight for this invoice.
	pending = frappe.db.exists("Mpesa Transaction", {
		"reference_doctype": ref_doctype, "reference_name": ref_name, "status": "Pending",
	})
	if pending:
		frappe.throw(_("A payment prompt was already sent for this invoice and is still pending. "
					   "Please complete or wait for it before trying again."))

	# Cap the paid amount to the true outstanding (never overpay).
	pay_amount = flt(amount) if amount else outstanding
	if pay_amount > outstanding:
		pay_amount = outstanding
	if pay_amount <= 0:
		frappe.throw(_("Nothing outstanding to pay on this invoice."))

	phone = phone or phone_lookup
	if not phone:
		frappe.throw(_("No tenant phone number for the STK push."))

	account_ref = f"INV{str(ref_name).split('-')[-1]}"
	resp = mpesa_daraja.stk_push(
		organization=organization, phone=phone, amount=pay_amount,
		account_reference=account_ref, description="Rent Invoice",
	)
	_log_transaction(
		resp.get("CheckoutRequestID"), resp.get("MerchantRequestID"), organization,
		"Invoice", ref_doctype, ref_name, pay_amount, phone,
	)
	return {"status": "STK push sent", "checkout_request_id": resp.get("CheckoutRequestID"), "amount": pay_amount}


@frappe.whitelist(allow_guest=True)
def stk_callback():
	"""
	Daraja STK callback (guest, must be HTTPS-reachable). Resolves the pending
	Mpesa Transaction by CheckoutRequestID and enqueues GL posting.

	The full raw payload is logged (server log + stored on the transaction) so you
	can see exactly what Safaricom sent.
	"""
	try:
		data = frappe.request.get_json() or {}

		# Print/log the raw callback for debugging (appears in bench logs / console).
		import json as _json
		raw = _json.dumps(data, indent=2)
		print("=== M-PESA STK CALLBACK ===")
		print(raw)
		frappe.logger("mpesa").info(f"STK callback: {raw}")

		cb = data.get("Body", {}).get("stkCallback", {})
		checkout_id = cb.get("CheckoutRequestID")
		result_code = cb.get("ResultCode")
		result_desc = cb.get("ResultDesc")

		txn_name = frappe.db.get_value("Mpesa Transaction", {"checkout_request_id": checkout_id}, "name")
		if txn_name:
			# Always store the raw payload + result on the transaction.
			frappe.db.set_value("Mpesa Transaction", txn_name, {
				"raw_callback": raw,
				"result_desc": result_desc,
			}, update_modified=False)

			if result_code == 0:
				meta = {i.get("Name"): i.get("Value")
						for i in cb.get("CallbackMetadata", {}).get("Item", []) if "Name" in i}
				frappe.db.commit()
				frappe.enqueue(
					"property_management.api.mpesa.finalize_stk_payment", queue="default",
					txn_name=txn_name, mpesa_code=meta.get("MpesaReceiptNumber"),
					amount=meta.get("Amount"), phone=meta.get("PhoneNumber"),
				)
			else:
				frappe.db.set_value("Mpesa Transaction", txn_name, "status", "Failed", update_modified=False)
				frappe.db.commit()
		else:
			frappe.logger("mpesa").warning(f"STK callback for unknown CheckoutRequestID: {checkout_id}")

		return {"ResultCode": 0, "ResultDesc": "Accepted"}
	except Exception:
		frappe.log_error(title="M-Pesa STK callback error", message=frappe.get_traceback())
		return {"ResultCode": 1, "ResultDesc": "Error"}


@frappe.whitelist()
def payment_status(checkout_request_id=None, lease=None, invoice=None):
	"""
	Poll the status of an STK payment. The frontend calls this after sending an STK
	to show the tenant/caretaker whether it's Pending, Paid, or Failed.
	Resolve by checkout id, or the latest transaction for a lease/invoice.
	"""
	filters = {}
	if checkout_request_id:
		filters["checkout_request_id"] = checkout_request_id
	elif invoice:
		filters = {"reference_doctype": "Property Invoice", "reference_name": invoice}
	elif lease:
		filters = {"reference_doctype": "Lease Agreement", "reference_name": lease}
	else:
		return {"status": None}

	rows = frappe.get_all(
		"Mpesa Transaction", filters=filters,
		fields=["name", "status", "result_desc", "mpesa_receipt", "amount", "kind"],
		order_by="creation desc", limit=1,
	)
	if not rows:
		return {"status": None}
	t = rows[0]
	return {
		"status": t.status,
		"result_desc": t.result_desc,
		"mpesa_receipt": t.mpesa_receipt,
		"amount": t.amount,
		"kind": t.kind,
	}


def finalize_stk_payment(txn_name, mpesa_code, amount, phone):
	"""Post the confirmed STK payment to the GL (onboarding split or invoice payment)."""
	txn = frappe.get_doc("Mpesa Transaction", txn_name)
	if txn.status == "Paid":
		return

	frappe.set_user("Administrator")

	if txn.kind == "Onboarding" and txn.reference_doctype == "Lease Agreement":
		lease = frappe.get_doc("Lease Agreement", txn.reference_name)
		je = pay.post_onboarding_payment(
			property_name=lease.property, rent_amount=txn.rent_amount,
			deposit_amount=txn.deposit_amount, reference_no=mpesa_code,
		)
		lease.db_set("initial_payment_status", "Paid", update_modified=False)
		lease.db_set("initial_payment_reference", mpesa_code, update_modified=False)
		lease.db_set("initial_amount_paid", flt(amount), update_modified=False)
		# Ensure the unit is occupied (payment-confirmed).
		if lease.unit:
			frappe.db.set_value("Property Unit", lease.unit, "status", "Occupied")
		txn.db_set("journal_entry", je, update_modified=False)

	elif txn.kind == "Invoice" and txn.reference_doctype == "Sales Invoice":
		# Native Sales Invoice payment (the real monthly invoice flow).
		si = frappe.db.get_value("Sales Invoice", txn.reference_name,
								 ["customer", "property_ref"], as_dict=True) or frappe._dict()
		tenant = frappe.db.get_value("Property Tenant", {"tenant_name": si.get("customer")}, "name")
		pe = pay.create_payment_entry(
			property_name=si.get("property_ref"), amount=flt(amount),
			sales_invoice=txn.reference_name, tenant=tenant,
			payment_method="M-Pesa", reference_no=mpesa_code,
		)
		txn.db_set("payment_entry", pe, update_modified=False)

	elif txn.kind == "Invoice" and txn.reference_doctype == "Property Invoice":
		inv = frappe.get_doc("Property Invoice", txn.reference_name)
		sales_invoice = frappe.db.get_value("Property Invoice", inv.name, "sales_invoice")
		pe = pay.create_payment_entry(
			property_name=inv.property, amount=flt(amount), sales_invoice=sales_invoice,
			tenant=inv.tenant, payment_method="M-Pesa", reference_no=mpesa_code,
		)
		txn.db_set("payment_entry", pe, update_modified=False)

	txn.db_set("status", "Paid", update_modified=False)
	txn.db_set("mpesa_receipt", mpesa_code, update_modified=False)
	frappe.db.commit()


# ------------------------------------------------------------------
# Legacy B2C disbursement (payroll / vendor payouts) - unchanged stub.
# ------------------------------------------------------------------

@frappe.whitelist()
def disburse_b2c(payroll_item_id=None, expense_id=None, amount=0, phone_number=None):
	"""Trigger M-Pesa B2C disbursement for approved salary / vendor expenses."""
	if not frappe.has_permission("Property Payment", "write"):
		frappe.throw(_("Not authorized to disburse funds."))
	frappe.enqueue(
		"property_management.api.mpesa.process_b2c_background", queue="default",
		payroll_item_id=payroll_item_id, expense_id=expense_id, amount=amount, phone_number=phone_number,
	)
	return {"status": "Queued", "message": "B2C Disbursement initiated."}


def process_b2c_background(payroll_item_id, expense_id, amount, phone_number):
	frappe.logger().info(f"Processing B2C payout: {amount} to {phone_number}")
