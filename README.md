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

## Docker deployment

The app is a zero-dependency Python app (stdlib `http.server` + Jinja2 + pure-Python `pymysql`). No compilation, no system libraries beyond the Python runtime. A single `Dockerfile` builds the app image; a `docker-compose.yml` pairs it with a MySQL 8 container.

### Quick start

```bash
# (a) clone the repo
git clone https://github.com/<you>/sct-app.git && cd sct-app

# (b) create your .env (copy from .env.example) and fill in the MySQL creds
cp .env.example .env

# (c) build + start the stack
docker compose up --build -d
docker compose logs -f sct-app   # watch the app start
```

Open `http://<host-or-ip>:8080`. Home `/` redirects to `/login`. Seeded demo accounts:
`admin@sct.local` / `admin123` (admin), `tech@sct.local` / `tech123` (technician), `viewer@sct.local` / `view123` (viewer).

### What each file does

| File | Purpose |
|---|---|
| `Dockerfile` | Builds the app image: `python:3.14-slim`, installs `jinja2` + `pymysql` (pure-Python, no compile step), copies the app, runs as a non-root `sct` user on port 8080. |
| `docker-compose.yml` | `sct-mysql` (MySQL 8.0) + `sct-app` (built from `Dockerfile`). `sct-app` depends on `sct-mysql` being healthy before starting, so the app's `apply_migrations()` + `seed_local_users()` run against a live DB. |
| `.dockerignore` | Excludes `data/`, `uploads/`, `__pycache__`, `.env`, `venv/`, `.git/`, tests, etc. from the image. |
| `.env.example` | Copy to `.env` (next to `docker-compose.yml`) to configure MySQL + app behaviour. |
| `scripts/setup-mysql.sh` | One-time MySQL provisioning (create DB, user, schema, seed demo users). Use it on a host-side MySQL, or let `docker-compose` do it. |

### Environment for a containerised MySQL

The `docker-compose.yml` sets `SCT_DB_HOST=sct-mysql` (the MySQL service name on the shared Docker network). All other settings fall through to the `.env` file next to `docker-compose.yml`:

| Variable | Default | Purpose |
|---|---|---|
| `SCT_DB_ENGINE` | `mysql` | `mysql` or `postgres` (postgres not yet implemented) |
| `SCT_DB_HOST` | `sct-mysql` (compose) | MySQL host |
| `SCT_DB_PORT` | `3306` | MySQL port |
| `SCT_DB_USER` | `sct` | DB user |
| `SCT_DB_PASSWORD` | `sct` (compose default) | DB password |
| `SCT_DB_NAME` | `sct` | DB name |
| `SCT_PORT` | `8080` | App listen port |
| `SCT_SECRET_KEY` | dev default | Session signing key (change in production) |
| `SCT_AUTH_MODE` | `local` | `local` (seeded demo users) or `authentik` (OIDC SSO) |
| `SCT_EMAIL_METHOD` | `local` | `local` (write to `data/emails/`) or `smtp` |

### Persistent data

- `sct_mysql_data` — MySQL data (survives `docker compose down`).
- `sct_app_data` — app `data/` (server.log). Bind mount or volume if you want to keep it.

### External access / TLS

- Publish the app port (`-p 127.0.0.1:8080:8080` as in the compose file). The `http.server` does **not** handle TLS; terminate TLS in a reverse proxy (Nginx/Traefik/Caddy) in front of the container, and do not route through Umbrel's app proxy (it requires Umbrel's auth header, which the plain `http.server` does not understand).
- The container listens on `0.0.0.0` so the host can reach it; bind to `127.0.0.1` for localhost-only access.

### Testing the image without a live MySQL

```bash
# Build the app image only (does not run the app)
docker build -t sct-app:local .

# Verify the image runs its startup (apply_migrations + seed_users) against a
# temporary throwaway MySQL, or against a running sct-mysql service.
docker run --rm --network sct-network -e SCT_DB_ENGINE=mysql sct-app:local
```

