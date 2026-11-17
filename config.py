"""SCT customer & service management configuration.

Settings are resolved in order:
  1. Environment variables (preferred)
  2. ``sct-app/.env`` file (loaded at startup)
  3. Hard defaults below
"""
import os
import sys

# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
UPLOAD_DIR = os.path.join(PROJECT_ROOT, "uploads")
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "templates")

DATABASE = os.environ.get("SCT_DATABASE", os.path.join(DATA_DIR, "sct.db"))

SECRET_KEY = os.environ.get(
    "SCT_SECRET_KEY",
    "dev-only-change-me-in-production--set-sct_secret_key",
)

# --------------------------------------------------------------------------- #
# Authentication mode
# --------------------------------------------------------------------------- #
# ``local``  -> built-in login for development / testing (default)
# ``authentik`` -> Authentik OIDC single sign-on, roles mapped from groups
AUTH_MODE = os.environ.get("SCT_AUTH_MODE", "local").lower()

# If AUTH_MODE=authentik, these must be supplied via environment:
AUTHNIK_URL = os.environ.get("AUTHNIK_URL", "").rstrip("/")
AUTHNIK_CLIENT_ID = os.environ.get("AUTHNIK_CLIENT_ID", "")
AUTHNIK_CLIENT_SECRET = os.environ.get("AUTHNIK_CLIENT_SECRET", "")
AUTHNIK_CALLBACK_PATH = os.environ.get("AUTHNIK_CALLBACK_PATH", "/auth/authentik/callback")
AUTHNIK_CALLBACK_URL = os.environ.get("AUTHNIK_CALLBACK_URL", "")
AUTHNIK_ADMIN_GROUP = os.environ.get("AUTHNIK_ADMIN_GROUP", "sct-admins").lower()
AUTHNIK_TECH_GROUP = os.environ.get("AUTHNIK_TECH_GROUP", "sct-techs").lower()

# --------------------------------------------------------------------------- #
# File uploads
# --------------------------------------------------------------------------- #
MAX_CONTENT_LENGTH = 25 * 1024 * 1024
ALLOWED_UPLOAD_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif",
    ".pdf", ".doc", ".docx", ".txt", ".htm", ".html",
}
ALLOWED_UPLOAD_MIMES = {
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/heic",
    "image/heif", "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain", "text/html",
}

# --------------------------------------------------------------------------- #
# Email (demo logger in local mode; SMTP in production)
# --------------------------------------------------------------------------- #
EMAIL_METHOD = os.environ.get("SCT_EMAIL_METHOD", "local").lower()  # local|smtp
EMAIL_FROM = os.environ.get("SCT_EMAIL_FROM", "noreply@sunsetcountry.tech")
SMTP_HOST = os.environ.get("SCT_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SCT_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SCT_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SCT_SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("SCT_SMTP_USE_TLS", "1") == "1"

# --------------------------------------------------------------------------- #
# Hnry integration (see docs/hnrry-integration.md)
# --------------------------------------------------------------------------- #
HNRY_WEBHOOK_URL = os.environ.get("SCT_HNRY_WEBHOOK_URL", "").rstrip("/")
HNRY_WEBHOOK_SECRET = os.environ.get("SCT_HNRY_WEBHOOK_SECRET", "")

# --------------------------------------------------------------------------- #
# Database (MySQL or PostgreSQL)
# --------------------------------------------------------------------------- #
def _load_dotenv():
    """Tiny dotenv loader (stdlib only).  ``sct-app/.env`` is loaded first."""
    base = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(base, ".env"), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_dotenv()

# Driver selection: ``mysql`` (default) or ``postgres``.
DB_ENGINE = os.environ.get("SCT_DB_ENGINE", "mysql").lower()
# Connection parameters (used when DB_ENGINE is mysql or postgres).
DB_HOST = os.environ.get("SCT_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("SCT_DB_PORT", "3306"))
DB_USER = os.environ.get("SCT_DB_USER", "sct")
DB_PASSWORD = os.environ.get("SCT_DB_PASSWORD", "")
DB_NAME = os.environ.get("SCT_DB_NAME", "sct")
DB_CHARSET = os.environ.get("SCT_DB_CHARSET", "utf8mb4")
DB_COLLATION = os.environ.get("SCT_DB_COLLATION", "utf8mb4_0900_ai_ci")

# --------------------------------------------------------------------------- #
# Timing
# --------------------------------------------------------------------------- #
LOCAL_TZ_NAME = "Australia/Melbourne"


def is_auth_enabled():
    return AUTH_MODE == "authentik"


def is_email_external():
    return EMAIL_METHOD == "smtp"
