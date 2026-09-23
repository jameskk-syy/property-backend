# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Admin document repository.

Surfaces the REAL documents the platform captures per tenant/lease — primarily the
signed Lease Agreement PDF generated at onboarding, plus the tenant's national ID
and any other files attached to the lease or tenant. Files are streamed through an
authenticated endpoint so private files download correctly for admins.
"""

import frappe
from frappe.utils import get_files_path


def file_to_data_url(file_url):
    """
    Read a File (public OR private) and return a base64 data: URL, so the frontend
    can render sensitive images (national IDs) inline without a separate,
    permission-guarded /files request. Returns None if unreadable.
    """
    if not file_url:
        return None
    if str(file_url).startswith("data:"):
        return file_url
    import base64, mimetypes, os
    try:
        content = None
        file_name = file_url
        fdoc = frappe.get_all("File", filters={"file_url": file_url},
                              fields=["name", "file_name"], limit=1)
        if fdoc:
            content = frappe.get_doc("File", fdoc[0].name).get_content()
            file_name = fdoc[0].file_name or file_url
        else:
            rel = file_url.split("/files/")[-1]
            base = get_files_path(is_private=("/private/" in file_url))
            with open(os.path.join(base, rel), "rb") as fh:
                content = fh.read()
        if content is None:
            return None
        mime = mimetypes.guess_type(file_name)[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    except Exception:
        frappe.log_error(title="id image encode failed", message=frappe.get_traceback())
        return None


def _lease_pdf_url(lease_name, agreement_pdf):
    """Best available URL for a lease's signed PDF (stored field or attachment)."""
    if agreement_pdf:
        return agreement_pdf
    return frappe.db.get_value(
        "File",
        {"attached_to_doctype": "Lease Agreement", "attached_to_name": lease_name,
         "file_name": ["like", "%.pdf"]},
        "file_url",
    )


@frappe.whitelist()
def list_tenant_documents(tenant=None, property=None, limit=200):
    """
    One row per lease with tenant details and document availability:
    tenant, national ID (number + front/back ID images as base64), property/unit,
    signed date, lease status, and the signed lease PDF URL. Filterable by tenant
    and/or property for the admin document repository.
    """
    filters = {}
    if tenant:
        filters["tenant"] = tenant
    if property:
        filters["property"] = property
    leases = frappe.get_all(
        "Lease Agreement", filters=filters,
        fields=["name", "tenant", "property", "unit", "status", "agreement_pdf",
                "signed_on", "tenant_signature", "caretaker_signature",
                "start_date", "end_date", "creation"],
        order_by="creation desc", limit=int(limit),
    )

    # Prefetch tenant details (including the ID image file URLs).
    tenant_ids = list({le.tenant for le in leases if le.tenant})
    tenant_map = {}
    if tenant_ids:
        for t in frappe.get_all(
            "Property Tenant", filters={"name": ["in", tenant_ids]},
            fields=["name", "tenant_name", "national_id", "phone", "email",
                    "national_id_front", "national_id_back"],
        ):
            tenant_map[t.name] = t

    # Prefetch friendly property/unit labels.
    prop_ids = list({le.property for le in leases if le.property})
    prop_names = {}
    if prop_ids:
        for p in frappe.get_all("Property", filters={"name": ["in", prop_ids]},
                                fields=["name", "property_name"]):
            prop_names[p.name] = p.property_name or p.name
    unit_ids = list({le.unit for le in leases if le.unit})
    unit_labels = {}
    if unit_ids:
        for u in frappe.get_all("Property Unit", filters={"name": ["in", unit_ids]},
                                fields=["name", "unit_number"]):
            unit_labels[u.name] = u.unit_number or u.name

    # Extra files attached to the leases / tenants (e.g. future ID scans).
    extra_by_lease = {}
    if leases:
        lease_ids = [le.name for le in leases]
        for f in frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Lease Agreement", "attached_to_name": ["in", lease_ids]},
            fields=["file_name", "file_url", "attached_to_name"],
        ):
            if f.file_name and f.file_name.endswith(".pdf"):
                continue  # the signed lease PDF is shown separately
            extra_by_lease.setdefault(f.attached_to_name, []).append(
                {"file_name": f.file_name, "file_url": f.file_url}
            )

    rows = []
    for le in leases:
        t = tenant_map.get(le.tenant, frappe._dict())
        pdf_url = _lease_pdf_url(le.name, le.agreement_pdf)
        # A lease is genuinely SIGNED only when signatures were actually captured
        # (signed_on set, or a tenant/caretaker signature stored). A PDF may exist
        # even for an unsigned lease, so PDF existence alone is NOT proof of signing.
        has_tenant_sig = bool((le.tenant_signature or "").strip())
        has_caretaker_sig = bool((le.caretaker_signature or "").strip())
        is_signed = bool(le.signed_on) or (has_tenant_sig and has_caretaker_sig)
        rows.append({
            "lease": le.name,
            "tenant": le.tenant,
            "tenant_name": t.get("tenant_name") or le.tenant,
            "national_id": t.get("national_id") or "",
            "phone": t.get("phone") or "",
            "email": t.get("email") or "",
            "property": le.property,
            "property_name": prop_names.get(le.property, le.property),
            "unit": unit_labels.get(le.unit, le.unit),
            "status": le.status,
            "signed_on": str(le.signed_on)[:19] if le.signed_on else None,
            "start_date": str(le.start_date) if le.start_date else None,
            "end_date": str(le.end_date) if le.end_date else None,
            "id_front": file_to_data_url(t.get("national_id_front")),
            "id_back": file_to_data_url(t.get("national_id_back")),
            "has_id_front": bool(t.get("national_id_front")),
            "has_id_back": bool(t.get("national_id_back")),
            "is_signed": is_signed,
            "has_tenant_signature": has_tenant_sig,
            "has_caretaker_signature": has_caretaker_sig,
            "has_lease_pdf": bool(pdf_url),
            "lease_pdf_url": pdf_url,
            "extra_files": extra_by_lease.get(le.name, []),
        })
    return rows


@frappe.whitelist()
def download_lease(lease):
    """
    Stream a lease's signed PDF through the (authenticated) API so private files
    download correctly. Sets frappe.response for the file download.
    """
    if not frappe.db.exists("Lease Agreement", lease):
        frappe.throw("Lease not found")

    agreement_pdf = frappe.db.get_value("Lease Agreement", lease, "agreement_pdf")
    file_url = _lease_pdf_url(lease, agreement_pdf)
    if not file_url:
        frappe.throw("No signed lease document available for this lease yet.")

    file_doc = frappe.get_all(
        "File", filters={"file_url": file_url}, fields=["name"], limit=1
    )
    if file_doc:
        content = frappe.get_doc("File", file_doc[0].name).get_content()
    else:
        # Fallback: read from disk relative to the files path.
        import os
        rel = file_url.split("/files/")[-1]
        base = get_files_path(is_private=("/private/" in file_url))
        with open(os.path.join(base, rel), "rb") as fh:
            content = fh.read()

    frappe.local.response.filename = f"Lease-{lease}.pdf"
    frappe.local.response.filecontent = content
    frappe.local.response.type = "pdf"
