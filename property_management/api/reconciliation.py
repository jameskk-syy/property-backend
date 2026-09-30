# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Reconciliation API - rent collection reporting and bank/M-Pesa transaction
reconciliation against tenant invoices.

- rent_collection_report: billed vs collected vs outstanding per property/period.
- list_unreconciled: incoming Bank Transactions not yet reconciled.
- reconcile: match a Bank Transaction to a tenant/invoice, record the payment
  (via v2.finance.record_payment) and flag the transaction as reconciled.
"""

import frappe
from frappe.utils import flt

from property_management.api.v2 import envelope
from property_management.api.utils import resolve_organization


@frappe.whitelist()
@envelope
def rent_collection_report(property=None, from_date=None, to_date=None, organization=None):
	"""
	Rent collection summary from Property Invoice records.

	Returns totals (billed / collected / outstanding) plus per-property rows.
	"""
	organization = resolve_organization(explicit=organization)

	filters = {}
	if organization:
		filters["organization"] = organization
	if property:
		filters["property"] = property
	if from_date and to_date:
		filters["posting_date"] = ["between", [from_date, to_date]]

	invoices = frappe.get_all(
		"Property Invoice",
		filters=filters,
		fields=["property", "total_amount", "paid_amount", "outstanding_amount", "status"],
	)

	total_billed = total_collected = total_outstanding = 0.0
	by_property = {}
	for inv in invoices:
		billed = flt(inv.total_amount)
		paid = flt(inv.paid_amount)
		out = flt(inv.outstanding_amount)
		total_billed += billed
		total_collected += paid
		total_outstanding += out

		row = by_property.setdefault(inv.property, {
			"property": inv.property, "billed": 0.0, "collected": 0.0,
			"outstanding": 0.0, "invoices": 0,
		})
		row["billed"] += billed
		row["collected"] += paid
		row["outstanding"] += out
		row["invoices"] += 1

	collection_rate = (total_collected / total_billed * 100) if total_billed else 0

	return {
		"from_date": from_date,
		"to_date": to_date,
		"total_billed": total_billed,
		"total_collected": total_collected,
		"total_outstanding": total_outstanding,
		"collection_rate": round(collection_rate, 1),
		"properties": list(by_property.values()),
	}


@frappe.whitelist()
@envelope
def list_unreconciled(bank_account=None, limit=100, property=None):
	"""List incoming (Credit) Bank Transactions that are not yet reconciled.

	property: optional Property filter. A Bank Transaction is linked to a property
	through its bank_account, which matches the property's collection_account. When
	a property is given we scope to that account (returns none if the property has
	no collection account configured).
	"""
	filters = {"reconciled": 0, "direction": "Credit"}
	if bank_account:
		filters["bank_account"] = bank_account
	if property:
		collection_account = frappe.db.get_value("Property", property, "collection_account")
		# No collection account => nothing can belong to this property yet.
		filters["bank_account"] = collection_account or "__none__"

	rows = frappe.get_all(
		"Bank Transaction",
		filters=filters,
		fields=["name", "bank_account", "transaction_date", "amount", "reference"],
		order_by="transaction_date desc",
		limit=int(limit),
	)
	return rows


@frappe.whitelist()
@envelope
def reconcile(bank_transaction, property, amount, tenant=None, sales_invoice=None,
              payment_method="Bank", reference_no=None):
	"""
	Reconcile a Bank Transaction against a tenant/invoice.

	Records a native Payment Entry (via the finance integration) and marks the
	transaction reconciled.
	"""
	if not frappe.db.exists("Bank Transaction", bank_transaction):
		raise frappe.ValidationError("Bank Transaction not found")

	txn = frappe.get_doc("Bank Transaction", bank_transaction)
	if txn.reconciled:
		raise frappe.ValidationError("This transaction is already reconciled")

	from property_management.integration.payments import create_payment_entry
	pe_name = create_payment_entry(
		property_name=property,
		amount=flt(amount),
		sales_invoice=sales_invoice,
		tenant=tenant,
		payment_method=payment_method,
		reference_no=reference_no or txn.reference,
		submit=True,
	)

	txn.reconciled = 1
	txn.save(ignore_permissions=True)

	return {"bank_transaction": bank_transaction, "payment_entry": pe_name, "reconciled": True}
