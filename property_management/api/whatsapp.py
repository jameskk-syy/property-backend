# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
WhatsApp Cloud API Integration

Provides:
- Webhook endpoint to receive messages and status updates from WAClient/Meta
- Send text, template, and interactive button messages
- Handle "Pay Now" button clicks to trigger M-Pesa STK push
- Message logging and status tracking

Webhook URL: /api/method/property_management.api.whatsapp.webhook
"""

import json
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime, flt

from property_management.api.v2 import envelope
from property_management.api.utils import resolve_organization


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _get_messaging_settings(organization=None):
    """Get Messaging Settings doc for the organization."""
    if not organization:
        try:
            organization = resolve_organization()
        except Exception:
            organization = None
    if not organization:
        return None
    name = frappe.db.get_value("Messaging Settings", {"organization": organization}, "name")
    return frappe.get_doc("Messaging Settings", name) if name else None


def _get_access_token(doc):
    """Decrypt the WhatsApp access token from Messaging Settings."""
    if not doc:
        return None
    try:
        return doc.get_password("whatsapp_access_token", raise_exception=False)
    except Exception:
        return None


def _normalize_phone(phone):
    """
    Normalize phone number to WhatsApp format (no + prefix, just digits).
    Examples: +254712345678 -> 254712345678, 0712345678 -> 254712345678
    """
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        digits = "254" + digits
    return digits


def _log_message(organization, phone, message_type, content, direction="Outgoing",
                 status="Queued", template_name=None, wamid=None, error=None,
                 button_payload=None, button_text=None, ref_doctype=None, ref_name=None,
                 raw_payload=None):
    """Create a WhatsApp Message Log entry."""
    try:
        doc = frappe.get_doc({
            "doctype": "WhatsApp Message Log",
            "organization": organization,
            "direction": direction,
            "recipient_phone": phone or "",
            "message_type": message_type,
            "template_name": template_name,
            "message_content": content[:65535] if content else "",
            "status": status,
            "wamid": wamid,
            "error_log": str(error)[:5000] if error else None,
            "button_payload": button_payload,
            "button_text": button_text,
            "reference_doctype": ref_doctype,
            "reference_name": ref_name,
            "raw_payload": json.dumps(raw_payload, indent=2)[:65535] if raw_payload else None,
            "timestamp": now_datetime(),
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name
    except Exception as e:
        frappe.log_error(title="WhatsApp log failed", message=frappe.get_traceback())
        return None


def _update_message_status(wamid, status, error=None):
    """Update status of an existing message by wamid."""
    if not wamid:
        return
    try:
        name = frappe.db.get_value("WhatsApp Message Log", {"wamid": wamid}, "name")
        if name:
            updates = {"status": status}
            if error:
                updates["error_log"] = str(error)[:5000]
            frappe.db.set_value("WhatsApp Message Log", name, updates, update_modified=True)
            frappe.db.commit()
    except Exception:
        frappe.log_error(title="WhatsApp status update failed", message=frappe.get_traceback())


# ---------------------------------------------------------------------------
# Meta Cloud API - Send Messages
# ---------------------------------------------------------------------------

def _send_to_meta(phone_number_id, access_token, payload, base_url=None):
    """
    Send a message via Meta Cloud API.
    Returns the API response dict with message ID.
    """
    import requests
    
    base = (base_url or "https://graph.facebook.com/v19.0").rstrip("/")
    url = f"{base}/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    data = resp.json()
    
    if resp.status_code >= 400:
        error_msg = data.get("error", {}).get("message", resp.text)
        raise Exception(f"Meta API error: {error_msg}")
    
    return data


def send_text_message(phone, message, organization=None):
    """
    Send a plain text message via WhatsApp Cloud API.
    
    Args:
        phone: Recipient phone number
        message: Text content to send
        organization: Organization name (auto-resolved if not provided)
    
    Returns:
        dict with message_id and status
    """
    organization = organization or resolve_organization()
    doc = _get_messaging_settings(organization)
    token = _get_access_token(doc)
    
    if not doc or not doc.get("whatsapp_enabled"):
        # Log but don't fail - allows testing without credentials
        _log_message(organization, phone, "text", message, status="Sent")
        return {"status": "logged", "message": "WhatsApp not configured, message logged only"}
    
    if not (doc.get("whatsapp_phone_number_id") and token):
        raise frappe.ValidationError("WhatsApp credentials incomplete")
    
    phone_normalized = _normalize_phone(phone)
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_normalized,
        "type": "text",
        "text": {"body": message}
    }
    
    try:
        resp = _send_to_meta(
            doc.get("whatsapp_phone_number_id"),
            token,
            payload,
            doc.get("whatsapp_base_url")
        )
        wamid = resp.get("messages", [{}])[0].get("id")
        _log_message(organization, phone, "text", message, status="Sent", wamid=wamid)
        return {"status": "sent", "message_id": wamid}
    except Exception as e:
        _log_message(organization, phone, "text", message, status="Failed", error=str(e))
        raise


def send_template_message(phone, template_name, language_code, components, organization=None):
    """
    Send a template message via WhatsApp Cloud API.
    
    Templates must be pre-approved by Meta. Use this for proactive messages
    outside the 24-hour window (rent reminders, overdue notices).
    
    Args:
        phone: Recipient phone number
        template_name: Name of approved template (e.g. "rent_reminder")
        language_code: Template language (e.g. "en", "en_US")
        components: List of template components with parameters
        organization: Organization name
    
    Returns:
        dict with message_id and status
    """
    organization = organization or resolve_organization()
    doc = _get_messaging_settings(organization)
    token = _get_access_token(doc)
    
    if not doc or not doc.get("whatsapp_enabled"):
        content = f"Template: {template_name}, Components: {json.dumps(components)}"
        _log_message(organization, phone, "template", content, template_name=template_name, status="Sent")
        return {"status": "logged", "message": "WhatsApp not configured, message logged only"}
    
    if not (doc.get("whatsapp_phone_number_id") and token):
        raise frappe.ValidationError("WhatsApp credentials incomplete")
    
    phone_normalized = _normalize_phone(phone)
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_normalized,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components or []
        }
    }
    
    try:
        resp = _send_to_meta(
            doc.get("whatsapp_phone_number_id"),
            token,
            payload,
            doc.get("whatsapp_base_url")
        )
        wamid = resp.get("messages", [{}])[0].get("id")
        content = f"Template: {template_name}"
        _log_message(organization, phone, "template", content, template_name=template_name,
                     status="Sent", wamid=wamid)
        return {"status": "sent", "message_id": wamid}
    except Exception as e:
        _log_message(organization, phone, "template", f"Template: {template_name}",
                     template_name=template_name, status="Failed", error=str(e))
        raise


def send_interactive_buttons(phone, header_text, body_text, buttons, organization=None,
                             footer_text=None, ref_doctype=None, ref_name=None):
    """
    Send an interactive button message via WhatsApp Cloud API.
    
    Args:
        phone: Recipient phone number
        header_text: Header text (optional, max 60 chars)
        body_text: Main message body (required, max 1024 chars)
        buttons: List of button dicts, each with 'id' and 'title'
                 e.g. [{"id": "pay_stk_INV-001", "title": "Pay Now"}]
                 Max 3 buttons, title max 20 chars
        organization: Organization name
        footer_text: Footer text (optional, max 60 chars)
        ref_doctype: Reference document type (for tracking)
        ref_name: Reference document name
    
    Returns:
        dict with message_id and status
    """
    organization = organization or resolve_organization()
    doc = _get_messaging_settings(organization)
    token = _get_access_token(doc)
    
    if not doc or not doc.get("whatsapp_enabled"):
        content = f"{header_text}\n\n{body_text}"
        _log_message(organization, phone, "interactive", content, status="Sent",
                     ref_doctype=ref_doctype, ref_name=ref_name)
        return {"status": "logged", "message": "WhatsApp not configured, message logged only"}
    
    if not (doc.get("whatsapp_phone_number_id") and token):
        raise frappe.ValidationError("WhatsApp credentials incomplete")
    
    phone_normalized = _normalize_phone(phone)
    
    # Build button objects
    button_objects = []
    for btn in (buttons or [])[:3]:  # Max 3 buttons
        button_objects.append({
            "type": "reply",
            "reply": {
                "id": btn.get("id", "")[:256],
                "title": btn.get("title", "")[:20]
            }
        })
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone_normalized,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text[:1024]},
            "action": {"buttons": button_objects}
        }
    }
    
    # Add optional header
    if header_text:
        payload["interactive"]["header"] = {
            "type": "text",
            "text": header_text[:60]
        }
    
    # Add optional footer
    if footer_text:
        payload["interactive"]["footer"] = {"text": footer_text[:60]}
    
    try:
        resp = _send_to_meta(
            doc.get("whatsapp_phone_number_id"),
            token,
            payload,
            doc.get("whatsapp_base_url")
        )
        wamid = resp.get("messages", [{}])[0].get("id")
        content = f"{header_text or ''}\n\n{body_text}"
        _log_message(organization, phone, "interactive", content, status="Sent", wamid=wamid,
                     ref_doctype=ref_doctype, ref_name=ref_name)
        return {"status": "sent", "message_id": wamid}
    except Exception as e:
        _log_message(organization, phone, "interactive", body_text, status="Failed",
                     error=str(e), ref_doctype=ref_doctype, ref_name=ref_name)
        raise


# ---------------------------------------------------------------------------
# Webhook Endpoint - Receive Messages & Status Updates
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["GET"])
def webhook_verify():
    """
    Meta webhook verification endpoint.
    
    Meta sends a GET request with hub.mode, hub.verify_token, and hub.challenge.
    We verify the token and return the challenge to confirm the webhook.
    """
    mode = frappe.request.args.get("hub.mode")
    token = frappe.request.args.get("hub.verify_token")
    challenge = frappe.request.args.get("hub.challenge")
    
    frappe.logger("whatsapp").info(f"Webhook verify: mode={mode}, token={token[:10]}...")
    
    if mode == "subscribe":
        # Find any Messaging Settings with this verify token
        settings = frappe.get_all(
            "Messaging Settings",
            filters={"whatsapp_webhook_verify_token": token},
            fields=["name"],
            limit=1
        )
        
        if settings or token == "nest_whatsapp_verify":  # Fallback for testing
            frappe.logger("whatsapp").info("Webhook verified successfully")
            frappe.response["type"] = "text"
            frappe.response["text"] = challenge
            return challenge
    
    frappe.logger("whatsapp").warning(f"Webhook verification failed: invalid token")
    frappe.throw("Verification failed", frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook():
    """
    Webhook endpoint to receive WhatsApp events from WAClient/Meta.
    
    Handles:
    - Incoming text messages
    - Interactive button replies (triggers actions like STK push)
    - Message status updates (sent, delivered, read, failed)
    
    URL: /api/method/property_management.api.whatsapp.webhook
    """
    try:
        data = frappe.request.get_json() or {}
        
        # Log raw webhook for debugging
        raw_json = json.dumps(data, indent=2)
        print("=== WHATSAPP WEBHOOK ===")
        print(raw_json[:2000])
        frappe.logger("whatsapp").info(f"Webhook received: {raw_json[:1000]}")
        
        # Handle WAClient format or direct Meta format
        entry = data.get("entry", [])
        if not entry:
            # WAClient might send in a slightly different format
            if data.get("messages") or data.get("statuses"):
                entry = [{"changes": [{"value": data}]}]
        
        for e in entry:
            for change in e.get("changes", []):
                value = change.get("value", {})
                
                # Get phone number ID to identify organization
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                organization = _resolve_org_from_phone_id(phone_number_id)
                
                # Handle incoming messages
                messages = value.get("messages", [])
                for msg in messages:
                    _handle_incoming_message(msg, organization, data)
                
                # Handle status updates
                statuses = value.get("statuses", [])
                for status in statuses:
                    _handle_status_update(status)
        
        return {"status": "ok"}
    
    except Exception:
        frappe.log_error(title="WhatsApp webhook error", message=frappe.get_traceback())
        return {"status": "error"}


def _resolve_org_from_phone_id(phone_number_id):
    """Find organization from WhatsApp phone number ID."""
    if not phone_number_id:
        # Default to first available organization
        org = frappe.db.get_value("Organization", {}, "name")
        return org
    
    settings = frappe.db.get_value(
        "Messaging Settings",
        {"whatsapp_phone_number_id": phone_number_id},
        "organization"
    )
    return settings or frappe.db.get_value("Organization", {}, "name")


def _handle_incoming_message(msg, organization, raw_data):
    """
    Process an incoming WhatsApp message.
    
    Handles text messages and interactive button replies.
    Button replies with "pay_stk_" prefix trigger M-Pesa STK push.
    """
    msg_type = msg.get("type")
    from_phone = msg.get("from")
    wamid = msg.get("id")
    timestamp = msg.get("timestamp")
    
    frappe.logger("whatsapp").info(f"Incoming message: type={msg_type}, from={from_phone}")
    
    if msg_type == "text":
        # Plain text message from user
        text_body = msg.get("text", {}).get("body", "")
        _log_message(
            organization, from_phone, "text", text_body,
            direction="Incoming", status="Delivered", wamid=wamid,
            raw_payload=raw_data
        )
        
        # Auto-reply acknowledgment (optional)
        # Uncomment to enable auto-replies:
        # _send_auto_reply(from_phone, text_body, organization)
    
    elif msg_type == "interactive":
        # Button reply from user
        interactive = msg.get("interactive", {})
        reply_type = interactive.get("type")
        
        if reply_type == "button_reply":
            button_reply = interactive.get("button_reply", {})
            button_id = button_reply.get("id", "")
            button_title = button_reply.get("title", "")
            
            _log_message(
                organization, from_phone, "button_reply", f"Button clicked: {button_title}",
                direction="Incoming", status="Delivered", wamid=wamid,
                button_payload=button_id, button_text=button_title,
                raw_payload=raw_data
            )
            
            # Handle Pay Now button
            if button_id.startswith("pay_stk_"):
                frappe.enqueue(
                    "property_management.api.whatsapp.handle_pay_button",
                    queue="default",
                    phone=from_phone,
                    button_id=button_id,
                    organization=organization
                )
            
            # Handle other button types as needed
            elif button_id.startswith("contact_"):
                _send_support_info(from_phone, organization)
    
    elif msg_type == "image" or msg_type == "document":
        # Media message - log it
        media_id = msg.get(msg_type, {}).get("id")
        caption = msg.get(msg_type, {}).get("caption", "")
        _log_message(
            organization, from_phone, msg_type, f"Media received: {media_id}. Caption: {caption}",
            direction="Incoming", status="Delivered", wamid=wamid,
            raw_payload=raw_data
        )


def _handle_status_update(status):
    """
    Process a message status update (sent, delivered, read, failed).
    Updates the corresponding WhatsApp Message Log entry.
    """
    wamid = status.get("id")
    status_value = status.get("status")  # sent, delivered, read, failed
    timestamp = status.get("timestamp")
    
    frappe.logger("whatsapp").info(f"Status update: wamid={wamid}, status={status_value}")
    
    # Map Meta status to our status values
    status_map = {
        "sent": "Sent",
        "delivered": "Delivered",
        "read": "Read",
        "failed": "Failed"
    }
    
    mapped_status = status_map.get(status_value, status_value)
    
    # Get error info if failed
    error = None
    if status_value == "failed":
        errors = status.get("errors", [])
        if errors:
            error = errors[0].get("message", "") or errors[0].get("title", "")
    
    _update_message_status(wamid, mapped_status, error)


# ---------------------------------------------------------------------------
# Button Action Handlers
# ---------------------------------------------------------------------------

def handle_pay_button(phone, button_id, organization):
    """
    Handle "Pay Now" button click from WhatsApp.
    
    Button ID format: pay_stk_<INVOICE_ID> or pay_stk_<LEASE_ID>
    Triggers M-Pesa STK push to the tenant's phone.
    """
    frappe.set_user("Administrator")
    
    try:
        # Parse the reference from button ID
        # Format: pay_stk_INV-2024-00001 or pay_stk_LA-00001
        ref_id = button_id.replace("pay_stk_", "")
        
        frappe.logger("whatsapp").info(f"Pay button: phone={phone}, ref={ref_id}")
        
        # Normalize phone for M-Pesa
        phone_normalized = _normalize_phone(phone)
        
        # Determine if it's an invoice or lease
        if frappe.db.exists("Sales Invoice", ref_id):
            from property_management.api.mpesa import initiate_invoice_payment
            result = initiate_invoice_payment(invoice=ref_id, phone=phone_normalized)
            
            # Send confirmation message
            send_text_message(
                phone,
                f"✅ M-Pesa payment prompt sent!\n\n"
                f"Amount: KES {result.get('amount', 0):,.0f}\n\n"
                f"Please check your phone and enter your M-Pesa PIN to complete payment.",
                organization
            )
        
        elif frappe.db.exists("Property Invoice", ref_id):
            from property_management.api.mpesa import initiate_invoice_payment
            result = initiate_invoice_payment(invoice=ref_id, phone=phone_normalized)
            
            send_text_message(
                phone,
                f"✅ M-Pesa payment prompt sent!\n\n"
                f"Amount: KES {result.get('amount', 0):,.0f}\n\n"
                f"Please check your phone and enter your M-Pesa PIN to complete payment.",
                organization
            )
        
        elif frappe.db.exists("Lease Agreement", ref_id):
            from property_management.api.mpesa import initiate_onboarding_payment
            result = initiate_onboarding_payment(lease=ref_id, phone=phone_normalized)
            
            send_text_message(
                phone,
                f"✅ M-Pesa payment prompt sent!\n\n"
                f"Amount: KES {result.get('amount', 0):,.0f}\n\n"
                f"Please check your phone and enter your M-Pesa PIN to complete payment.",
                organization
            )
        
        else:
            frappe.logger("whatsapp").warning(f"Pay button: reference not found: {ref_id}")
            send_text_message(
                phone,
                "⚠️ Sorry, we couldn't find the invoice for this payment. "
                "Please contact support or try again from the tenant portal.",
                organization
            )
    
    except Exception as e:
        frappe.log_error(title="WhatsApp pay button error", message=frappe.get_traceback())
        send_text_message(
            phone,
            f"⚠️ Sorry, we couldn't process your payment request.\n\n"
            f"Error: {str(e)[:100]}\n\n"
            f"Please try again or contact support.",
            organization
        )


def _send_support_info(phone, organization):
    """Send support contact information."""
    send_text_message(
        phone,
        "📞 Contact Support\n\n"
        "For assistance with your rental account, please:\n"
        "• Call: 0700 000 000\n"
        "• Email: support@example.com\n"
        "• Visit the tenant portal\n\n"
        "Our team is available Mon-Fri, 8am-5pm.",
        organization
    )


# ---------------------------------------------------------------------------
# High-Level Message Functions (for use by other modules)
# ---------------------------------------------------------------------------

def send_rent_reminder(tenant, invoice, organization=None):
    """
    Send a rent reminder message with Pay Now button.
    
    Args:
        tenant: Property Tenant name or doc
        invoice: Property Invoice or Sales Invoice name
        organization: Organization name
    """
    if isinstance(tenant, str):
        tenant = frappe.get_doc("Property Tenant", tenant)
    
    phone = tenant.get("phone")
    if not phone:
        frappe.logger("whatsapp").warning(f"No phone for tenant {tenant.name}")
        return
    
    # Get invoice details
    if frappe.db.exists("Sales Invoice", invoice):
        inv = frappe.db.get_value("Sales Invoice", invoice,
                                   ["outstanding_amount", "due_date", "name"], as_dict=True)
        amount = inv.outstanding_amount
        due_date = inv.due_date
        inv_id = inv.name
    else:
        inv = frappe.get_doc("Property Invoice", invoice)
        amount = inv.outstanding_amount
        due_date = inv.due_date
        inv_id = inv.name
    
    organization = organization or inv.get("organization") or resolve_organization()
    
    # Get unit info
    unit_info = ""
    lease = frappe.db.get_value("Lease Agreement", {"tenant": tenant.name, "status": "Active"},
                                 ["unit", "property"], as_dict=True)
    if lease:
        unit_info = f"Unit: {lease.unit}\n"
    
    body = (
        f"Hello {tenant.tenant_name},\n\n"
        f"This is a reminder that your rent payment is due.\n\n"
        f"💰 Amount: KES {flt(amount):,.0f}\n"
        f"📅 Due Date: {due_date}\n"
        f"{unit_info}\n"
        f"Tap 'Pay Now' to pay instantly via M-Pesa."
    )
    
    buttons = [
        {"id": f"pay_stk_{inv_id}", "title": "Pay Now"},
        {"id": "contact_support", "title": "Contact Support"}
    ]
    
    return send_interactive_buttons(
        phone=phone,
        header_text="📅 Rent Reminder",
        body_text=body,
        buttons=buttons,
        organization=organization,
        footer_text="Nest Property Management",
        ref_doctype="Property Invoice" if frappe.db.exists("Property Invoice", invoice) else "Sales Invoice",
        ref_name=invoice
    )


def send_overdue_notice(tenant, invoice, days_overdue, organization=None):
    """
    Send an overdue payment notice with Pay Now button.
    """
    if isinstance(tenant, str):
        tenant = frappe.get_doc("Property Tenant", tenant)
    
    phone = tenant.get("phone")
    if not phone:
        return
    
    # Get invoice details
    if frappe.db.exists("Sales Invoice", invoice):
        inv = frappe.db.get_value("Sales Invoice", invoice,
                                   ["outstanding_amount", "name"], as_dict=True)
        amount = inv.outstanding_amount
        inv_id = inv.name
    else:
        inv = frappe.get_doc("Property Invoice", invoice)
        amount = inv.outstanding_amount
        inv_id = inv.name
    
    organization = organization or resolve_organization()
    
    body = (
        f"Hello {tenant.tenant_name},\n\n"
        f"⚠️ Your rent payment is {days_overdue} days overdue.\n\n"
        f"💰 Outstanding: KES {flt(amount):,.0f}\n\n"
        f"Please pay immediately to avoid late fees and service disruption.\n\n"
        f"Tap 'Pay Now' to pay via M-Pesa."
    )
    
    buttons = [
        {"id": f"pay_stk_{inv_id}", "title": "Pay Now"},
        {"id": "contact_support", "title": "Contact Support"}
    ]
    
    return send_interactive_buttons(
        phone=phone,
        header_text="⚠️ Payment Overdue",
        body_text=body,
        buttons=buttons,
        organization=organization,
        footer_text="Nest Property Management",
        ref_doctype="Property Invoice" if frappe.db.exists("Property Invoice", invoice) else "Sales Invoice",
        ref_name=invoice
    )


def send_payment_confirmation(tenant, amount, mpesa_receipt, balance=0, organization=None):
    """
    Send payment confirmation message after successful M-Pesa payment.
    """
    if isinstance(tenant, str):
        tenant = frappe.get_doc("Property Tenant", tenant)
    
    phone = tenant.get("phone")
    if not phone:
        return
    
    organization = organization or resolve_organization()
    
    message = (
        f"✅ Payment Received!\n\n"
        f"Thank you, {tenant.tenant_name}!\n\n"
        f"💰 Amount: KES {flt(amount):,.0f}\n"
        f"📝 M-Pesa Ref: {mpesa_receipt}\n"
        f"📊 New Balance: KES {flt(balance):,.0f}\n\n"
        f"Your payment has been recorded."
    )
    
    return send_text_message(phone, message, organization)


def send_invoice_notification(tenant, invoice, organization=None):
    """
    Send notification when a new invoice is generated.
    """
    if isinstance(tenant, str):
        tenant = frappe.get_doc("Property Tenant", tenant)
    
    phone = tenant.get("phone")
    if not phone:
        return
    
    # Get invoice details
    if frappe.db.exists("Sales Invoice", invoice):
        inv = frappe.db.get_value("Sales Invoice", invoice,
                                   ["grand_total", "due_date", "name"], as_dict=True)
        amount = inv.grand_total
        due_date = inv.due_date
        inv_id = inv.name
    else:
        inv = frappe.get_doc("Property Invoice", invoice)
        amount = inv.total_amount
        due_date = inv.due_date
        inv_id = inv.name
    
    organization = organization or resolve_organization()
    
    body = (
        f"Hello {tenant.tenant_name},\n\n"
        f"📄 Your invoice for this month is ready.\n\n"
        f"Invoice: {inv_id}\n"
        f"💰 Amount: KES {flt(amount):,.0f}\n"
        f"📅 Due: {due_date}\n\n"
        f"Tap 'Pay Now' to pay via M-Pesa."
    )
    
    buttons = [
        {"id": f"pay_stk_{inv_id}", "title": "Pay Now"},
        {"id": "contact_support", "title": "Contact Support"}
    ]
    
    return send_interactive_buttons(
        phone=phone,
        header_text="📄 Invoice Ready",
        body_text=body,
        buttons=buttons,
        organization=organization,
        footer_text="Nest Property Management",
        ref_doctype="Property Invoice" if frappe.db.exists("Property Invoice", invoice) else "Sales Invoice",
        ref_name=invoice
    )


# ---------------------------------------------------------------------------
# API Endpoints (Whitelisted)
# ---------------------------------------------------------------------------

@frappe.whitelist()
@envelope
def send_message(phone, message, organization=None):
    """API endpoint to send a text message."""
    organization = organization or resolve_organization()
    return send_text_message(phone, message, organization)


@frappe.whitelist()
@envelope
def send_template(phone, template_name, language="en", components=None, organization=None):
    """API endpoint to send a template message."""
    organization = organization or resolve_organization()
    if isinstance(components, str):
        components = frappe.parse_json(components)
    return send_template_message(phone, template_name, language, components or [], organization)


@frappe.whitelist()
@envelope
def send_buttons(phone, header, body, buttons, footer=None, organization=None):
    """API endpoint to send an interactive button message."""
    organization = organization or resolve_organization()
    if isinstance(buttons, str):
        buttons = frappe.parse_json(buttons)
    return send_interactive_buttons(phone, header, body, buttons, organization, footer)


@frappe.whitelist()
def send_invoice_whatsapp(invoice_id):
    """
    Legacy endpoint - sends invoice notification via WhatsApp.
    Kept for backward compatibility.
    """
    if not invoice_id:
        frappe.throw(_("Invoice ID is required."))
    
    if frappe.db.exists("Sales Invoice", invoice_id):
        invoice = frappe.get_doc("Sales Invoice", invoice_id)
        tenant_name = frappe.db.get_value("Property Tenant", {"tenant_name": invoice.customer}, "name")
    else:
        invoice = frappe.get_doc("Property Invoice", invoice_id)
        tenant_name = invoice.tenant
    
    if not tenant_name:
        frappe.throw(_("Could not find tenant for invoice."))
    
    tenant = frappe.get_doc("Property Tenant", tenant_name)
    
    frappe.enqueue(
        "property_management.api.whatsapp.send_invoice_notification",
        queue="default",
        tenant=tenant.name,
        invoice=invoice_id,
        organization=invoice.get("organization") or resolve_organization()
    )
    
    return {"status": "Queued", "message": f"WhatsApp notification queued for invoice {invoice_id}"}


@frappe.whitelist()
@envelope
def get_messages(organization=None, phone=None, direction=None, limit=50):
    """
    Get WhatsApp message history.
    
    Args:
        organization: Filter by organization
        phone: Filter by phone number
        direction: Filter by Incoming/Outgoing
        limit: Max records to return
    """
    organization = organization or resolve_organization()
    
    filters = {"organization": organization}
    if phone:
        filters["recipient_phone"] = ["like", f"%{phone}%"]
    if direction:
        filters["direction"] = direction
    
    messages = frappe.get_all(
        "WhatsApp Message Log",
        filters=filters,
        fields=["name", "direction", "recipient_phone", "message_type", "message_content",
                "status", "timestamp", "template_name", "button_payload"],
        order_by="creation desc",
        limit=int(limit)
    )
    
    return messages
