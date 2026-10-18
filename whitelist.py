"""Permission hierarchy: admin (all commands) > himas (himas + user) > user (user only)."""
from integrations import WHITELIST_FILE, PERMANENT_ADMIN
from utils.json_store import CachedJsonFile

_ROLES = ("admin", "himas", "user")


def _normalize(data):
    data = data if isinstance(data, dict) else {}
    out = {}
    for key in _ROLES:
        lst = data.get(key, [])
        out[key] = [int(x) for x in lst] if isinstance(lst, list) else []
    return out


# A corrupt file is backed up and treated as empty, but never overwritten until the next explicit save.
_store = CachedJsonFile(WHITELIST_FILE, default={k: [] for k in _ROLES}, normalize=_normalize)


def load_whitelist():
    return _store.get()


def save_whitelist(data):
    _store.save({key: [str(int(x)) for x in data.get(key, [])] for key in _ROLES})


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

def is_permanent_admin(user_id) -> bool:
    return int(user_id) == PERMANENT_ADMIN


def is_admin(user_id):
    return get_user_permission(user_id) == "admin"


def has_himas_permission(user_id):
    return get_user_permission(user_id) in ("admin", "himas")

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
