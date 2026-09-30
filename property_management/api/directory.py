# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

"""
Directory API - create & bulk-import Landlords and Caretakers.

These endpoints auto-resolve the Organization (which is required on the
DocTypes) so the frontend doesn't have to send it, and accept the fields the
create/import forms collect. All responses use the v2 envelope shape.
"""

import frappe
from frappe.utils import flt

from property_management.api.v2 import envelope
from property_management.api.utils import resolve_organization
def _file_to_base64(file_url):
    """Convert a Frappe file URL to base64 data URL for frontend display."""
    if not file_url:
        return None
    if file_url.startswith("data:"):
        return file_url
    try:
        import base64, os, mimetypes
        if file_url.startswith("/files/"):
            file_name = file_url.replace("/files/", "")
            file_path = os.path.join(frappe.get_site_path(), "public", "files", file_name)
        elif file_url.startswith("/private/files/"):
            file_name = file_url.replace("/private/files/", "")
            file_path = os.path.join(frappe.get_site_path(), "private", "files", file_name)
        else:
            return file_url
        if not os.path.exists(file_path):
            return file_url
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "image/jpeg"
        with open(file_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime_type};base64,{data}"
    except Exception:
        return file_url




# --------------------------------------------------------------------------
# User + role provisioning (shared by landlord / caretaker / tenant onboarding)
# --------------------------------------------------------------------------

import secrets
import string

# Desk access per role. Caretaker uses the desk-scoped app; Tenant/Landlord are
# portal-only (no desk), with no Module Permission rows => all module perms false.
_ROLE_DESK_ACCESS = {"Caretaker": 1, "Landlord": 0, "Tenant": 0}


def _generate_random_password(length=10):
	"""Generate a secure random password with letters, digits, and special chars."""
	alphabet = string.ascii_letters + string.digits + "!@#$%"
	# Ensure at least one of each type
	password = [
		secrets.choice(string.ascii_lowercase),
		secrets.choice(string.ascii_uppercase),
		secrets.choice(string.digits),
		secrets.choice("!@#$%"),
	]
	# Fill the rest
	password += [secrets.choice(alphabet) for _ in range(length - 4)]
	# Shuffle
	secrets.SystemRandom().shuffle(password)
	return "".join(password)


def _send_credentials_notification(user_id, password, full_name, phone, email, role, organization=None):
	"""
	Send login credentials to user via enabled messaging channels (WhatsApp, SMS, Email).
	Uses transactional messaging mode from Messaging Settings.
	"""
	try:
		# Resolve organization if not provided
		if not organization:
			organization = resolve_organization()
		
		# Get messaging settings
		msg_settings = None
		if organization:
			name = frappe.db.get_value("Messaging Settings", {"organization": organization}, "name")
			if name:
				msg_settings = frappe.get_doc("Messaging Settings", name)
		
		# Build credentials message
		login_id = email if email and not email.endswith("@nest.local") else phone
		message = (
			f"Hello {full_name},\n\n"
			f"Your Nest Property Management account has been created.\n\n"
			f"🔑 Login Details:\n"
			f"Username: {login_id}\n"
			f"Password: {password}\n\n"
			f"Role: {role}\n\n"
			f"Please change your password after first login for security.\n\n"
			f"- Nest Property Management"
		)
		
		sent_via = []
		
		# Send via WhatsApp if enabled
		if phone and msg_settings and msg_settings.get("whatsapp_enabled"):
			try:
				from property_management.api.whatsapp import send_text_message
				send_text_message(phone, message, organization)
				sent_via.append("whatsapp")
			except Exception as e:
				frappe.log_error(f"Credentials WhatsApp failed: {e}", "Credentials Notification")
		
		# Send via SMS if enabled
		if phone and msg_settings and msg_settings.get("sms_enabled"):
			try:
				from property_management.api.messaging import _dispatch_sms
				# Shorter SMS version
				sms_msg = f"Nest Login - User: {login_id}, Password: {password}. Change password after login."
				_dispatch_sms(phone, sms_msg, organization)
				sent_via.append("sms")
			except Exception as e:
				frappe.log_error(f"Credentials SMS failed: {e}", "Credentials Notification")
		
		# Send via Email if enabled and email is real (not synthetic @nest.local)
		if email and not email.endswith("@nest.local") and msg_settings and msg_settings.get("email_enabled"):
			try:
				from property_management.api.messaging import _dispatch_email
				_dispatch_email(email, message, "Your Nest Property Management Account", organization)
				sent_via.append("email")
			except Exception as e:
				frappe.log_error(f"Credentials Email failed: {e}", "Credentials Notification")
		
		if sent_via:
			frappe.logger().info(f"Credentials sent to {user_id} via: {', '.join(sent_via)}")
		else:
			frappe.logger().warning(f"No messaging channel enabled to send credentials to {user_id}")
		
		return sent_via
	except Exception as e:
		frappe.log_error(f"Failed to send credentials: {e}", "Credentials Notification")
		return []


def _parse_datetime(value):
	"""
	Normalize a client datetime (e.g. ISO '2026-09-15T21:39:19.223Z') into a
	MariaDB-acceptable 'YYYY-MM-DD HH:MM:SS'. Returns None for empty input, or
	the current datetime as a safe fallback if parsing fails.
	"""
	if not value:
		return None
	try:
		from frappe.utils import get_datetime
		s = str(value).replace("Z", "").replace("T", " ")
		# Drop milliseconds if present.
		if "." in s:
			s = s.split(".")[0]
		return get_datetime(s)
	except Exception:
		from frappe.utils import now_datetime
		return now_datetime()


def _resolve_role_name(role):
	"""
	Match a desired role name against existing Roles case-insensitively so we
	reuse the roles already configured (e.g. 'Caretaker'). Returns the actual
	stored role name if a case-insensitive match exists, else the input unchanged.
	"""
	if not role:
		return role
	for r in frappe.get_all("Role", pluck="name"):
		if r.lower() == str(role).strip().lower():
			return r
	return role


def _ensure_role(role):
	"""Ensure a Frappe Role exists (created with no Module Permission rows => all
	module permissions effectively false). Returns the resolved role name."""
	role_name = _resolve_role_name(role)
	if role_name and not frappe.db.exists("Role", role_name):
		frappe.get_doc({
			"doctype": "Role",
			"role_name": role_name,
			"desk_access": _ROLE_DESK_ACCESS.get(role_name, 0),
		}).insert(ignore_permissions=True)
	return role_name


def _phone_digits(phone):
	return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _ensure_user_with_role(email, full_name, role, phone=None, organization=None):
	"""
	Create (or reuse) a Frappe User for a person, keyed by email when available
	else by a synthetic address derived from the phone number, so the account can
	be linked/identified by phone. Grants the given role (matched
	case-insensitively; created if missing).
	
	For NEW users:
	- Generates a random secure password (not hardcoded)
	- Sends credentials via enabled messaging channels (WhatsApp/SMS/Email)
	
	Returns the user id, or None if neither email nor phone is given.
	Idempotent for existing users.
	"""
	email = (email or "").strip()
	digits = _phone_digits(phone)

	# Determine the user id (User.name == email field in Frappe).
	if email:
		user_id = email
	elif digits:
		# Synthetic address so the account exists and is findable by phone.
		user_id = f"{digits}@nest.local"
	else:
		return None

	role_name = _ensure_role(role)

	parts = (full_name or user_id.split("@")[0]).strip().split(" ", 1)
	first_name = parts[0] or "User"
	last_name = parts[1] if len(parts) > 1 else ""

	is_new = not frappe.db.exists("User", user_id)
	if is_new:
		# Generate random password for new users
		password = _generate_random_password()
		
		user = frappe.get_doc({
			"doctype": "User",
			"email": user_id,
			"first_name": first_name,
			"last_name": last_name,
			"mobile_no": phone or None,
			"phone": phone or None,
			"send_welcome_email": 0,
			"user_type": "System User",
			"new_password": password,
		})
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)
		
		# Send credentials notification via enabled messaging channels
		frappe.enqueue(
			_send_credentials_notification,
			user_id=user_id,
			password=password,
			full_name=full_name,
			phone=phone,
			email=email if email and not email.endswith("@nest.local") else None,
			role=role_name,
			organization=organization,
			queue="short",
			now=frappe.flags.in_test,
		)
	else:
		user = frappe.get_doc("User", user_id)
		if phone and not user.mobile_no:
			user.mobile_no = phone

	# Grant the role if not already present.
	if role_name and role_name not in {r.role for r in user.get("roles", [])}:
		user.append("roles", {"role": role_name})
		user.save(ignore_permissions=True)

	return user.name


# --------------------------------------------------------------------------
# Landlords
# --------------------------------------------------------------------------

def _upsert_landlord(row, organization):
	"""Create (or return existing) a Landlord from a dict of import/form fields."""
	name = (row.get("landlord_name") or row.get("name") or "").strip()
	if not name:
		raise frappe.ValidationError("Landlord name is required")

	email = row.get("email_address") or row.get("email")
	phone = row.get("phone_number") or row.get("phone")

	if frappe.db.exists("Landlord", name):
		doc = frappe.get_doc("Landlord", name)
		doc.phone = phone or doc.phone
		doc.email = email or doc.email
		# Backfill the user link/role for existing records too.
		if not doc.get("user"):
			doc.user = _ensure_user_with_role(email, name, "Landlord", phone, organization)
		doc.save(ignore_permissions=True)
		return doc, False

	user_id = _ensure_user_with_role(email, name, "Landlord", phone, organization)

	doc = frappe.get_doc({
		"doctype": "Landlord",
		"organization": organization,
		"landlord_name": name,
		"phone": phone,
		"email": email,
		"user": user_id,
	})
	doc.insert(ignore_permissions=True)
	return doc, True


def _property_unit_counts_by(link_field):
	"""
	Return {link_value: {"properties": n, "units": m}} where link_field is
	'landlord' or 'caretaker' on Property. Units are counted from Property Unit
	for the properties linked to each person. Two grouped queries, no N+1.
	"""
	result = {}
	prop_rows = frappe.get_all(
		"Property",
		filters={link_field: ["is", "set"]},
		fields=[f"{link_field} as who", "name"],
	)
	prop_to_who = {}
	for r in prop_rows:
		prop_to_who[r.name] = r.who
		d = result.setdefault(r.who, {"properties": 0, "units": 0})
		d["properties"] += 1

	if prop_to_who:
		unit_rows = frappe.get_all(
			"Property Unit",
			filters={"property": ["in", list(prop_to_who.keys())]},
			fields=["property", "count(name) as cnt"],
			group_by="property",
		)
		for u in unit_rows:
			who = prop_to_who.get(u.property)
			if who:
				result[who]["units"] += u.cnt
	return result


@frappe.whitelist()
@envelope
def list_landlords(page=1, page_size=8, search=None):
	"""List landlords with their assigned property + unit counts and pagination."""
	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	offset = (page - 1) * page_size
	
	# Build filters for search
	filters = {}
	or_filters = None
	if search:
		search_term = f'%{search}%'
		or_filters = [
			['Landlord', 'landlord_name', 'like', search_term],
			['Landlord', 'phone', 'like', search_term],
			['Landlord', 'email', 'like', search_term],
		]
	
	# Get total count
	if or_filters:
		total = len(frappe.get_all('Landlord', filters=filters, or_filters=or_filters, pluck='name'))
	else:
		total = frappe.db.count('Landlord', filters)
	
	counts = _property_unit_counts_by("landlord")
	
	# Get paginated data
	if or_filters:
		rows = frappe.get_all(
			"Landlord",
			filters=filters,
			or_filters=or_filters,
			fields=["name", "landlord_name", "phone", "email", "payout_bank_name", "payout_mpesa_phone"],
			order_by="landlord_name asc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	else:
		rows = frappe.get_all(
			"Landlord",
			fields=["name", "landlord_name", "phone", "email", "payout_bank_name", "payout_mpesa_phone"],
			order_by="landlord_name asc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	
	out = []
	for l in rows:
		c = counts.get(l.name, {"properties": 0, "units": 0})
		out.append({
			"id": l.name,
			"name": l.landlord_name or l.name,
			"phone": l.phone or "",
			"email": l.email or "",
			"properties": c["properties"],
			"units": c["units"],
			"payout_method": "M-Pesa" if l.payout_mpesa_phone else ("Bank" if l.payout_bank_name else "Bank"),
			"status": "Active",
		})
	
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': out,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


def _caretakers_for_user(user=None):
	"""
	Resolve ALL Caretaker records for a user (a user may be linked to more than
	one Caretaker record). Matches by the Caretaker.user link first, then by the
	user's full name. Returns a list of Caretaker docnames.
	"""
	user = user or frappe.session.user
	names = frappe.get_all("Caretaker", filters={"user": user}, pluck="name")
	if names:
		return names
	full_name = frappe.db.get_value("User", user, "full_name") or frappe.utils.get_fullname(user)
	if full_name:
		return frappe.get_all("Caretaker", filters={"caretaker_name": full_name}, pluck="name")
	return []


def _caretaker_for_user(user=None):
	"""Back-compat single-value resolver."""
	names = _caretakers_for_user(user)
	return names[0] if names else None


@frappe.whitelist()
@envelope
def my_properties():
	"""
	Properties assigned to the currently logged-in caretaker. Admin / unrestricted
	roles get all properties. Resolution is server-side (not name-guessing on the
	client), so scoping is authoritative.
	"""
	from property_management.api.roles import UNRESTRICTED_ROLES
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	unrestricted = user == "Administrator" or bool(roles & UNRESTRICTED_ROLES)

	filters = {}
	if not unrestricted:
		caretakers = _caretakers_for_user(user)
		if not caretakers:
			return []
		filters["caretaker"] = ["in", caretakers]

	props = frappe.get_all(
		"Property", filters=filters,
		fields=["name", "property_code", "property_name", "property_type",
				"address", "landlord", "caretaker", "status"],
		order_by="creation desc",
	)

	# Unit counts per property (grouped, no N+1).
	names = [p.name for p in props]
	by_prop = {}
	if names:
		for row in frappe.get_all(
			"Property Unit", filters={"property": ["in", names]},
			fields=["property", "status", "count(name) as cnt"], group_by="property, status",
		):
			d = by_prop.setdefault(row.property, {"total": 0, "occupied": 0, "vacant": 0})
			d["total"] += row.cnt
			if row.status == "Occupied":
				d["occupied"] += row.cnt
			elif row.status == "Vacant":
				d["vacant"] += row.cnt

	out = []
	for p in props:
		c = by_prop.get(p.name, {"total": 0, "occupied": 0, "vacant": 0})
		out.append({
			"name": p.name,
			"property_code": p.property_code,
			"property_name": p.property_name,
			"property_type": p.property_type,
			"address": p.address,
			"landlord": p.landlord,
			"caretaker": p.caretaker,
			"status": p.status,
			"total_units": c["total"],
			"occupied_units": c["occupied"],
			"vacant_units": c["vacant"],
		})
	return out


@frappe.whitelist()
@envelope
def my_arrears():
	"""
	Rent arrears for tenants on properties assigned to the current caretaker.
	Admin / unrestricted roles get portfolio-wide arrears. Aggregates overdue
	Property Invoice balances scoped to the caretaker's properties.
	"""
	from property_management.api.roles import UNRESTRICTED_ROLES
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	unrestricted = user == "Administrator" or bool(roles & UNRESTRICTED_ROLES)

	prop_filter = None
	if not unrestricted:
		caretakers = _caretakers_for_user(user)
		if not caretakers:
			return []
		prop_names = frappe.get_all("Property", filters={"caretaker": ["in", caretakers]}, pluck="name")
		if not prop_names:
			return []
		prop_filter = prop_names

	filters = {"outstanding_amount": [">", 0]}
	if prop_filter is not None:
		filters["property"] = ["in", prop_filter]

	invoices = frappe.get_all(
		"Property Invoice", filters=filters,
		fields=["name", "property", "unit", "tenant", "due_date",
				"total_amount", "paid_amount", "outstanding_amount", "status"],
		order_by="outstanding_amount desc",
	)

	from frappe.utils import getdate, nowdate, date_diff
	today = getdate(nowdate())
	out = []
	for inv in invoices:
		days = date_diff(today, getdate(inv.due_date)) if inv.due_date else 0
		out.append({
			"id": inv.name,
			"tenant": inv.tenant,
			"property": inv.property,
			"unit": inv.unit,
			"amount": inv.outstanding_amount,
			"daysOverdue": max(0, days),
			"lastReminder": None,
		})
	return out


@frappe.whitelist()
@envelope
def list_caretakers(page=1, page_size=8, search=None):
	"""List caretakers with their assigned property + unit counts and pagination."""
	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	offset = (page - 1) * page_size
	
	# Build filters for search
	filters = {}
	or_filters = None
	if search:
		search_term = f'%{search}%'
		or_filters = [
			['Caretaker', 'caretaker_name', 'like', search_term],
			['Caretaker', 'phone', 'like', search_term],
			['Caretaker', 'email', 'like', search_term],
		]
	
	# Get total count
	if or_filters:
		total = len(frappe.get_all('Caretaker', filters=filters, or_filters=or_filters, pluck='name'))
	else:
		total = frappe.db.count('Caretaker', filters)
	
	counts = _property_unit_counts_by("caretaker")
	
	# Get paginated data
	if or_filters:
		rows = frappe.get_all(
			"Caretaker",
			filters=filters,
			or_filters=or_filters,
			fields=["name", "caretaker_name", "phone", "email"],
			order_by="caretaker_name asc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	else:
		rows = frappe.get_all(
			"Caretaker",
			fields=["name", "caretaker_name", "phone", "email"],
			order_by="caretaker_name asc",
			limit_start=offset,
			limit_page_length=page_size,
		)
	
	out = []
	for c_row in rows:
		c = counts.get(c_row.name, {"properties": 0, "units": 0})
		out.append({
			"id": c_row.name,
			"name": c_row.caretaker_name or c_row.name,
			"phone": c_row.phone or "",
			"email": c_row.email or "",
			"properties": c["properties"],
			"units": c["units"],
			"status": "Active",
		})
	
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': out,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


@frappe.whitelist()
@envelope
def create_landlord(landlord_name=None, phone=None, email=None, organization=None, **kwargs):
	"""Create a single landlord. Organization is auto-resolved if not given."""
	org = resolve_organization(explicit=organization)
	if not org:
		raise frappe.ValidationError("No Organization configured")
	row = {
		"landlord_name": landlord_name,
		"phone": phone,
		"email": email,
	}
	doc, created = _upsert_landlord(row, org)
	return {"name": doc.name, "created": created}


@frappe.whitelist()
@envelope
def bulk_create_landlords(landlords, organization=None):
	"""
	Bulk-create landlords from an imported list.

	landlords: JSON list of {landlord_id?, landlord_name, phone_number, email_address, properties_owned?}
	Returns {created, updated, failed, total}.
	"""
	if isinstance(landlords, str):
		landlords = frappe.parse_json(landlords)

	org = resolve_organization(explicit=organization)
	if not org:
		raise frappe.ValidationError("No Organization configured")

	created, updated, failed = [], [], []
	for row in landlords or []:
		try:
			doc, was_created = _upsert_landlord(row, org)
			(created if was_created else updated).append(doc.name)
		except Exception as e:
			failed.append({"landlord": row.get("landlord_name") or row.get("name"), "error": str(e)})

	return {
		"created": created,
		"updated": updated,
		"failed": failed,
		"total": len(landlords or []),
	}


# --------------------------------------------------------------------------
# Caretakers
# --------------------------------------------------------------------------

def _upsert_caretaker(row, organization):
	name = (row.get("caretaker_name") or row.get("name") or "").strip()
	if not name:
		raise frappe.ValidationError("Caretaker name is required")

	email = row.get("email_address") or row.get("email")
	phone = row.get("phone_number") or row.get("phone")

	if frappe.db.exists("Caretaker", name):
		doc = frappe.get_doc("Caretaker", name)
		doc.phone = phone or doc.phone
		doc.email = email or doc.email
		if not doc.get("user"):
			doc.user = _ensure_user_with_role(email, name, "Caretaker", phone, organization)
		doc.save(ignore_permissions=True)
		return doc, False

	user_id = _ensure_user_with_role(email, name, "Caretaker", phone, organization)

	doc = frappe.get_doc({
		"doctype": "Caretaker",
		"organization": organization,
		"caretaker_name": name,
		"phone": phone,
		"email": email,
		"user": user_id,
	})
	doc.insert(ignore_permissions=True)
	return doc, True


@frappe.whitelist()
@envelope
def create_caretaker(caretaker_name=None, phone=None, email=None, organization=None, **kwargs):
	"""Create a single caretaker. Organization is auto-resolved if not given."""
	org = resolve_organization(explicit=organization)
	if not org:
		raise frappe.ValidationError("No Organization configured")
	row = {
		"caretaker_name": caretaker_name,
		"phone": phone,
		"email": email,
	}
	doc, created = _upsert_caretaker(row, org)
	return {"name": doc.name, "created": created}


@frappe.whitelist()
@envelope
def bulk_create_caretakers(caretakers, organization=None):
	"""
	Bulk-create caretakers from an imported list.

	caretakers: JSON list of {caretaker_id?, caretaker_name, phone_number, email_address, assigned_properties?}
	Returns {created, updated, failed, total}.
	"""
	if isinstance(caretakers, str):
		caretakers = frappe.parse_json(caretakers)

	org = resolve_organization(explicit=organization)
	if not org:
		raise frappe.ValidationError("No Organization configured")

	created, updated, failed = [], [], []
	for row in caretakers or []:
		try:
			doc, was_created = _upsert_caretaker(row, org)
			(created if was_created else updated).append(doc.name)
		except Exception as e:
			failed.append({"caretaker": row.get("caretaker_name") or row.get("name"), "error": str(e)})

	return {
		"created": created,
		"updated": updated,
		"failed": failed,
		"total": len(caretakers or []),
	}


# --------------------------------------------------------------------------
# Properties
# --------------------------------------------------------------------------

# UI property types -> DocType Select options (Residential/Commercial/Mixed Use)
_PROPERTY_TYPE_MAP = {
	"apartment": "Residential",
	"villa / maisonette": "Residential",
	"bedsitter block": "Residential",
	"commercial complex": "Commercial",
	"mixed use": "Mixed Use",
	"residential": "Residential",
	"commercial": "Commercial",
}


def _map_property_type(value):
	if not value:
		return "Residential"
	return _PROPERTY_TYPE_MAP.get(str(value).strip().lower(), "Residential")


def _save_data_url_as_file(data_url, attached_to_doctype=None, attached_to_name=None,
						   is_private=0, prefix="property"):
	"""
	Persist a base64 data: URL as a File and return its file_url. Non-data URLs
	pass through unchanged. Set is_private=1 for sensitive docs (e.g. national IDs).
	"""
	if not data_url or not isinstance(data_url, str):
		return None
	if not data_url.startswith("data:"):
		return data_url  # already a URL/path

	import base64
	try:
		header, b64 = data_url.split(",", 1)
		ext = "png"
		if "image/" in header:
			ext = header.split("image/")[1].split(";")[0] or "png"
		content = base64.b64decode(b64)
		fname = f"{prefix}_{frappe.generate_hash(length=8)}.{ext}"
		filedoc = frappe.get_doc({
			"doctype": "File",
			"file_name": fname,
			"attached_to_doctype": attached_to_doctype,
			"attached_to_name": attached_to_name,
			"is_private": 1 if is_private else 0,
			"content": content,
		})
		filedoc.insert(ignore_permissions=True)
		return filedoc.file_url
	except Exception:
		frappe.log_error(title="image decode failed", message=frappe.get_traceback())
		return None


def _upsert_property(row, organization):
	name = (row.get("property_name") or row.get("name") or "").strip()
	if not name:
		raise frappe.ValidationError("Property name is required")

	code = (row.get("property_id") or row.get("property_code") or "").strip()
	if not code:
		code = f"PROP-{frappe.generate_hash(length=6).upper()}"

	# Resolve landlord by name if provided (import gives a name, not an id).
	landlord = row.get("landlord")
	if landlord and not frappe.db.exists("Landlord", landlord):
		match = frappe.db.get_value("Landlord", {"landlord_name": landlord}, "name")
		landlord = match or None

	def _int(v):
		try:
			return int(float(v)) if v not in (None, "", "-") else None
		except Exception:
			return None

	doc = frappe.get_doc({
		"doctype": "Property",
		"organization": organization,
		"property_code": code,
		"property_name": name,
		"property_type": _map_property_type(row.get("property_type") or row.get("type")),
		"status": "Active",
		"landlord": landlord,
		"caretaker": row.get("caretaker") or None,
		"address": row.get("location") or row.get("address"),
		"county": row.get("county"),
		"sub_county": row.get("sub_county"),
		"year_built": _int(row.get("year_built")),
		"total_units": _int(row.get("total_units")),
		"floors": _int(row.get("floors")),
		"description": row.get("description"),
		"amenities": row.get("amenities"),
	})
	doc.insert(ignore_permissions=True)

	# Images: list of data URLs or URLs (first = cover). Saving each File attaches
	# it to the Property, which bumps the Property's `modified` timestamp via the
	# attachment hook. Save the files first, then reload the doc so the subsequent
	# save uses the current timestamp (avoids TimestampMismatchError).
	images = row.get("images") or []
	saved_urls = []
	for idx, img in enumerate(images):
		url = _save_data_url_as_file(img, "Property", doc.name)
		if url:
			saved_urls.append(url)

	if saved_urls:
		doc.reload()
		for idx, url in enumerate(saved_urls):
			doc.append("images", {"image": url, "is_cover": 1 if idx == 0 else 0})
		doc.cover_image = saved_urls[0]
		doc.save(ignore_permissions=True)

	return doc, True


@frappe.whitelist()
@envelope
def get_property(property=None, name=None):
	"""
	Full detail for a single property: real master fields, computed unit counts
	(total/occupied/vacant), uploaded images (URLs + cover), landlord/caretaker.
	Runs server-side so it is not blocked by /resource doctype permissions.
	"""
	pid = property or name
	if not pid or not frappe.db.exists("Property", pid):
		raise frappe.ValidationError("Property not found")

	p = frappe.get_doc("Property", pid)

	# Unit counts by status.
	counts = frappe.get_all(
		"Property Unit", filters={"property": pid},
		fields=["status", "count(name) as cnt"], group_by="status",
	)
	total = occupied = vacant = 0
	for c in counts:
		total += c.cnt
		if c.status == "Occupied":
			occupied += c.cnt
		elif c.status == "Vacant":
			vacant += c.cnt

	# Images from the child table (cover first) - convert to base64.
	images = []
	for img in (p.get("images") or []):
		if img.image:
			img_b64 = _file_to_base64(img.image)
			images.append({"url": img_b64 or img.image, "caption": img.caption or "", "is_cover": bool(img.is_cover)})
	images.sort(key=lambda x: 0 if x["is_cover"] else 1)
	cover = p.get("cover_image") or (images[0]["url"] if images else None)

	# Friendly display names for linked landlord/caretaker.
	landlord_name = frappe.db.get_value("Landlord", p.landlord, "landlord_name") if p.landlord else None
	caretaker_name = frappe.db.get_value("Caretaker", p.caretaker, "caretaker_name") if p.caretaker else None

	return {
		"name": p.name,
		"property_code": p.property_code,
		"property_name": p.property_name,
		"property_type": p.property_type,
		"status": p.status,
		"location": p.address,
		"county": p.county,
		"sub_county": p.sub_county,
		"year_built": p.year_built,
		"floors": p.floors,
		"description": p.description,
		"amenities": p.amenities,
		"landlord": p.landlord,
		"landlord_name": landlord_name,
		"caretaker": p.caretaker,
		"caretaker_name": caretaker_name,
		"total_units": total,
		"occupied_units": occupied,
		"vacant_units": vacant,
		"cover_image": cover,
		"images": images,
	}


@frappe.whitelist()
@envelope
def create_property(**kwargs):
	"""
	Create a single property with the extended onboarding fields and (optionally)
	its units in one call. Organization is auto-resolved and applied to both the
	Property and each Property Unit. `images` / `units` may be JSON strings.

	Unit types not in the Property Unit Select are coerced to a valid option so a
	single odd type doesn't silently drop the whole unit.
	"""
	organization = resolve_organization(explicit=kwargs.get("organization"))
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	images = kwargs.get("images")
	if isinstance(images, str):
		images = frappe.parse_json(images)
	kwargs["images"] = images or []

	units = kwargs.get("units")
	if isinstance(units, str):
		units = frappe.parse_json(units)

	doc, _ = _upsert_property(kwargs, organization)

	created, failed = [], []
	for row in units or []:
		try:
			u = _upsert_unit(row, doc.name, organization)
			created.append(u.name)
		except Exception as e:
			failed.append({"unit": row.get("unit_number") or row.get("number"), "error": str(e)})

	if created:
		frappe.db.set_value("Property", doc.name, "total_units",
							frappe.db.count("Property Unit", {"property": doc.name}))

	frappe.db.commit()
	return {
		"name": doc.name,
		"property_code": doc.property_code,
		"units": {"created": created, "failed": failed},
	}


@frappe.whitelist()
@envelope
def bulk_create_properties(properties, organization=None):
	"""
	Bulk-create properties from an imported list.
	Returns {created, failed, total}.
	"""
	if isinstance(properties, str):
		properties = frappe.parse_json(properties)

	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	created, failed = [], []
	for row in properties or []:
		try:
			doc, _ = _upsert_property(row, organization)
			created.append(doc.name)
		except Exception as e:
			failed.append({"property": row.get("property_name") or row.get("name"), "error": str(e)})

	return {"created": created, "failed": failed, "total": len(properties or [])}


# --------------------------------------------------------------------------
# Tenants (with income range + lease signatures)
# --------------------------------------------------------------------------


@frappe.whitelist()
@envelope
def list_tenants(page=1, page_size=8, search=None, limit=None, property=None):
	"""
	List tenants with server-side pagination.
	
	Args:
		page: Page number (1-indexed), default 1
		page_size: Records per page, default 8, max 100
		search: Optional search term (searches name, phone, email)
		limit: Deprecated - use page_size instead (kept for backward compatibility)
		property: Optional Property filter. Restricts to tenants who have a lease
			on that property (tenant->property link is via Lease Agreement).
	
	Returns:
		{data: [...], pagination: {page, page_size, total, total_pages, has_next, has_prev}}
	"""
	from frappe.utils import flt
	
	# Handle backward compatibility with old limit param
	if limit and not page_size:
		page_size = int(limit)
	
	# Sanitize inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	offset = (page - 1) * page_size
	
	# Build search filters
	filters = {}
	or_filters = None

	# Property scope: tenants are linked to a property through their lease, so
	# resolve the set of tenants leasing on this property and constrain by name.
	if property:
		leased_tenants = frappe.get_all(
			'Lease Agreement', filters={'property': property}, pluck='tenant', distinct=True,
		)
		# Empty sentinel keeps the query valid (and correctly returns 0 rows) when
		# no tenant leases on the property.
		filters['name'] = ['in', leased_tenants or ['__none__']]
	if search:
		search_term = f'%{search}%'
		or_filters = [
			['Property Tenant', 'tenant_name', 'like', search_term],
			['Property Tenant', 'phone', 'like', search_term],
			['Property Tenant', 'email', 'like', search_term],
		]
	
	# Get total count
	if or_filters:
		total = len(frappe.get_all('Property Tenant', filters=filters, or_filters=or_filters, pluck='name'))
	else:
		total = frappe.db.count('Property Tenant', filters)
	
	# Get paginated data
	if or_filters:
		rows = frappe.get_all(
			'Property Tenant',
			filters=filters,
			or_filters=or_filters,
			fields=['name', 'tenant_name', 'phone', 'email', 'national_id', 'status', 'creation'],
			order_by='creation desc',
			limit_start=offset,
			limit_page_length=page_size
		)
	else:
		rows = frappe.get_all(
			'Property Tenant',
			filters=filters,
			fields=['name', 'tenant_name', 'phone', 'email', 'national_id', 'status', 'creation'],
			order_by='creation desc',
			limit_start=offset,
			limit_page_length=page_size
		)
	
	tenant_names = [t.name for t in rows]
	
	# Active lease per tenant -> unit + monthly rent
	lease_by_tenant = {}
	unit_label = {}
	if tenant_names:
		leases = frappe.get_all(
			'Lease Agreement',
			filters={'tenant': ['in', tenant_names], 'status': 'Active'},
			fields=['tenant', 'unit', 'rent_amount', 'creation'],
			order_by='creation desc',
		)
		for le in leases:
			if le.tenant not in lease_by_tenant:
				lease_by_tenant[le.tenant] = le
		
		unit_ids = list({le.unit for le in lease_by_tenant.values() if le.unit})
		if unit_ids:
			for u in frappe.get_all(
				'Property Unit', filters={'name': ['in', unit_ids]},
				fields=['name', 'unit_number'],
			):
				unit_label[u.name] = u.unit_number or u.name
	
	# Outstanding balance per tenant from Sales Invoices
	balance_by_customer = {}
	tenant_display = {t.name: (t.tenant_name or t.name) for t in rows}
	customer_names = list({v for v in tenant_display.values()})
	if customer_names:
		for r in frappe.get_all(
			'Sales Invoice',
			filters={'docstatus': 1, 'customer': ['in', customer_names], 'outstanding_amount': ['>', 0]},
			fields=['customer', 'sum(outstanding_amount) as bal'],
			group_by='customer',
		):
			balance_by_customer[r['customer']] = flt(r['bal'])
	
	# Build output
	data = []
	for t in rows:
		le = lease_by_tenant.get(t.name)
		balance = balance_by_customer.get(tenant_display.get(t.name), 0.0)
		base_status = t.status or 'Active'
		data.append({
			'id': t.name,
			'name': t.tenant_name or t.name,
			'phone': t.phone or '',
			'email': t.email or '',
			'national_id': t.national_id or '',
			'unit': (unit_label.get(le.unit, le.unit) if le else '') or '',
			'rent': flt(le.rent_amount) if le else 0.0,
			'balance': balance,
			'status': 'Overdue' if balance > 0 else base_status,
		})
	
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': data,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


@frappe.whitelist()
@envelope
def create_tenant(tenant_name=None, phone=None, email=None, national_id=None,
                  income_range=None, status="Active", organization=None,
                  national_id_front=None, national_id_back=None, **kwargs):
	"""Create a single tenant with the income range field. Organization auto-resolved."""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	user_id = _ensure_user_with_role(email, tenant_name, "Tenant", phone, organization)

	doc = frappe.get_doc({
		"doctype": "Property Tenant",
		"organization": organization,
		"tenant_name": tenant_name,
		"phone": phone,
		"email": email,
		"national_id": national_id,
		"income_range": income_range or None,
		"status": status or "Active",
		"user": user_id,
	})
	doc.insert(ignore_permissions=True)
	_save_tenant_id_images(doc, national_id_front, national_id_back)
	return {"name": doc.name}


def _save_tenant_id_images(tenant_doc, front=None, back=None):
	"""Decode base64 ID images to PRIVATE Files attached to the tenant and set the links."""
	updates = {}
	if front:
		url = _save_data_url_as_file(front, "Property Tenant", tenant_doc.name,
									 is_private=1, prefix="tenant_id_front")
		if url:
			updates["national_id_front"] = url
	if back:
		url = _save_data_url_as_file(back, "Property Tenant", tenant_doc.name,
									 is_private=1, prefix="tenant_id_back")
		if url:
			updates["national_id_back"] = url
	if updates:
		tenant_doc.db_set(updates, update_modified=False)


@frappe.whitelist()
@envelope
def onboard_tenant(tenant_name=None, phone=None, email=None, national_id=None,
                   income_range=None, property=None, unit=None, rent=None,
                   deposit=None, water_deposit=None, electricity_deposit=None,
                   lease_start=None, lease_end=None,
                   tenant_signature=None, caretaker_signature=None,
                   signed_at=None, organization=None,
                   national_id_front=None, national_id_back=None, **kwargs):
	"""
	Create a tenant AND a Lease Agreement (assigning a unit) carrying both digital
	signatures, in one call. Onboarding REQUIRES a property + unit so a tenant is
	never left floating without a unit. Organization auto-resolved.
	"""
	organization = resolve_organization(explicit=organization)
	if not organization:
		raise frappe.ValidationError("No Organization configured")

	# Onboarding must assign a unit — reject a tenant with no unit.
	if not property or not unit:
		raise frappe.ValidationError("Select a property and a vacant unit to onboard the tenant.")

	user_id = _ensure_user_with_role(email, tenant_name, "Tenant", phone, organization)

	tenant = frappe.get_doc({
		"doctype": "Property Tenant",
		"organization": organization,
		"tenant_name": tenant_name,
		"phone": phone,
		"email": email,
		"national_id": national_id,
		"income_range": income_range or None,
		"status": "Active",
		"user": user_id,
	})
	tenant.insert(ignore_permissions=True)
	_save_tenant_id_images(tenant, national_id_front, national_id_back)

	def _num(v):
		try:
			return float(v) if v not in (None, "") else 0
		except Exception:
			return 0

	signed_on = _parse_datetime(signed_at)

	# Resolve the unit whether given as its doc name or a unit_number.
	unit_name = unit if frappe.db.exists("Property Unit", unit) else frappe.db.get_value(
		"Property Unit", {"property": property, "unit_number": unit}, "name"
	)
	if not unit_name:
		# Roll back the just-created tenant so we never leave an orphan.
		frappe.db.rollback()
		raise frappe.ValidationError("Selected unit not found for this property.")

	# Lease dates: default the end date to 12 months after start (must be AFTER start).
	start = lease_start or frappe.utils.nowdate()
	end = lease_end
	if not end or frappe.utils.getdate(end) <= frappe.utils.getdate(start):
		end = frappe.utils.add_months(start, 12)

	lease_name = None
	try:
		lease = frappe.get_doc({
			"doctype": "Lease Agreement",
			"organization": organization,
			"property": property,
			"unit": unit_name,
			"tenant": tenant.name,
			"status": "Active",
			"start_date": start,
			"end_date": end,
			"rent_amount": _num(rent),
			"deposit_amount": _num(deposit),
			# Non-refundable utility deposits: stored as record values only, never
			# posted to accounting and excluded from the initial amount due / STK total.
			"water_deposit": _num(water_deposit),
			"electricity_deposit": _num(electricity_deposit),
			"tenant_signature": tenant_signature,
			"caretaker_signature": caretaker_signature,
			"signed_on": signed_on,
			"initial_amount_due": _num(rent) + _num(deposit),
			"initial_payment_status": "Pending",
		})
		lease.insert(ignore_permissions=True)
		lease_name = lease.name
	except Exception:
		# If the lease fails, roll back the tenant too (no orphan tenants).
		frappe.db.rollback()
		raise

	# on_update on the lease already marks the unit Occupied for Active leases.
	# Generate + attach the signed agreement PDF.
	try:
		lease.generate_agreement_pdf()
	except Exception:
		frappe.log_error(title="lease pdf generation failed", message=frappe.get_traceback())

	frappe.db.commit()
	return {"tenant": tenant.name, "lease_agreement": lease_name}


@frappe.whitelist()
@envelope
def list_leases(organization=None, property=None, caretaker_scope=False):
	"""
	List Lease Agreements with tenant + initial payment status, for the onboarding
	table and the caretaker retry action. When caretaker_scope is truthy, restrict
	to the current caretaker's assigned properties.
	"""
	filters = {}
	if property:
		filters["property"] = property
	if organization:
		filters["organization"] = organization

	if caretaker_scope in (True, "true", "1", 1):
		user = frappe.session.user
		from property_management.api.roles import UNRESTRICTED_ROLES
		roles = set(frappe.get_roles(user))
		if user != "Administrator" and not (roles & UNRESTRICTED_ROLES):
			caretakers = _caretakers_for_user(user)
			if not caretakers:
				return []
			prop_names = frappe.get_all("Property", filters={"caretaker": ["in", caretakers]}, pluck="name")
			if not prop_names:
				return []
			filters["property"] = ["in", prop_names]

	rows = frappe.get_all(
		"Lease Agreement", filters=filters,
		fields=["name", "tenant", "property", "unit", "status", "rent_amount", "deposit_amount",
				"initial_payment_status", "initial_amount_due", "initial_amount_paid", "creation"],
		order_by="creation desc",
	)
	out = []
	for r in rows:
		tname = frappe.db.get_value("Property Tenant", r.tenant, ["tenant_name", "phone"], as_dict=True) or {}
		out.append({
			"lease": r.name,
			"tenant": r.tenant,
			"tenant_name": tname.get("tenant_name") or r.tenant,
			"phone": tname.get("phone") or "",
			"property": r.property,
			"unit": r.unit,
			"status": r.status,
			"rent_amount": r.rent_amount,
			"deposit_amount": r.deposit_amount,
			"initial_payment_status": r.initial_payment_status or "Pending",
			"initial_amount_due": r.initial_amount_due,
			"initial_amount_paid": r.initial_amount_paid,
		})
	return out


@frappe.whitelist()
@envelope
def delete_unassigned_tenants():
	"""
	Delete tenants that have no lease (not assigned to any unit), plus their portal
	user account. Admin/unrestricted only. Returns the deleted names.
	"""
	from property_management.api.roles import UNRESTRICTED_ROLES
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if user != "Administrator" and not (roles & UNRESTRICTED_ROLES):
		frappe.throw("Not permitted.", frappe.PermissionError)

	leased = set(frappe.get_all("Lease Agreement", pluck="tenant"))
	orphans = [t for t in frappe.get_all("Property Tenant", fields=["name", "user"]) if t.name not in leased]
	deleted = []
	for t in orphans:
		u = t.user
		frappe.delete_doc("Property Tenant", t.name, force=True, ignore_permissions=True)
		if u and frappe.db.exists("User", u) and frappe.db.get_value("User", u, "user_type") != "System User":
			try:
				frappe.delete_doc("User", u, force=True, ignore_permissions=True)
			except Exception:
				pass
		deleted.append(t.name)
	frappe.db.commit()
	return {"deleted": deleted, "count": len(deleted)}


@frappe.whitelist()
@envelope
def delete_tenant(tenant):
	"""
	Delete a single tenant IF it has no lease (unassigned). Refuses to delete a
	tenant that holds a lease. Admin/unrestricted only.
	"""
	from property_management.api.roles import UNRESTRICTED_ROLES
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if user != "Administrator" and not (roles & UNRESTRICTED_ROLES):
		frappe.throw("Not permitted.", frappe.PermissionError)

	if not frappe.db.exists("Property Tenant", tenant):
		raise frappe.ValidationError("Tenant not found")
	if frappe.db.exists("Lease Agreement", {"tenant": tenant}):
		raise frappe.ValidationError("Tenant has a lease/unit; unassign the unit before deleting.")

	u = frappe.db.get_value("Property Tenant", tenant, "user")
	frappe.delete_doc("Property Tenant", tenant, force=True, ignore_permissions=True)
	if u and frappe.db.exists("User", u) and frappe.db.get_value("User", u, "user_type") != "System User":
		try:
			frappe.delete_doc("User", u, force=True, ignore_permissions=True)
		except Exception:
			pass
	frappe.db.commit()
	return {"deleted": tenant}


@frappe.whitelist()
@envelope
def my_tenants():
	"""
	Tenants for the current caretaker: everyone they onboarded (tenant created by
	this user) OR who holds a lease on one of the caretaker's properties. Admin /
	unrestricted roles get all tenants. Includes lease + initial payment status
	and the retry-able lease name when a lease exists.
	"""
	from property_management.api.roles import UNRESTRICTED_ROLES
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	unrestricted = user == "Administrator" or bool(roles & UNRESTRICTED_ROLES)

	tenant_names = None
	if not unrestricted:
		caretakers = _caretakers_for_user(user)
		# Properties assigned to this caretaker -> tenants with leases there.
		prop_names = frappe.get_all("Property", filters={"caretaker": ["in", caretakers]}, pluck="name") if caretakers else []
		leased = set()
		if prop_names:
			leased = set(frappe.get_all("Lease Agreement", filters={"property": ["in", prop_names]}, pluck="tenant"))
		# Tenants this user created (covers standalone onboarding without a lease).
		created = set(frappe.get_all("Property Tenant", filters={"owner": user}, pluck="name"))
		tenant_names = list(leased | created)
		if not tenant_names:
			return []

	t_filters = {}
	if tenant_names is not None:
		t_filters["name"] = ["in", tenant_names]
	tenants = frappe.get_all(
		"Property Tenant", filters=t_filters,
		fields=["name", "tenant_name", "phone", "email", "status", "creation"],
		order_by="creation desc",
	)

	# Latest lease per tenant (if any) for unit + payment status.
	out = []
	for t in tenants:
		lease = frappe.get_all(
			"Lease Agreement", filters={"tenant": t.name},
			fields=["name", "property", "unit", "status", "rent_amount", "deposit_amount",
					"initial_payment_status", "initial_amount_due"],
			order_by="creation desc", limit=1,
		)
		l = lease[0] if lease else {}
		out.append({
			"tenant": t.name,
			"tenant_name": t.tenant_name or t.name,
			"phone": t.phone or "",
			"email": t.email or "",
			"status": t.status or "Active",
			"lease": l.get("name"),
			"property": l.get("property"),
			"unit": l.get("unit"),
			"rent_amount": l.get("rent_amount"),
			"initial_amount_due": l.get("initial_amount_due"),
			"initial_payment_status": l.get("initial_payment_status") or ("—" if not l else "Pending"),
			"assigned": bool(l),
		})
	return out


@frappe.whitelist()
@envelope
def assign_tenant_to_unit(tenant, unit, property=None, rent=None, deposit=None,
						  water_deposit=None, electricity_deposit=None,
						  lease_start=None, lease_end=None,
						  tenant_signature=None, caretaker_signature=None, signed_at=None):
	"""
	Assign an EXISTING tenant to a unit by creating a Lease Agreement (marks the
	unit Occupied via the lease's on_update) and generating the signed PDF. Use
	this for tenants that were created standalone (no lease yet).
	"""
	if not frappe.db.exists("Property Tenant", tenant):
		raise frappe.ValidationError("Tenant not found")

	unit_name = unit if frappe.db.exists("Property Unit", unit) else None
	if not unit_name and property:
		unit_name = frappe.db.get_value("Property Unit", {"property": property, "unit_number": unit}, "name")
	if not unit_name:
		raise frappe.ValidationError("Unit not found")

	prop = property or frappe.db.get_value("Property Unit", unit_name, "property")
	organization = frappe.db.get_value("Property Tenant", tenant, "organization") \
		or frappe.db.get_value("Property", prop, "organization") or resolve_organization()
	unit_doc = frappe.get_doc("Property Unit", unit_name)

	def _num(v):
		try:
			return float(v) if v not in (None, "") else 0
		except Exception:
			return 0

	rent_amt = _num(rent) if rent not in (None, "") else _num(unit_doc.base_rent)
	deposit_amt = _num(deposit) if deposit not in (None, "") else _num(unit_doc.security_deposit)
	# Non-refundable utility deposits: stored as values only, excluded from GL + amount due.
	water_dep = _num(water_deposit)
	electricity_dep = _num(electricity_deposit)

	lease = frappe.get_doc({
		"doctype": "Lease Agreement",
		"organization": organization,
		"property": prop,
		"unit": unit_name,
		"tenant": tenant,
		"status": "Active",
		"start_date": lease_start or frappe.utils.nowdate(),
		"end_date": lease_end or frappe.utils.add_months(frappe.utils.nowdate(), 12),
		"rent_amount": rent_amt,
		"deposit_amount": deposit_amt,
		"water_deposit": water_dep,
		"electricity_deposit": electricity_dep,
		"tenant_signature": tenant_signature,
		"caretaker_signature": caretaker_signature,
		"signed_on": _parse_datetime(signed_at),
		"initial_amount_due": rent_amt + deposit_amt,
		"initial_payment_status": "Pending",
	})
	lease.flags.ignore_mandatory = True
	lease.insert(ignore_permissions=True)
	try:
		lease.generate_agreement_pdf()
	except Exception:
		frappe.log_error(title="lease pdf generation failed", message=frappe.get_traceback())

	frappe.db.commit()
	return {
		"lease_agreement": lease.name,
		"unit": unit_name,
		"unit_status": frappe.db.get_value("Property Unit", unit_name, "status"),
		"initial_amount_due": rent_amt + deposit_amt,
		"agreement_pdf": lease.agreement_pdf,
	}


# --------------------------------------------------------------------------
# Property Units
# --------------------------------------------------------------------------

def _upsert_unit(row, property_name, organization):
	unit_number = (row.get("unit_number") or row.get("number") or row.get("name") or "").strip()
	if not unit_number:
		raise frappe.ValidationError("Unit number is required")

	def _num(v):
		try:
			return float(v) if v not in (None, "") else 0
		except Exception:
			return 0

	doc = frappe.get_doc({
		"doctype": "Property Unit",
		"organization": organization,
		"property": property_name,
		"unit_number": unit_number,
		"unit_type": _map_unit_type(row.get("unit_type") or row.get("type")),
		"floor": row.get("floor") or "Ground Floor",
		"base_rent": _num(row.get("base_rent") or row.get("rent")),
		"security_deposit": _num(row.get("security_deposit") or row.get("deposit")),
		"status": row.get("status") or "Vacant",
	})
	doc.insert(ignore_permissions=True)
	return doc


# Valid options on the Property Unit.unit_type Select.
_VALID_UNIT_TYPES = {
	"Bedsitter", "Studio", "1 Bedroom", "2 Bedroom", "3 Bedroom",
	"4 Bedroom", "Penthouse", "Commercial Shop", "Office Space",
}
_UNIT_TYPE_ALIASES = {
	"bedsitter": "Bedsitter",
	"bed sitter": "Bedsitter",
	"studio": "Studio",
	"1 bedroom": "1 Bedroom",
	"2 bedroom": "2 Bedroom",
	"3 bedroom": "3 Bedroom",
	"4 bedroom": "4 Bedroom",
	"penthouse": "Penthouse",
	"commercial shop": "Commercial Shop",
	"office space": "Office Space",
}


def _map_unit_type(value):
	if not value:
		return "1 Bedroom"
	if value in _VALID_UNIT_TYPES:
		return value
	return _UNIT_TYPE_ALIASES.get(str(value).strip().lower(), "1 Bedroom")


@frappe.whitelist()
@envelope
def list_units(property=None, search=None, page=1, page_size=8):
	"""
	List Property Units with pagination for a property (or all). Runs server-side so it is not
	blocked by the Property Unit doctype's role permissions the way /resource is.
	"""
	# Sanitize pagination inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))
	
	filters = {}
	if property:
		filters["property"] = property
	
	rows = frappe.get_all(
		"Property Unit", filters=filters,
		fields=["name", "unit_number", "unit_type", "base_rent", "security_deposit",
				"status", "property", "floor"],
		order_by="unit_number asc",
	)
	
	out = []
	for r in rows:
		# Apply search filter
		if search:
			s = str(search).lower()
			hay = f"{r.get('unit_number', '')} {r.get('unit_type', '')} {r.get('floor', '')} {r.get('status', '')}".lower()
			if s not in hay:
				continue
		out.append(r)
	
	# Apply pagination
	total = len(out)
	offset = (page - 1) * page_size
	paginated = out[offset:offset + page_size]
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': paginated,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


@frappe.whitelist()
@envelope
def create_unit(property, **kwargs):
	"""Create a single Property Unit; organization inherited from the property."""
	if not property or not frappe.db.exists("Property", property):
		raise frappe.ValidationError("Property not found")
	organization = frappe.db.get_value("Property", property, "organization") \
		or resolve_organization()
	doc = _upsert_unit(kwargs, property, organization)
	return {"name": doc.name}


@frappe.whitelist()
@envelope
def bulk_create_units(property, units):
	"""
	Bulk-create units under a property. `units` is a JSON list.
	Organization is inherited from the property, and the property's total_units
	is refreshed. Returns {created, failed, total, total_units}.
	"""
	if isinstance(units, str):
		units = frappe.parse_json(units)
	if not property or not frappe.db.exists("Property", property):
		raise frappe.ValidationError("Property not found")

	organization = frappe.db.get_value("Property", property, "organization") \
		or resolve_organization()

	created, failed = [], []
	for row in units or []:
		try:
			doc = _upsert_unit(row, property, organization)
			created.append(doc.name)
		except Exception as e:
			failed.append({"unit": row.get("unit_number") or row.get("number"), "error": str(e)})

	# Keep the property's stored unit count in sync.
	total_units = frappe.db.count("Property Unit", {"property": property})
	frappe.db.set_value("Property", property, "total_units", total_units)

	return {"created": created, "failed": failed, "total": len(units or []), "total_units": total_units}


# --------------------------------------------------------------------------
# Vacate unit / move-out + held tenant items
# --------------------------------------------------------------------------

def _assert_can_manage_lease(lease_name):
	"""Raise PermissionError unless the current user is unrestricted OR the
	caretaker assigned to the lease's property. Returns the Lease doc."""
	from property_management.api.roles import UNRESTRICTED_ROLES

	if not frappe.db.exists("Lease Agreement", lease_name):
		raise frappe.ValidationError("Lease not found")

	lease = frappe.get_doc("Lease Agreement", lease_name)
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or (roles & UNRESTRICTED_ROLES):
		return lease

	caretakers = _caretakers_for_user(user)
	prop_caretaker = frappe.db.get_value("Property", lease.property, "caretaker")
	if caretakers and prop_caretaker in caretakers:
		return lease

	raise frappe.PermissionError("You are not permitted to manage this lease.")


@frappe.whitelist()
@envelope
def vacate_unit(lease=None, reason=None, held_items=None):
	"""
	Caretaker move-out flow. Terminates the lease (which frees the unit -> the
	Lease Agreement.on_update sets the Property Unit status to 'Vacant', so it
	surfaces in the Nyumba public feed and admin Vacancy Management), marks the
	tenant 'Moved Out', and records any held tenant items (belongings held
	against unpaid rent / dues).

	held_items: JSON list of {item_name, description, quantity, estimated_value,
	photo (base64 data URL)}.
	"""
	lease_doc = _assert_can_manage_lease(lease)

	tenant = lease_doc.tenant
	prop = lease_doc.property
	unit = lease_doc.unit
	organization = lease_doc.organization

	tenant_name = frappe.db.get_value("Property Tenant", tenant, "tenant_name") if tenant else None

	# 1) Terminate the lease -> frees the unit (Vacant) via lease.on_update.
	lease_doc.status = "Terminated"
	lease_doc.save(ignore_permissions=True)

	# 2) Mark the tenant Moved Out.
	if tenant and frappe.db.exists("Property Tenant", tenant):
		frappe.db.set_value("Property Tenant", tenant, "status", "Moved Out")

	# 3) Record held items.
	if isinstance(held_items, str):
		held_items = frappe.parse_json(held_items) if held_items else []
	held_items = held_items or []

	created_items = []
	for row in held_items:
		name = (row.get("item_name") or "").strip()
		if not name:
			continue
		item = frappe.get_doc({
			"doctype": "Held Tenant Item",
			"organization": organization,
			"property": prop,
			"unit": unit,
			"tenant": tenant,
			"tenant_name": tenant_name,
			"lease": lease_doc.name,
			"reason": reason or row.get("reason"),
			"item_name": name,
			"description": row.get("description"),
			"quantity": int(row.get("quantity") or 1),
			"estimated_value": row.get("estimated_value") or 0,
			"status": "Held",
			"held_on": frappe.utils.now_datetime(),
		})
		item.flags.ignore_mandatory = True
		item.insert(ignore_permissions=True)

		photo = row.get("photo")
		if photo and str(photo).startswith("data:"):
			url = _save_data_url_as_file(photo, "Held Tenant Item", item.name,
										 is_private=0, prefix="held_item")
			if url:
				frappe.db.set_value("Held Tenant Item", item.name, "photo", url)
		created_items.append(item.name)

	frappe.db.commit()

	return {
		"lease": lease_doc.name,
		"unit": unit,
		"unit_status": frappe.db.get_value("Property Unit", unit, "status") if unit else None,
		"tenant": tenant,
		"held_items_created": created_items,
	}


@frappe.whitelist()
@envelope
def list_held_items(tenant=None, property=None, search=None, page=1, page_size=8):
	"""
	List Held Tenant Items with pagination, optionally filtered by tenant and/or property.
	Photos are returned as base64 data URLs (frontend can't reach the bench
	files origin cross-domain). Caretaker scoping is enforced by the
	permission_query_conditions hook on the doctype.
	"""
	from property_management.api.documents import file_to_data_url

	# Sanitize pagination inputs
	page = max(1, int(page or 1))
	page_size = min(100, max(1, int(page_size or 8)))

	filters = {}
	if tenant:
		filters["tenant"] = tenant
	if property:
		filters["property"] = property

	rows = frappe.get_all(
		"Held Tenant Item", filters=filters,
		fields=["name", "organization", "property", "unit", "tenant", "tenant_name",
				"lease", "reason", "item_name", "description", "quantity",
				"estimated_value", "photo", "status", "held_on"],
		order_by="held_on desc",
	)

	out = []
	for r in rows:
		# Apply search filter
		if search:
			s = str(search).lower()
			hay = f"{r.get('tenant_name', '')} {r.get('item_name', '')} {r.get('description', '')}".lower()
			if s not in hay:
				continue
		if r.get("photo"):
			try:
				r["photo"] = file_to_data_url(r["photo"]) or r["photo"]
			except Exception:
				pass
		out.append(r)
	
	# Apply pagination
	total = len(out)
	offset = (page - 1) * page_size
	paginated = out[offset:offset + page_size]
	total_pages = (total + page_size - 1) // page_size if total > 0 else 1
	
	return {
		'data': paginated,
		'pagination': {
			'page': page,
			'page_size': page_size,
			'total': total,
			'total_pages': total_pages,
			'has_next': page < total_pages,
			'has_prev': page > 1
		}
	}


# --------------------------------------------------------------------------
# Bulk tenant import + per-tenant update + assign-unit/sign-lease
# --------------------------------------------------------------------------

def _upsert_tenant(row, organization):
	"""Create (or update) a Property Tenant WITHOUT a lease, from an import/form
	dict. Keyed by tenant_name (the doctype autonames on it). Returns (doc, created)."""
	name = (row.get("tenant_name") or row.get("name") or "").strip()
	if not name:
		raise frappe.ValidationError("Tenant name is required")

	email = row.get("email_address") or row.get("email")
	phone = row.get("phone_number") or row.get("phone")
	national_id = row.get("national_id") or row.get("id_number") or row.get("id")
	income_range = row.get("income_range") or None

	if frappe.db.exists("Property Tenant", name):
		doc = frappe.get_doc("Property Tenant", name)
		doc.phone = phone or doc.phone
		doc.email = email or doc.email
		doc.national_id = national_id or doc.national_id
		if income_range:
			doc.income_range = income_range
		if not doc.get("user"):
			doc.user = _ensure_user_with_role(email, name, "Tenant", phone, organization)
		doc.save(ignore_permissions=True)
		return doc, False

	user_id = _ensure_user_with_role(email, name, "Tenant", phone, organization)
	doc = frappe.get_doc({
		"doctype": "Property Tenant",
		"organization": organization,
		"tenant_name": name,
		"phone": phone,
		"email": email,
		"national_id": national_id,
		"income_range": income_range,
		"status": row.get("status") or "Active",
		"user": user_id,
	})
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)
	return doc, True


@frappe.whitelist()
@envelope
def bulk_create_tenants(tenants, organization=None):
	"""
	Bulk-create tenants from an imported list. Optionally assign units and mark as
	already paid (for migration of existing tenants).

	tenants: JSON list of {
		tenant_name|name, phone_number|phone, email_address|email,
		national_id, income_range?,
		# Optional migration fields:
		property?, unit?, rent?, deposit?, already_paid? (bool)
	}
	
	When already_paid=True AND property+unit provided:
	- Creates tenant + lease
	- Marks initial_payment_status as "Paid"
	- Creates Journal Entry for accounting (debit Cash, credit Rent Income + Deposits)
	- Generates migration reference number
	
	Returns {created, updated, failed, total}.
	"""
	if isinstance(tenants, str):
		tenants = frappe.parse_json(tenants) if tenants else []
	tenants = tenants or []

	org = resolve_organization(explicit=organization)
	if not org:
		raise frappe.ValidationError("No Organization configured")

	# Pre-bootstrap accounting masters ONCE (not per tenant)
	needs_accounting = any(
		row.get("already_paid") in (True, "true", "True", "yes", "Yes", "1", 1)
		and row.get("unit") and row.get("property")
		for row in tenants
	)
	
	# Cache for accounting lookups to avoid repeated queries
	accounting_cache = {}
	if needs_accounting:
		try:
			from property_management.integration.erpnext_setup import bootstrap_global_masters
			bootstrap_global_masters()
		except:
			pass

	created, updated, failed = [], [], []
	for row in tenants:
		try:
			doc, was_created = _upsert_tenant(row, org)
			
			# Handle migration: assign unit + mark as paid
			already_paid = row.get("already_paid") in (True, "true", "True", "yes", "Yes", "1", 1)
			unit = row.get("unit")
			prop = row.get("property")
			
			if unit and prop:
				# Create/sign lease for this tenant
				rent = flt(row.get("rent") or row.get("rent_amount") or 0)
				deposit = flt(row.get("deposit") or row.get("deposit_amount") or 0)
				# Non-refundable utility deposits: stored as values only (no GL impact).
				water_deposit = flt(row.get("water_deposit") or 0)
				electricity_deposit = flt(row.get("electricity_deposit") or 0)
				
				lease = _create_migration_lease(
					tenant=doc.name,
					property_name=prop,
					unit=unit,
					rent=rent,
					deposit=deposit,
					water_deposit=water_deposit,
					electricity_deposit=electricity_deposit,
					organization=org,
					already_paid=already_paid
				)
				
				if already_paid and lease:
					# Create accounting entries and mark as paid (with cache)
					_post_migration_payment(lease, rent, deposit, prop, accounting_cache)
			
			(created if was_created else updated).append(doc.name)
		except Exception as e:
			failed.append({"tenant": row.get("tenant_name") or row.get("name"), "error": str(e)})

	frappe.db.commit()
	return {"created": created, "updated": updated, "failed": failed, "total": len(tenants)}


def _create_migration_lease(tenant, property_name, unit, rent, deposit, organization,
							water_deposit=0, electricity_deposit=0, already_paid=False):
	"""Create a lease for a migrated tenant."""
	from frappe.utils import today, add_years
	
	# Check if lease already exists for this tenant+unit
	existing = frappe.db.exists("Lease Agreement", {
		"tenant": tenant,
		"unit": unit,
		"status": "Active"
	})
	if existing:
		return frappe.get_doc("Lease Agreement", existing)
	
	# Mark unit as occupied
	if frappe.db.exists("Property Unit", unit):
		frappe.db.set_value("Property Unit", unit, "status", "Occupied")
	
	total = flt(rent) + flt(deposit)
	
	lease = frappe.get_doc({
		"doctype": "Lease Agreement",
		"organization": organization,
		"tenant": tenant,
		"property": property_name,
		"unit": unit,
		"rent_amount": rent,
		"deposit_amount": deposit,
		# Non-refundable utility deposits: stored as values only, never posted to GL
		# and excluded from initial_amount_due / initial_amount_paid.
		"water_deposit": flt(water_deposit),
		"electricity_deposit": flt(electricity_deposit),
		"start_date": today(),
		"end_date": add_years(today(), 1),
		"status": "Active",
		"initial_payment_status": "Paid" if already_paid else "Pending",
		"initial_amount_due": total,
		"initial_amount_paid": total if already_paid else 0,
	})
	lease.flags.ignore_mandatory = True
	lease.insert(ignore_permissions=True)
	return lease


def _post_migration_payment(lease, rent_amount, deposit_amount, property_name, cache=None):
	"""
	Create accounting entries for a migrated tenant who already paid.
	Generates a migration reference number and creates a Journal Entry.
	Uses cache to avoid repeated lookups for company/accounts.
	"""
	from frappe.utils import today, now_datetime
	from property_management.integration.erpnext_setup import (
		get_income_account,
		ensure_deposit_liability_account,
		provision_property_cost_center,
	)
	from property_management.integration.payments import _paid_to_account
	
	cache = cache or {}
	
	# Generate migration reference: MIG-YYYYMMDD-XXXXX
	ref_date = now_datetime().strftime("%Y%m%d")
	random_suffix = frappe.generate_hash(length=5).upper()
	reference_no = f"MIG-{ref_date}-{random_suffix}"
	
	total = flt(rent_amount) + flt(deposit_amount)
	if total <= 0:
		return
	
	# Get company (cached per property)
	cache_key = f"company_{property_name}"
	if cache_key in cache:
		company = cache[cache_key]
	else:
		prop = frappe.get_doc("Property", property_name)
		company = None
		if prop.organization:
			company = frappe.db.get_value("Organization", prop.organization, "erpnext_company")
		if not company:
			company = frappe.db.get_single_value("Global Defaults", "default_company")
		cache[cache_key] = company
		cache[f"prop_{property_name}"] = prop
	
	if not company:
		frappe.log_error(f"No company found for migration payment on {property_name}")
		return
	
	# Get accounts (cached per company)
	bank_key = f"bank_{company}"
	rent_key = f"rent_{company}"
	deposit_key = f"deposit_{company}"
	cc_key = f"cc_{property_name}"
	
	if bank_key not in cache:
		cache[bank_key] = _paid_to_account(company)
	if rent_key not in cache:
		cache[rent_key] = get_income_account(company, "Rent")
	if deposit_key not in cache:
		cache[deposit_key] = ensure_deposit_liability_account(company)
	if cc_key not in cache:
		prop = cache.get(f"prop_{property_name}") or frappe.get_doc("Property", property_name)
		cache[cc_key] = prop.get("cost_center") or provision_property_cost_center(property_name)
	
	bank = cache[bank_key]
	rent_income = cache[rent_key]
	deposit_liab = cache[deposit_key]
	cost_center = cache[cc_key]
	
	# Build journal entry lines
	accounts = [{
		"account": bank,
		"debit_in_account_currency": total,
		"credit_in_account_currency": 0,
		"cost_center": cost_center,
	}]
	
	if rent_amount > 0 and rent_income:
		accounts.append({
			"account": rent_income,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": rent_amount,
			"cost_center": cost_center,
		})
	
	if deposit_amount > 0 and deposit_liab:
		accounts.append({
			"account": deposit_liab,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": deposit_amount,
			"cost_center": cost_center,
		})
	
	# Create ERPNext Journal Entry
	je = frappe.get_doc({
		"doctype": "Journal Entry",
		"voucher_type": "Journal Entry",
		"company": company,
		"posting_date": today(),
		"cheque_no": reference_no,
		"cheque_date": today(),
		"user_remark": f"Migration: Opening balance for {lease.tenant} - {property_name} (ref {reference_no})",
		"accounts": accounts,
	})
	je.flags.ignore_mandatory = True
	je.insert(ignore_permissions=True)
	je.submit()
	
	# Update lease with journal entry reference
	frappe.db.set_value("Lease Agreement", lease.name, {
		"initial_payment_status": "Paid",
		"journal_entry": je.name,
	})


@frappe.whitelist()
@envelope
def get_tenant_detail(tenant=None):
	"""
	Full detail for a single tenant for the caretaker edit surface: master fields,
	the ID images (base64), the active lease (if any) + its unit/property, and any
	extra document files (base64) attached to the tenant.
	"""
	if not tenant or not frappe.db.exists("Property Tenant", tenant):
		raise frappe.ValidationError("Tenant not found")

	from property_management.api.documents import file_to_data_url

	t = frappe.get_doc("Property Tenant", tenant)

	# Latest lease (prefer Active) for unit/property + signing state.
	leases = frappe.get_all(
		"Lease Agreement", filters={"tenant": tenant},
		fields=["name", "property", "unit", "status", "rent_amount", "deposit_amount",
				"signed_on", "tenant_signature", "caretaker_signature", "agreement_pdf",
				"start_date", "end_date", "creation"],
		order_by="creation desc",
	)
	active = next((l for l in leases if l.status == "Active"), None) or (leases[0] if leases else None)

	unit_label = ""
	property_label = ""
	if active:
		unit_label = frappe.db.get_value("Property Unit", active.unit, "unit_number") or active.unit or ""
		property_label = frappe.db.get_value("Property", active.property, "property_name") or active.property or ""

	# Extra (non-ID) document images attached to the tenant.
	documents = []
	for f in frappe.get_all(
		"File", filters={"attached_to_doctype": "Property Tenant", "attached_to_name": tenant},
		fields=["name", "file_name", "file_url"], order_by="creation desc",
	):
		# Skip the ID front/back (surfaced separately below).
		if f.file_url in (t.national_id_front, t.national_id_back):
			continue
		documents.append({
			"name": f.name,
			"file_name": f.file_name,
			"data_url": file_to_data_url(f.file_url),
		})

	return {
		"tenant": t.name,
		"tenant_name": t.tenant_name,
		"phone": t.phone or "",
		"email": t.email or "",
		"national_id": t.national_id or "",
		"income_range": t.income_range or "",
		"emergency_contact": t.emergency_contact or "",
		"status": t.status or "Active",
		"id_front": file_to_data_url(t.national_id_front),
		"id_back": file_to_data_url(t.national_id_back),
		"documents": documents,
		"lease": (active.name if active else None),
		"lease_status": (active.status if active else None),
		"unit": (active.unit if active else None),
		"unit_label": unit_label,
		"property": (active.property if active else None),
		"property_label": property_label,
		"rent_amount": (active.rent_amount if active else None),
		"deposit_amount": (active.deposit_amount if active else None),
		"is_signed": bool(active and (active.signed_on or (active.tenant_signature and active.caretaker_signature))) if active else False,
		"has_lease_pdf": bool(active and active.agreement_pdf),
	}


@frappe.whitelist()
@envelope
def update_tenant(tenant=None, tenant_name=None, phone=None, email=None, national_id=None,
				  income_range=None, emergency_contact=None, status=None,
				  national_id_front=None, national_id_back=None, documents=None, **kwargs):
	"""
	Update an existing tenant's details and (optionally) their ID images + extra
	document images. Base64 data URLs are saved as PRIVATE Files. Passing an
	existing file URL (not a data: URL) leaves that image unchanged.

	documents: JSON list of base64 data URLs to attach as additional tenant docs.
	Returns the refreshed tenant detail.
	"""
	if not tenant or not frappe.db.exists("Property Tenant", tenant):
		raise frappe.ValidationError("Tenant not found")

	doc = frappe.get_doc("Property Tenant", tenant)
	if tenant_name:
		doc.tenant_name = tenant_name
	if phone is not None:
		doc.phone = phone
	if email is not None:
		doc.email = email
	if national_id is not None:
		doc.national_id = national_id
	if income_range is not None:
		doc.income_range = income_range or None
	if emergency_contact is not None:
		doc.emergency_contact = emergency_contact
	if status:
		doc.status = status
	doc.flags.ignore_mandatory = True
	doc.save(ignore_permissions=True)

	# ID images (base64 -> PRIVATE File). Only overwrite when a new data URL is sent.
	_save_tenant_id_images(
		doc,
		national_id_front if (national_id_front and str(national_id_front).startswith("data:")) else None,
		national_id_back if (national_id_back and str(national_id_back).startswith("data:")) else None,
	)

	# Extra document images attached to the tenant.
	if isinstance(documents, str):
		documents = frappe.parse_json(documents) if documents else []
	for d in (documents or []):
		data_url = d.get("data_url") if isinstance(d, dict) else d
		if data_url and str(data_url).startswith("data:"):
			_save_data_url_as_file(data_url, "Property Tenant", tenant,
								   is_private=1, prefix="tenant_doc")

	frappe.db.commit()
	return get_tenant_detail(tenant=tenant)


@frappe.whitelist()
@envelope
def sign_lease(tenant=None, unit=None, property=None, rent=None, deposit=None,
			   lease_start=None, lease_end=None, tenant_signature=None,
			   caretaker_signature=None, signed_at=None, lease=None):
	"""
	Assign a unit to an existing tenant and sign the lease. If `lease` is given,
	RE-SIGN that existing lease (update signatures + regenerate the PDF, and move
	the unit if a different one is supplied). Otherwise create a new Lease
	Agreement via assign_tenant_to_unit.

	Returns {lease_agreement, unit, unit_status, agreement_pdf}.
	"""
	# Re-sign / update an existing lease.
	if lease and frappe.db.exists("Lease Agreement", lease):
		ld = frappe.get_doc("Lease Agreement", lease)
		if tenant_signature is not None:
			ld.tenant_signature = tenant_signature
		if caretaker_signature is not None:
			ld.caretaker_signature = caretaker_signature
		ld.signed_on = _parse_datetime(signed_at)
		if rent not in (None, ""):
			ld.rent_amount = float(rent)
		if deposit not in (None, ""):
			ld.deposit_amount = float(deposit)
		# Optionally move to a different unit.
		if unit:
			unit_name = unit if frappe.db.exists("Property Unit", unit) else frappe.db.get_value(
				"Property Unit", {"property": property or ld.property, "unit_number": unit}, "name")
			if unit_name and unit_name != ld.unit:
				old_unit = ld.unit
				ld.unit = unit_name
				if old_unit and frappe.db.exists("Property Unit", old_unit):
					frappe.db.set_value("Property Unit", old_unit, "status", "Vacant")
		ld.flags.ignore_mandatory = True
		ld.save(ignore_permissions=True)
		try:
			ld.generate_agreement_pdf()
		except Exception:
			frappe.log_error(title="lease pdf regeneration failed", message=frappe.get_traceback())
		frappe.db.commit()
		return {
			"lease_agreement": ld.name,
			"unit": ld.unit,
			"unit_status": frappe.db.get_value("Property Unit", ld.unit, "status") if ld.unit else None,
			"agreement_pdf": ld.agreement_pdf,
		}

	# New assignment: delegate to the existing helper. Call the UNWRAPPED function
	# so we don't double-apply the @envelope (which would nest the response).
	_assign = getattr(assign_tenant_to_unit, "__wrapped__", assign_tenant_to_unit)
	return _assign(
		tenant=tenant, unit=unit, property=property, rent=rent, deposit=deposit,
		lease_start=lease_start, lease_end=lease_end,
		tenant_signature=tenant_signature, caretaker_signature=caretaker_signature,
		signed_at=signed_at,
	)
