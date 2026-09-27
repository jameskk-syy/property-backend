# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Token-based Authentication API using Frappe's native API Key/Secret system

Features:
- Uses Frappe's built-in api_key and api_secret
- Generates tokens on login
- Token format: api_key:api_secret
- Auto-generates keys for users who don't have them
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, cint
import hashlib
from functools import wraps


def _get_user_info(user_id):
    """Get user details for token response."""
    # Check if it's Administrator
    if user_id == 'Administrator':
        return {
            'user_id': 'Administrator',
            'email': 'admin@nest.co.ke',
            'name': 'Administrator',
            'role': 'admin'
        }
    
    # Try to find user in Frappe User doctype
    if frappe.db.exists('User', user_id):
        user = frappe.get_doc('User', user_id)
        
        # Determine role from user roles
        roles = [r.role for r in user.roles]
        if 'Administrator' in roles or 'System Manager' in roles:
            role = 'admin'
        elif 'Director' in roles:
            role = 'admin'
        else:
            # Check if caretaker, landlord, or tenant
            if frappe.db.exists('Caretaker', {'user': user_id}):
                role = 'caretaker'
            elif frappe.db.exists('Landlord', {'user': user_id}):
                role = 'landlord'
            elif frappe.db.exists('Property Tenant', {'user': user_id}):
                role = 'tenant'
            else:
                role = 'caretaker'  # Default
        
        return {
            'user_id': user_id,
            'email': user.email or user_id,
            'name': user.full_name or user.first_name or user_id,
            'role': role
        }
    
    return None


def _generate_api_keys(user):
    """Generate API key and secret for a user if they don't have them."""
    user_doc = frappe.get_doc('User', user)
    
    # Check if user already has api_key
    api_key = user_doc.api_key
    
    if not api_key:
        # Generate new keys using Frappe's built-in method
        from frappe.core.doctype.user.user import generate_keys
        generate_keys(user)
        frappe.db.commit()
        user_doc.reload()
        api_key = user_doc.api_key
    
    # Get the api_secret
    api_secret = frappe.utils.password.get_decrypted_password(
        'User', user, 'api_secret'
    )
    
    return api_key, api_secret


@frappe.whitelist(allow_guest=True)
def login(email, password):
    """
    Authenticate user and return API token.
    Supports login via: username, email, or phone number.
    """
    try:
        from frappe.utils.password import check_password
        
        # Find user by email, username, or phone
        user_id = email
        
        if '@' in email:
            # Email login
            user_id = frappe.db.get_value('User', {'email': email}, 'name') or email
        elif email.isdigit() or (email.startswith('+') and email[1:].replace(' ', '').isdigit()):
            # Phone number login - find user by phone
            phone_user = _find_user_by_phone(email)
            if phone_user:
                user_id = phone_user
            else:
                frappe.throw(_('No account found with this phone number'), frappe.AuthenticationError)
        # else: assume it's a username (like "Administrator")
        
        # Verify password
        try:
            check_password(user_id, password)
        except frappe.AuthenticationError:
            frappe.throw(_('Invalid email or password'), frappe.AuthenticationError)
        
        # Create a proper Frappe session (sets session cookie)
        from frappe.auth import LoginManager
        login_manager = LoginManager()
        login_manager.user = user_id
        login_manager.post_login()
        
        # Get user data in single query - include phone fields
        user_data = frappe.db.get_value('User', user_id, 
            ['name', 'email', 'full_name', 'first_name', 'api_key', 'mobile_no', 'phone'], as_dict=True)
        
        if not user_data:
            frappe.throw(_('User not found'), frappe.AuthenticationError)
        
        # Determine role
        role = 'admin' if user_id == 'Administrator' else 'caretaker'
        if user_id != 'Administrator':
            has_admin = frappe.db.exists('Has Role', {'parent': user_id, 'role': ['in', ['Administrator', 'System Manager', 'Director']]})
            if has_admin:
                role = 'admin'
            else:
                if frappe.db.exists('Caretaker', {'user': user_id}):
                    role = 'caretaker'
                elif frappe.db.exists('Landlord', {'user': user_id}):
                    role = 'landlord'
                elif frappe.db.exists('Property Tenant', {'user': user_id}):
                    role = 'tenant'
        
        # Get or generate API keys
        api_key = user_data.get('api_key')
        api_secret = None
        
        if api_key:
            api_secret = frappe.utils.password.get_decrypted_password('User', user_id, 'api_secret')
        
        if not api_key or not api_secret:
            # Use Administrator context to generate keys (requires System Manager role)
            original_user = frappe.session.user
            frappe.set_user('Administrator')
            try:
                from frappe.core.doctype.user.user import generate_keys
                generate_keys(user_id)
                frappe.db.commit()
            finally:
                frappe.set_user(original_user)
            api_key = frappe.db.get_value('User', user_id, 'api_key')
            api_secret = frappe.utils.password.get_decrypted_password('User', user_id, 'api_secret')
        
        token = f"{api_key}:{api_secret}"
        display_name = user_data.get('full_name') or user_data.get('first_name') or user_id
        
        # Get phone number - prefer mobile_no, fallback to phone field
        phone = user_data.get('mobile_no') or user_data.get('phone') or None
        phone_normalized = _normalize_phone(phone) if phone else None
        
        return {
            'status': 'success',
            'token': token,
            'api_key': api_key,
            'api_secret': api_secret,
            'token_type': 'token',
            'user': {
                'id': user_id,
                'name': display_name,
                'email': user_data.get('email') or f'{user_id}@nest.co.ke',
                'phone': phone_normalized,
                'role': role
            }
        }
        
    except frappe.AuthenticationError:
        raise
    except Exception as e:
        frappe.log_error(f'Login error: {str(e)}')
        frappe.throw(_('Invalid email or password'), frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True)
def verify(token=None):
    """
    Verify a token and return user info.
    
    Args:
        token: API token (api_key:api_secret) or from Authorization header
        
    Returns:
        dict: { valid: bool, user: {...} }
    """
    if not token:
        # Try to get from header
        auth_header = frappe.get_request_header('Authorization')
        if auth_header and auth_header.startswith('token '):
            token = auth_header[6:]  # Remove 'token ' prefix
    
    if not token:
        return {
            'status': 'error',
            'valid': False,
            'message': 'No token provided'
        }
    
    try:
        # Parse token
        if ':' not in token:
            return {
                'status': 'error',
                'valid': False,
                'message': 'Invalid token format'
            }
        
        api_key, api_secret = token.split(':', 1)
        
        # Find user by api_key
        user = frappe.db.get_value('User', {'api_key': api_key}, 'name')
        
        if not user:
            return {
                'status': 'error',
                'valid': False,
                'message': 'Invalid token'
            }
        
        # Verify api_secret
        stored_secret = frappe.utils.password.get_decrypted_password(
            'User', user, 'api_secret'
        )
        
        if stored_secret != api_secret:
            return {
                'status': 'error',
                'valid': False,
                'message': 'Invalid token'
            }
        
        # Get user info
        user_info = _get_user_info(user)
        
        return {
            'status': 'success',
            'valid': True,
            'user': {
                'id': user_info['user_id'],
                'email': user_info['email'],
                'name': user_info['name'],
                'role': user_info['role']
            } if user_info else None
        }
        
    except Exception as e:
        return {
            'status': 'error',
            'valid': False,
            'message': str(e)
        }


@frappe.whitelist()
def logout():
    """
    Logout user (clears Frappe session).
    Note: API tokens remain valid until regenerated.
    """
    try:
        frappe.local.login_manager.logout()
    except:
        pass
    
    return {
        'status': 'success',
        'message': 'Logged out successfully'
    }


@frappe.whitelist()
def regenerate_token():
    """
    Regenerate API token for current user.
    Invalidates the old token.
    
    Returns:
        dict: { token, api_key, api_secret }
    """
    user = frappe.session.user
    
    if not user or user == 'Guest':
        frappe.throw(_('Not authenticated'), frappe.AuthenticationError)
    
    # Generate new keys
    from frappe.core.doctype.user.user import generate_keys
    generate_keys(user)
    frappe.db.commit()
    
    # Get new keys
    user_doc = frappe.get_doc('User', user)
    api_key = user_doc.api_key
    api_secret = frappe.utils.password.get_decrypted_password(
        'User', user, 'api_secret'
    )
    
    token = f"{api_key}:{api_secret}"
    
    return {
        'status': 'success',
        'token': token,
        'api_key': api_key,
        'api_secret': api_secret,
        'message': 'Token regenerated successfully. Old token is now invalid.'
    }


@frappe.whitelist(allow_guest=True)
def get_current_user():
    """
    Get current authenticated user from token or session.
    
    Returns:
        dict: User info or Guest
    """
    # Try token auth first
    auth_header = frappe.get_request_header('Authorization')
    
    if auth_header and auth_header.startswith('token '):
        token = auth_header[6:]
        result = verify(token)
        if result.get('valid'):
            return result.get('user')
    
    # Fall back to session
    user = frappe.session.user
    if user and user != 'Guest':
        user_info = _get_user_info(user)
        return user_info
    
    return {'id': 'Guest', 'name': 'Guest', 'email': '', 'role': 'guest'}


def require_token(fn):
    """
    Decorator to require valid API token for endpoints.
    
    Usage:
        @frappe.whitelist(allow_guest=True)
        @require_token
        def my_protected_api():
            user = frappe.local.token_user
            # ... do stuff
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = frappe.get_request_header('Authorization')
        
        if not auth_header:
            frappe.throw(_('Authorization header required'), frappe.AuthenticationError)
        
        if not auth_header.startswith('token '):
            frappe.throw(_('Invalid authorization format. Use: token api_key:api_secret'), frappe.AuthenticationError)
        
        token = auth_header[6:]
        result = verify(token)
        
        if not result.get('valid'):
            frappe.throw(_(result.get('message', 'Invalid token')), frappe.AuthenticationError)
        
        # Store user info for use in the endpoint
        frappe.local.token_user = result.get('user')
        
        # Set Frappe user context
        user_id = result['user']['id']
        frappe.set_user(user_id)
        
        return fn(*args, **kwargs)
    
    return wrapper


def require_role(*allowed_roles):
    """
    Decorator to require specific roles.
    
    Usage:
        @frappe.whitelist(allow_guest=True)
        @require_token
        @require_role('admin', 'director')
        def admin_only_api():
            # ... do stuff
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            token_user = getattr(frappe.local, 'token_user', None)
            
            if not token_user:
                frappe.throw(_('Authentication required'), frappe.AuthenticationError)
            
            user_role = token_user.get('role', '')
            
            if user_role not in allowed_roles:
                frappe.throw(
                    _('Access denied. Required roles: {0}').format(', '.join(allowed_roles)),
                    frappe.PermissionError
                )
            
            return fn(*args, **kwargs)
        
        return wrapper
    return decorator


# --------------------------------------------------------------------------
# OTP-based Authentication (WhatsApp / SMS)
# --------------------------------------------------------------------------

import random
import string
from frappe.utils import now_datetime, add_to_date, get_datetime

# OTP Configuration
OTP_LENGTH = 6
OTP_EXPIRY_MINUTES = 10
DEV_MODE = True  # Set to False in production to actually send WhatsApp


def _generate_otp():
    """Generate a random numeric OTP."""
    return ''.join(random.choices(string.digits, k=OTP_LENGTH))


def _normalize_phone(phone):
    """Normalize phone number to digits only, with country code."""
    if not phone:
        return None
    # Remove all non-digits
    digits = ''.join(ch for ch in str(phone) if ch.isdigit())
    # Handle Kenyan numbers
    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif digits.startswith('7') and len(digits) == 9:
        digits = '254' + digits
    elif digits.startswith('1') and len(digits) == 9:
        digits = '254' + digits
    return digits


def _find_user_by_phone(phone):
    """Find a Frappe User by phone number."""
    digits = _normalize_phone(phone)
    if not digits:
        return None
    
    # Check for synthetic email format (phone@nest.local)
    synthetic_email = f"{digits}@nest.local"
    if frappe.db.exists("User", synthetic_email):
        return synthetic_email
    
    # Check mobile_no field
    user = frappe.db.get_value("User", {"mobile_no": ["like", f"%{digits[-9:]}%"]}, "name")
    if user:
        return user
    
    # Check phone field
    user = frappe.db.get_value("User", {"phone": ["like", f"%{digits[-9:]}%"]}, "name")
    if user:
        return user
    
    return None


def _store_otp(user_id, otp):
    """Store OTP in a custom field or cache."""
    expiry = add_to_date(now_datetime(), minutes=OTP_EXPIRY_MINUTES)
    # Store in cache (Redis) with expiry
    cache_key = f"otp:{user_id}"
    frappe.cache().set_value(cache_key, {
        "otp": otp,
        "expiry": str(expiry),
        "attempts": 0
    }, expires_in_sec=OTP_EXPIRY_MINUTES * 60)


def _verify_stored_otp(user_id, otp):
    """Verify OTP from cache."""
    cache_key = f"otp:{user_id}"
    stored = frappe.cache().get_value(cache_key)
    
    if not stored:
        return False, "OTP expired or not found. Please request a new one."
    
    # Check expiry
    expiry = get_datetime(stored.get("expiry"))
    if get_datetime(now_datetime()) > expiry:
        frappe.cache().delete_value(cache_key)
        return False, "OTP has expired. Please request a new one."
    
    # Check attempts (max 3)
    attempts = stored.get("attempts", 0)
    if attempts >= 3:
        frappe.cache().delete_value(cache_key)
        return False, "Too many failed attempts. Please request a new OTP."
    
    # Verify OTP
    if stored.get("otp") != otp:
        # Increment attempts
        stored["attempts"] = attempts + 1
        frappe.cache().set_value(cache_key, stored, expires_in_sec=OTP_EXPIRY_MINUTES * 60)
        return False, f"Invalid OTP. {3 - stored['attempts']} attempts remaining."
    
    # OTP is valid - clear it
    frappe.cache().delete_value(cache_key)
    return True, "OTP verified successfully."


def _send_otp_whatsapp(phone, otp):
    """Send OTP via WhatsApp. In dev mode, just log it."""
    if DEV_MODE:
        frappe.log_error(f"DEV MODE - OTP for {phone}: {otp}", "OTP Debug")
        return True
    
    # Production: Send via WhatsApp API
    try:
        from property_management.integration.whatsapp import send_whatsapp_message
        message = f"Your Nest login code is: {otp}\n\nThis code expires in {OTP_EXPIRY_MINUTES} minutes. Do not share it with anyone."
        send_whatsapp_message(phone, message)
        return True
    except Exception as e:
        frappe.log_error(f"WhatsApp OTP send failed: {str(e)}")
        return False


@frappe.whitelist(allow_guest=True)
def request_otp(phone):
    """
    Request OTP for phone-based login.
    
    Args:
        phone: Phone number (any format)
        
    Returns:
        dict: { status, message, dev_otp? (only in dev mode) }
    """
    if not phone:
        return {
            "status": "error",
            "message": "Phone number is required"
        }
    
    normalized = _normalize_phone(phone)
    if not normalized or len(normalized) < 10:
        return {
            "status": "error",
            "message": "Invalid phone number format"
        }
    
    # Find user by phone
    user_id = _find_user_by_phone(phone)
    
    if not user_id:
        return {
            "status": "error",
            "message": "No account found with this phone number. Please contact your administrator."
        }
    
    # Check if user is enabled
    user_enabled = frappe.db.get_value("User", user_id, "enabled")
    if not user_enabled:
        return {
            "status": "error",
            "message": "This account has been disabled. Please contact your administrator."
        }
    
    # Generate OTP
    otp = _generate_otp()
    
    # Store OTP
    _store_otp(user_id, otp)
    
    # Send OTP (WhatsApp or log in dev mode)
    sent = _send_otp_whatsapp(normalized, otp)
    
    response = {
        "status": "success",
        "message": f"OTP sent to your WhatsApp. Valid for {OTP_EXPIRY_MINUTES} minutes.",
        "phone_masked": f"****{normalized[-4:]}"
    }
    
    # In dev mode, include OTP in response for testing
    if DEV_MODE:
        response["dev_otp"] = otp
        response["dev_mode"] = True
    
    return response


@frappe.whitelist(allow_guest=True)
def verify_otp(phone, otp):
    """
    Verify OTP and login user.
    
    Args:
        phone: Phone number used to request OTP
        otp: The OTP code
        
    Returns:
        dict: { status, token, user } on success, { status, message } on failure
    """
    if not phone or not otp:
        return {
            "status": "error",
            "message": "Phone number and OTP are required"
        }
    
    # Find user
    user_id = _find_user_by_phone(phone)
    
    if not user_id:
        return {
            "status": "error",
            "message": "No account found with this phone number."
        }
    
    # Verify OTP
    valid, message = _verify_stored_otp(user_id, otp)
    
    if not valid:
        return {
            "status": "error",
            "message": message
        }
    
    # OTP is valid - log the user in
    try:
        # Set session user
        frappe.set_user(user_id)
        
        # Get user data
        user_data = frappe.db.get_value('User', user_id, 
            ['name', 'email', 'full_name', 'first_name', 'api_key', 'mobile_no'], as_dict=True)
        
        # Determine role
        role = 'tenant'  # Default for phone-only users
        if frappe.db.exists('Caretaker', {'user': user_id}):
            role = 'caretaker'
        elif frappe.db.exists('Landlord', {'user': user_id}):
            role = 'landlord'
        elif frappe.db.exists('Property Tenant', {'user': user_id}):
            role = 'tenant'
        
        # Check for admin roles
        has_admin = frappe.db.exists('Has Role', {
            'parent': user_id, 
            'role': ['in', ['Administrator', 'System Manager', 'Director']]
        })
        if has_admin:
            role = 'admin'
        
        # Get or generate API keys
        api_key = user_data.get('api_key')
        api_secret = None
        
        if api_key:
            api_secret = frappe.utils.password.get_decrypted_password('User', user_id, 'api_secret')
        
        if not api_key or not api_secret:
            # Use Administrator context to generate keys (requires System Manager role)
            original_user = frappe.session.user
            frappe.set_user('Administrator')
            try:
                from frappe.core.doctype.user.user import generate_keys
                generate_keys(user_id)
                frappe.db.commit()
            finally:
                frappe.set_user(original_user)
            api_key = frappe.db.get_value('User', user_id, 'api_key')
            api_secret = frappe.utils.password.get_decrypted_password('User', user_id, 'api_secret')
        
        token = f"{api_key}:{api_secret}"
        display_name = user_data.get('full_name') or user_data.get('first_name') or user_id.split('@')[0]
        
        return {
            "status": "success",
            "message": "Login successful",
            "token": token,
            "api_key": api_key,
            "api_secret": api_secret,
            "user": {
                "id": user_id,
                "name": display_name,
                "email": user_data.get('email') or user_id,
                "phone": user_data.get('mobile_no') or _normalize_phone(phone),
                "role": role
            }
        }
        
    except Exception as e:
        frappe.log_error(f"OTP login error: {str(e)}")
        return {
            "status": "error",
            "message": "Login failed. Please try again."
        }


@frappe.whitelist(allow_guest=True)
def resend_otp(phone):
    """
    Resend OTP to phone number.
    Same as request_otp but with rate limiting message.
    """
    return request_otp(phone)
