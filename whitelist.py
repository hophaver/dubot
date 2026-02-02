"""Permission hierarchy: admin (all commands) > himas (himas + user) > user (user only)."""
import json
from integrations import WHITELIST_FILE, PERMANENT_ADMIN


def load_whitelist():
    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ("admin", "himas", "user"):
            if key in data and isinstance(data[key], list):
                data[key] = [int(x) for x in data[key]]
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        default = {"admin": [], "himas": [], "user": []}
        save_whitelist(default)
        return default


def save_whitelist(data):
    out = {}
    for key in ("admin", "himas", "user"):
        lst = data.get(key, [])
        out[key] = [str(int(x)) for x in lst]
    with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=4)

def get_user_permission(user_id):
    whitelist = load_whitelist()
    user_id = int(user_id)

    # Check permanent admin first
    if user_id == PERMANENT_ADMIN:
        return "admin"

    if user_id in whitelist.get("admin", []):
        return "admin"
    elif user_id in whitelist.get("himas", []):
        return "himas"
    elif user_id in whitelist.get("user", []):
        return "user"
    return None

def is_admin(user_id):
    return get_user_permission(user_id) == "admin"


def has_himas_permission(user_id):
    return get_user_permission(user_id) in ("admin", "himas")

def add_user_to_whitelist(user_id, permission):
    """Add user to whitelist. permission: 1=user, 2=admin. For himas set_user_role."""
    role = "admin" if permission == 2 else "user"
    return set_user_role(user_id, role)

def set_user_role(user_id, role: str):
    """Set user's role. role: 'admin', 'himas', or 'user'. Removes from other roles first."""
    user_id = int(user_id)
    if user_id == PERMANENT_ADMIN:
        return False
    if role not in ("admin", "himas", "user"):
        return False
    whitelist = load_whitelist()
    for key in ("admin", "himas", "user"):
        lst = whitelist.get(key, [])
        if user_id in lst:
            lst.remove(user_id)
            whitelist[key] = lst
    whitelist.setdefault(role, []).append(user_id)
    save_whitelist(whitelist)
    return True

def remove_user_from_whitelist(user_id):
    """Remove user from whitelist (all levels)."""
    user_id = int(user_id)
    if user_id == PERMANENT_ADMIN:
        return False
    whitelist = load_whitelist()
    changed = False
    for key in ("admin", "himas", "user"):
        lst = whitelist.get(key, [])
        if user_id in lst:
            lst.remove(user_id)
            whitelist[key] = lst
            changed = True
    if changed:
        save_whitelist(whitelist)
    return changed
