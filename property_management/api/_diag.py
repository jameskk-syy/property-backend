import frappe


def pdf_smoke():
	frappe.set_user("Administrator")
	from property_management.api.v2.finance import _invoice_pdf_html
	from frappe.utils.pdf import get_pdf

	target = None
	for dt in ("Sales Invoice", "Purchase Invoice"):
		nm = frappe.db.get_value(dt, {"docstatus": ["<", 2]}, "name")
		if nm:
			target = (dt, nm)
			break
	print("TARGET", target)
	if not target:
		print("NO_INVOICE_OF_EITHER_TYPE")
		return
	html = _invoice_pdf_html(*target)
	pdf = get_pdf(html)
	head = pdf[:5]
	print("PDF_HEAD", head)
	print("PDF_LEN", len(pdf))
	print("IS_PDF", head.startswith(b"%PDF"))
