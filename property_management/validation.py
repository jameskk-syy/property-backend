# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Validation hooks for ensuring data integrity.
"""

import frappe
from frappe import _


def _normalize_phone(phone):
    """Normalize phone number to digits only for comparison."""
    if not phone:
        return None
    # Remove all non-digits
    digits = ''.join(ch for ch in str(phone) if ch.isdigit())
    # Handle Kenyan numbers - normalize to 9 digits (without country code prefix)
    if digits.startswith('254') and len(digits) >= 12:
        digits = digits[3:]  # Remove 254
    elif digits.startswith('0') and len(digits) == 10:
        digits = digits[1:]  # Remove leading 0
    return digits if digits else None


def validate_unique_phone(doc, method=None):
    """
    Ensure phone number is unique across all users.
    Called on User validate event.
    """
    # Check both mobile_no and phone fields
    for field in ['mobile_no', 'phone']:
        phone = doc.get(field)
        if not phone:
            continue
        
        normalized = _normalize_phone(phone)
        if not normalized or len(normalized) < 8:
            continue
        
        # Check if another user has this phone number
        # Use LIKE with the last 9 digits to catch different formats
        search_pattern = f"%{normalized[-9:]}%"
        
        existing = frappe.db.sql("""
            SELECT name, mobile_no, phone 
            FROM `tabUser` 
            WHERE name != %(current_user)s
            AND (
                mobile_no LIKE %(pattern)s 
                OR phone LIKE %(pattern)s
            )
            LIMIT 1
        """, {
            'current_user': doc.name,
            'pattern': search_pattern
        }, as_dict=True)
        
        if existing:
            frappe.throw(
                _("Phone number {0} is already registered to another user ({1}). Each user must have a unique phone number.").format(
                    phone, existing[0].name
                ),
                title=_("Duplicate Phone Number")
            )
