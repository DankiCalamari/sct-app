"""Authentication, sessions, roles, and optional Authentik OIDC SSO.

Modes (selected by SCT_AUTH_MODE):
  * ``local``  -> built-in login with seeded demo credentials. Role based
                  permissions: admin / technician / viewer.
  * ``authentik`` -> Authentik as IdP via OpenID Connect (code flow). Roles are
                  mapped from user groups; missing env vars fall back to a
                  disabled state that renders a clear message.

For testing and local development the built-in "local" mode is the default.
"""
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

import jwt

import config

# --------------------------------------------------------------------------- #
# Demo (local) credentials - only used when SCT_AUTH_MODE=local
# --------------------------------------------------------------------------- #
DEMO_USERS = [
    {"email": "admin@sct.local", "password": "admin123", "name": "Admin User", "role": "admin"},
    {"email": "tech@sct.local", "password": "tech123", "name": "Technician User", "role": "technician"},
    {"email": "viewer@sct.local", "password": "view123", "name": "Viewer User", "role": "viewer"},
]

# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #
_sessions = {}          # token -> {"user_id", "role", "name", "exp"}
SESSION_TTL = 60 * 60 * 24 * 7          # 7 days

def _sign(payload: bytes) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()

def create_session(user_id, role, name):
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "user_id": user_id,
        "role": role,
        "name": name,
        "exp": time.time() + SESSION_TTL,
    }
    return token

def get_session_user(token):
    if not token:
        return None
    s = _sessions.get(token)
    if s is None or s["exp"] < time.time():
        return None
    return s

def delete_session(token):
    _sessions.pop(token, None)

# --------------------------------------------------------------------------- #
# Local password hash (PBKDF2, stdlib)
# --------------------------------------------------------------------------- #
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000)
    return f"{salt}${dk.hex()}"

def verify_password(password, stored):
    try:
        salt, hexdigest = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000)
    return hmac.compare_digest(dk.hex(), hexdigest)


def seed_local_users(conn):
    """Create demo users on first run (local mode)."""
    for u in DEMO_USERS:
        exists = conn.execute("SELECT id FROM users WHERE email = %s", (u["email"],)).fetchone()
        if exists is None:
            from db import create_user
            create_user(conn, u["email"], hash_password(u["password"]), u["name"], u["role"])


# --------------------------------------------------------------------------- #
# Authentik OIDC support
# --------------------------------------------------------------------------- #
def authentik_configured():
    return bool(config.AUTHNIK_URL and config.AUTHNIK_CLIENT_ID and config.AUTHNIK_CLIENT_SECRET)


class AuthentikClient:
    def __init__(self):
        self.base = config.AUTHNIK_URL
        self.client_id = config.AUTHNIK_CLIENT_ID
        self.secret = config.AUTHNIK_CLIENT_SECRET
        self.redirect_uri = config.AUTHNIK_CALLBACK_URL or (
            f"{config.AUTHNIK_CALLBACK_PATH}".rsplit("/", 1)[0]
        )

    # -- public endpoints (discovery) --------------------------------------- #
    @property
    def well_known(self):
        return f"{self.base}/.well-known/openid-configuration"

    def _get(self, url, params=None):
        import urllib.request

        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.json()

    def _post(self, url, data):
        import urllib.request

        body = urlencode(data).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.json()

    def authorize_url(self, state):
        from urllib.parse import urlencode

        return f"{self.base}/authorize?{urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
            "scope": "openid profile email groups",
        })}"

    def exchange_code(self, code, state):
        from urllib.parse import urlencode

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.secret,
        }
        return self._post(f"{self.base}/token", data)

    def userinfo(self, id_token):
        # Authentik returns a JWT in the code flow; decode to get claims.
        try:
            unverified = jwt.decode(id_token, options={"verify_signature": False})
            return unverified
        except Exception:
            return {}


def find_or_create_user(conn, profile):
    """Map an Authentik userinfo profile to an SCT user."""
    email = (profile.get("email") or "").strip().lower()
    name = profile.get("name") or profile.get("preferred_username") or email or "Authentik User"
    # Derive role from groups (lowercased names match config aliases).
    groups = [g.lower() for g in profile.get("groups") or []]
    if any(g in config.AUTHNIK_ADMIN_GROUP for g in groups):
        role = "admin"
    elif any(g in config.AUTHNIK_TECH_GROUP for g in groups):
        role = "technician"
    else:
        role = "viewer"
    if email:
        row = conn.execute("SELECT id, role FROM users WHERE lower(email) = ?", (email,)).fetchone()
        if row:
            from db import update_user
            update_user(conn, row["id"], role=role)
            uid = row["id"]
        else:
            from db import create_user
            uid = create_user(conn, email, hash_password(secrets.token_hex(16)), name, role)
    else:
        # Fallback: use authentik sub as a temporary local account.
        sub = profile.get("sub") or email
        row = conn.execute("SELECT id, role FROM users WHERE email = %s", (sub,)).fetchone()
        if row:
            from db import update_user
            update_user(conn, row["id"], role=role)
            uid = row["id"]
        else:
            from db import create_user
            uid = create_user(conn, sub, hash_password(secrets.token_hex(16)), name, role)
    return uid
