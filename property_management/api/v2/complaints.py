# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
API v2 - Complaints (DEPRECATED - use feedback.py instead).

This file is kept for backward compatibility. All functions redirect to feedback.py.

Base URL: /api/method/property_management.api.v2.complaints.<function>
"""

# Re-export all functions from feedback module for backward compatibility
from property_management.api.v2.feedback import (
    list_complaints,
    complaint_stats,
    respond_complaint,
    tenant_options,
    # New API names also available
    list_feedback,
    feedback_stats,
    respond_feedback,
    raise_feedback,
    my_feedback,
    caretaker_options,
)
