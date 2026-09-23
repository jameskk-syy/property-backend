# Copyright (c) 2026, DADIS Estates and contributors
# For license information, please see license.txt

import frappe

UNRESTRICTED_ROLES = {"Administrator", "System Manager", "Director", "Office", "Office User", "Organization Admin"}


@frappe.whitelist()
def create_module(module_name, label=None, organization=None):
	"""Registers a new custom access module."""
	if frappe.db.exists("Module Def", module_name):
		doc = frappe.get_doc("Module Def", module_name)
		if label:
			doc.label = label
		if organization:
			doc.organization = organization
		doc.save(ignore_permissions=True)
		return doc

	doc = frappe.get_doc({
		"doctype": "Module Def",
		"module_name": module_name,
		"label": label or module_name.replace("_", " ").title(),
		"organization": organization,
		"enabled": 1
	})
	doc.insert(ignore_permissions=True)
	return doc


@frappe.whitelist()
def assign_module_to_role(role, module, can_view=1, can_create=0, can_edit=0, can_approve=0, organization=None):
	"""Attaches custom module permissions to a role."""
	existing = frappe.db.get_value(
		"Module Permission",
		{"role": role, "module": module, "organization": organization},
		"name"
	)
	if existing:
		doc = frappe.get_doc("Module Permission", existing)
		doc.can_view = int(can_view)
		doc.can_create = int(can_create)
		doc.can_edit = int(can_edit)
		doc.can_approve = int(can_approve)
		doc.save(ignore_permissions=True)
		return doc

	doc = frappe.get_doc({
		"doctype": "Module Permission",
		"organization": organization,
		"role": role,
		"module": module,
		"can_view": int(can_view),
		"can_create": int(can_create),
		"can_edit": int(can_edit),
		"can_approve": int(can_approve)
	})
	doc.insert(ignore_permissions=True)
	return doc


@frappe.whitelist()
def assign_user_to_property(user, property, organization=None):
	"""Scopes a user to a specific property."""
	existing = frappe.db.get_value(
		"User Property Assignment",
		{"user": user, "property": property},
		"name"
	)
	if existing:
		return frappe.get_doc("User Property Assignment", existing)

	doc = frappe.get_doc({
		"doctype": "User Property Assignment",
		"user": user,
		"property": property,
		"organization": organization or frappe.db.get_value("Property", property, "organization")
	})
	doc.insert(ignore_permissions=True)
	return doc


@frappe.whitelist()
def get_user_permissions(user=None):
	"""Returns complete module x action permission map for a user."""
	if not user:
		user = frappe.session.user

	user_roles = set(frappe.get_roles(user))

	# Unrestricted / Executive roles get full access across all modules
	if user == "Administrator" or bool(user_roles & UNRESTRICTED_ROLES):
		all_modules = frappe.get_all("Module Def", fields=["module_name"])
		perms = {}
		for m in all_modules:
			perms[m.module_name] = {"can_view": 1, "can_create": 1, "can_edit": 1, "can_approve": 1}
		return perms

	# Aggregated module permissions for specific roles
	perms = {}
	mod_perms = frappe.get_all(
		"Module Permission",
		filters={"role": ["in", list(user_roles)]},
		fields=["module", "can_view", "can_create", "can_edit", "can_approve"]
	)
	for row in mod_perms:
		m = row.module
		if m not in perms:
			perms[m] = {"can_view": 0, "can_create": 0, "can_edit": 0, "can_approve": 0}
		perms[m]["can_view"] = max(perms[m]["can_view"], row.can_view or 0)
		perms[m]["can_create"] = max(perms[m]["can_create"], row.can_create or 0)
		perms[m]["can_edit"] = max(perms[m]["can_edit"], row.can_edit or 0)
		perms[m]["can_approve"] = max(perms[m]["can_approve"], row.can_approve or 0)

	return perms


@frappe.whitelist()
def check_module_permission(user, module, action):
	"""
	Gate check called by controllers/APIs.
	Action must be one of: 'view', 'create', 'edit', 'approve'.
	"""
	if not user:
		user = frappe.session.user

	user_roles = set(frappe.get_roles(user))
	if user == "Administrator" or bool(user_roles & UNRESTRICTED_ROLES):
		return True

	user_perms = get_user_permissions(user)
	mod_perm = user_perms.get(module, {})
	key = f"can_{action}"
	return bool(mod_perm.get(key, 0))


@frappe.whitelist(allow_guest=True)
def get_users_list():
	"""Returns list of system users with roles, status, and module permissions."""
	# Include BOTH System Users and Website Users: Frappe auto-classifies users
	# with only portal roles (Landlord/Tenant, desk_access=0) as Website Users,
	# so filtering to System User only would hide landlords and tenants.
	users = frappe.get_all(
		"User",
		filters={"name": ["not in", ["Guest", "Administrator"]]},
		or_filters=[["user_type", "=", "System User"], ["user_type", "=", "Website User"]],
		fields=["name", "email", "first_name", "last_name", "enabled", "user_image", "creation"]
	)
	# Always include Administrator explicitly (it's excluded above to avoid dupes).
	admin = frappe.get_all(
		"User", filters={"name": "Administrator"},
		fields=["name", "email", "first_name", "last_name", "enabled", "user_image", "creation"]
	)
	users = admin + users

	res = []
	all_modules = ["Leasing", "Finance", "People", "Operations", "System"]
	for u in users:
		user_roles = set(frappe.get_roles(u.name))
		# Determine primary role. Highest privilege wins FIRST — the Administrator
		# holds every role, so admin/System Manager MUST be checked before the
		# specialized (landlord/caretaker/tenant) roles, otherwise Administrator
		# would be mis-detected as a landlord.
		if u.name == "Administrator" or bool(user_roles & UNRESTRICTED_ROLES):
			primary_role = "admin"
		elif "Caretaker" in user_roles:
			primary_role = "caretaker"
		elif "Landlord" in user_roles:
			primary_role = "landlord"
		elif "Tenant" in user_roles:
			primary_role = "tenant"
		else:
			primary_role = "admin"

		# Check custom user property for allowed_modules or fallback to role modules
		allowed_modules_raw = frappe.db.get_value("User", u.name, "bio")
		modules_list = None
		if allowed_modules_raw and allowed_modules_raw.startswith("["):
			try:
				import json
				modules_list = json.loads(allowed_modules_raw)
			except Exception:
				pass
		if modules_list is None:
			# Admins see everything; specialized roles get a sensible default.
			if primary_role == "admin":
				modules_list = all_modules
			elif primary_role == "caretaker":
				modules_list = ["Leasing", "Operations"]
			else:
				modules_list = []

		res.append({
			"id": u.name,
			"email": u.email or u.name,
			"name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.name,
			"role": primary_role,
			"status": "Active" if u.enabled else "Suspended",
			"allowed_modules": modules_list,
			"creation": str(u.creation) if u.creation else ""
		})
	return res


@frappe.whitelist(allow_guest=True)
def create_or_update_user(email, name=None, role="admin", status="Active", password=None, allowed_modules=None):
	"""Creates or updates a user in Frappe with credentials, role, and module permissions."""
	import json
	if isinstance(allowed_modules, list):
		modules_json = json.dumps(allowed_modules)
	elif isinstance(allowed_modules, str):
		modules_json = allowed_modules
	else:
		modules_json = json.dumps(["Leasing", "Finance", "People", "Operations", "System"])

	names = (name or email).split(" ", 1)
	first_name = names[0]
	last_name = names[1] if len(names) > 1 else ""

	enabled_val = 1 if status == "Active" else 0

	if frappe.db.exists("User", email):
		user_doc = frappe.get_doc("User", email)
		user_doc.first_name = first_name
		user_doc.last_name = last_name
		user_doc.enabled = enabled_val
		user_doc.bio = modules_json
		user_doc.save(ignore_permissions=True)
	else:
		user_doc = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"enabled": enabled_val,
			"send_welcome_email": 0,
			"user_type": "System User",
			"bio": modules_json
		})
		user_doc.insert(ignore_permissions=True)

	if password:
		try:
			from frappe.utils.password import update_password
			update_password(user=email, pwd=password)
		except Exception:
			pass

	# Assign Role in Frappe
	role_mapping = {
		"admin": "System Manager",
		"landlord": "Landlord",
		"caretaker": "Caretaker",
		"tenant": "Tenant"
	}
	frappe_role = role_mapping.get(role, "System Manager")
	if not frappe.db.exists("Role", frappe_role):
		r_doc = frappe.get_doc({"doctype": "Role", "role_name": frappe_role})
		r_doc.insert(ignore_permissions=True)

	user_doc.add_roles(frappe_role)

	return {
		"id": email,
		"email": email,
		"name": name or email,
		"role": role,
		"status": status,
		"allowed_modules": json.loads(modules_json) if modules_json.startswith("[") else []
	}


@frappe.whitelist(allow_guest=True)
def toggle_user_status(email, status):
	"""Toggles user active vs suspended status."""
	if frappe.db.exists("User", email):
		enabled = 1 if status == "Active" else 0
		frappe.db.set_value("User", email, "enabled", enabled)
		return {"email": email, "status": status}
	return {"error": "User not found"}


@frappe.whitelist(allow_guest=True)
def get_module_catalog():
	"""
	The authoritative access-module catalog: sections -> [{key, label}].
	The frontend renders its permission matrix from this so create/update role
	always matches exactly what the backend defines.
	"""
	from property_management.setup import ACCESS_MODULE_CATALOG
	return [
		{"section": section, "modules": [{"key": k, "label": lbl} for k, lbl in mods]}
		for section, mods in ACCESS_MODULE_CATALOG
	]


def _role_permissions(role_name):
	"""Per-key {module_key: bool} map from Module Permission for a role."""
	from property_management.setup import ACCESS_MODULE_KEYS
	perms = {k: False for k in ACCESS_MODULE_KEYS}
	for r in frappe.get_all("Module Permission", filters={"role": role_name},
							fields=["module", "can_view"]):
		if r.module in perms:
			perms[r.module] = bool(r.can_view)
	return perms


def _perms_or_full(role_name):
	"""
	Return the role's saved per-key permission map. If the role has NO Module
	Permission rows yet (fresh state), fall back to full access so newly seeded
	admin roles read as all-on until explicitly edited. Once edited (rows exist),
	the saved values are always honoured.
	"""
	from property_management.setup import ACCESS_MODULE_KEYS
	has_rows = frappe.db.exists("Module Permission", {"role": role_name})
	if has_rows:
		return _role_permissions(role_name)
	return {k: True for k in ACCESS_MODULE_KEYS}


@frappe.whitelist(allow_guest=True)
def get_roles_and_permissions():
	"""
	Returns the app's real roles with their granular (per-key) module permission
	matrix, read from Module Permission. Roles with no rows yet default to full
	access; once a role is edited/saved, the stored matrix is always returned.
	"""
	from property_management.setup import ACCESS_MODULE_KEYS

	roles_list = [{
		"id": "admin",
		"role": "System Manager",
		"name": "Administrator",
		"description": "Full access to all modules, financial reporting, and system settings.",
		"locked": True,
		# Read the saved System Manager matrix (honours edits); full access if unset.
		"permissions": _perms_or_full("System Manager"),
	}]

	# Property-domain roles, in display order, with descriptions.
	seeded = [
		("Caretaker", "Caretaker / Site Officer",
		 "Manages day-to-day maintenance, tenant onboarding, and WhatsApp communication."),
		("Landlord", "Landlord",
		 "Portfolio owner with read access to their properties and statements."),
		("Tenant", "Tenant",
		 "Tenant portal access to their lease, invoices and payments."),
		("Organization Admin", "Organization Admin",
		 "Full operational access across the organization."),
		("Director", "Director",
		 "Executive oversight and approvals across all modules."),
	]

	for role_name, label, desc in seeded:
		if not frappe.db.exists("Role", role_name):
			continue
		roles_list.append({
			"id": role_name.lower().replace(" ", "_"),
			"role": role_name,
			"name": label,
			"description": desc,
			"locked": False,
			# Honour saved permissions for every role (Org Admin/Director too).
			"permissions": _perms_or_full(role_name),
		})

	return roles_list


@frappe.whitelist()
def create_or_update_role(role_name, permissions=None, description=None):
	"""
	Create/update a Frappe Role and persist its granular module permissions as
	Module Permission rows. `permissions` is a {module_key: bool} map matching the
	catalog from get_module_catalog. Returns the saved role + permissions.
	"""
	import json
	from property_management.setup import ACCESS_MODULE_KEYS, _ensure_module_def

	if isinstance(permissions, str):
		permissions = json.loads(permissions or "{}")
	permissions = permissions or {}

	if not frappe.db.exists("Role", role_name):
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(ignore_permissions=True)

	# Upsert a Module Permission row per granular key.
	for key in ACCESS_MODULE_KEYS:
		_ensure_module_def(key)
		granted = 1 if permissions.get(key) else 0
		existing = frappe.db.get_value("Module Permission", {"role": role_name, "module": key}, "name")
		if existing:
			doc = frappe.get_doc("Module Permission", existing)
		else:
			doc = frappe.get_doc({"doctype": "Module Permission", "role": role_name, "module": key})
		doc.can_view = granted
		doc.can_create = granted
		doc.can_edit = granted
		doc.can_approve = granted
		doc.flags.ignore_permissions = True
		doc.flags.ignore_links = True
		doc.flags.ignore_mandatory = True
		if existing:
			doc.save(ignore_permissions=True)
		else:
			doc.insert(ignore_permissions=True)

	frappe.db.commit()
	return {
		"role": role_name,
		"description": description,
		"permissions": _role_permissions(role_name),
	}
