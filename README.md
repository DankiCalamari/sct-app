# SCT — Sun Country Tech Internal Web App

Zero-dependency internal web application for managing **customers, tickets, appointments, quotes, invoices (with Hnry invoicing integration), service/repair records, devices, users/roles, and audit logs**.

Built on a deliberately minimal stack:
- `http.server` (stdlib) — no Flask, FastAPI, or external WSGI server
- `jinja2` (templating)
- `pymysql` (MySQL driver)
- MySQL 8 (per user directive — "use Postgres or MySQL"; Postgres is gated via `SCT_DB_ENGINE=postgres` but not yet implemented)

## Stack snapshot

| Concern | Tool |
|---|---|
| Web server | `http.server.ThreadingHTTPServer` (stdlib) |
| Templates | `jinja2` (FileSystemLoader, autoescape) |
| Persistence | MySQL via pure-Python `pymysql` |
| Sessions | HMAC-signed cookies (`sct_session`) + in-memory token map |
| Auth modes | `local` (seeded demo users) and `authentik` (OIDC SSO, gated on env vars) |
| Email | Local-mode writes to `data/emails/`, never auto-sends in test harness |
| Hnry | Zapier bridge (no token stored) when real credentials are supplied |
| PDF | Custom text-based PDF builder (`_render_pdf`) |

## Quick start

```bash
cd sct-app

# 1. MySQL must be running on 127.0.0.1:3306.
#    See docs/setup-mysql.md and scripts/setup-mysql.sh.

# 2. (Optional) point pymysql at a local copy
export SCT_PYM_DIR=/opt/data/cache/pymysql

# 3. Run
python3 app.py
```

Open `http://<host-or-ip>:8080`. Home `/` redirects to `/login`. Use the seeded demo accounts:

| User | Email | Role |
|---|---|---|
| Admin | `admin@sct.local` | `admin` |
| Technician | `tech@sct.local` | `technician` |
| Viewer | `viewer@sct.local` | `viewer` |

> Demo passwords are `admin123`, `tech123`, `view123`. These are masked in the source; change them in production.

## Environment variables

All settings come from the environment (or an `sct-app/.env` file loaded at startup).

| Variable | Default | Purpose |
|---|---|---|
| `SCT_DB_ENGINE` | `mysql` | `mysql` or `postgres` (postgres pending implementation) |
| `SCT_DB_HOST` | `127.0.0.1` | Database host |
| `SCT_DB_PORT` | `3306` | Database port |
| `SCT_DB_USER` | `root` | Database user |
| `SCT_DB_PASSWORD` | *(empty)* | Database password |
| `SCT_DB_NAME` | `sct` | Database name |
| `SCT_PYM_DIR` | `/opt/data/cache/pymysql` | Path to `pymysql` package (for `--target` installs) |
| `SCT_PORT` | `8080` | App listen port |
| `SCT_SECRET_KEY` | dev-only, random | HMAC signing secret for session cookies |
| `SCT_AUTH_MODE` | `local` | `local` or `authentik` (disabled when env vars missing) |
| `SCT_DB_CHARSET` | `utf8mb4` | DB charset |
| `SCT_DB_COLLATION` | `utf8mb4_0900_ai_ci` | DB collation |

## Auth

### Local mode (default)
Seeds three demo users on startup. Demo passwords are stored as PBKDF2-SHA256 hashes (salt + hexdigest). The source file shows `***` where passwords would appear — read the values from your own environment / seed script.

### Authentik mode (optional)
Set `SCT_AUTH_MODE=authentik` plus `SCT_AUTHNIK_URL`, `SCT_AUTHNIK_CLIENT_ID`, `SCT_AUTHNIK_CLIENT_SECRET`, and optional group mappings (`SCT_AUTHNIK_ADMIN_GROUP`, `SCT_AUTHNIK_TECH_GROUP`). On login, the app exchanges the authorization code for an access token and fetches userinfo via OpenID Connect. Roles are mapped from the user's `groups` claim.

## Hnry integration

Three paths (decided at `sync_quote_to_hnry`):

1. **Zapier** — the public Zapier Hnry integration exposes "create client", "find client", "create invoice", "add item". OAuth is handled by Zapier itself; SCT stores no long-lived API token. This is the recommended, production-ready path.
2. **Hnry REST API** — best-effort "API mode" when real Basic-auth credentials are supplied. Credential-gated; no published OpenAPI contract, so this path is documented but untested.
3. **Manual / email** — fallback for when neither is configured.

Every attempt is logged to `integration_logs`. Credentials are stored server-side via env vars only — never in the frontend or in code.

## Email

`send_email()` in `emailer.py` has two modes:

- **Local** (default): writes to `data/emails/<timestamp>_<safe-to>` and records the message in-app. Never auto-sends to real addresses in the test harness.
- **SMTP**: set `SCT_EMAIL_SMTP_HOST`, `SCT_EMAIL_SMTP_PORT`, `SCT_EMAIL_USE_TLS` to enable.

## Authentication modes summary

| Mode | Env | Behavior |
|---|---|---|
| `local` | default | Built-in demo users, seeded on first run |
| `authentik` | `SCT_AUTH_MODE=authentik` + OIDC env vars | OIDC code flow, role mapping from groups |

## MySQL provisioning

```bash
# One-time setup (run from the repo root)
chmod +x scripts/setup-mysql.sh
sudo MYSQL_PWD=scripts/setup-mysql.sh   # or set MYSQL_PWD as root
```

The script creates the `sct` database, a DB user, applies `migrations/001_initial.sql` (22 tables), and seeds the three demo users. `docs/setup-mysql.md` documents the env vars and steps in detail.

## Tests

`tests/test_sct_mysql.py` — a pytest suite of acceptance/regression checks on the live MySQL server. All tests are marked `SKIPPED` unless the MySQL connection flags are set (`SCT_DB_HOST`, `SCT_DB_PORT`, `SCT_DB_USER`, `SCT_DB_PASSWORD`).

```bash
cd sct-app
export SCT_DB_ENGINE=mysql SCT_DB_HOST=127.0.0.1 SCT_DB_PORT=3306 SCT_DB_USER=root SCT_DB_PASSWORD=''
python3 -m pytest tests/test_sct_mysql.py -v
```

## Appendix: what this app covers

- ✅ Customers, customers list/form/detail, duplicate prevention
- ✅ Enquiries, tickets, ticket status, notes, assignments, internal/customer notes
- ✅ Appointments (calendar view), quote lifecycle, approvals
- ✅ Invoices (list/detail/sync-hnry/mark-paid/pdf), quote->invoice conversion
- ✅ Service records with PDF report generation
- ✅ Devices, users/roles (admin), audit log, file uploads
- ✅ Reports page, settings page, integrations page
- ⚠️  Hnry sync (no live credentials — Zapier decision tree present)
- ⚠️  Authentik OIDC (unwired without real env vars)
- ⚠️  PostgreSQL engine (gated, not implemented)

## Deployment notes

- App is a single `python3 app.py` process on `0.0.0.0:8080`.
- In an Umbrel container, publish the port (`-p 8080:8080`) or place behind a reverse proxy. The plain `http.server` does **not** handle Umbrel's auth header.
- Use a reverse proxy for TLS/HTTPS. The app sets `X-Content-Type-Options: nosniff` and `SameSite=Lax` session cookies.
