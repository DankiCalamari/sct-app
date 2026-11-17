# Deploying the SCT application with MySQL

SCT's internal business management application now persists to a **real
relational database (MySQL)**. The SQLite backend has been replaced entirely.

> The task brief asks for PostgreSQL or MySQL. SCT is a business supporting
> Australian businesses (GST, BAS, invoicing), and MySQL is the best match for a
> production IT-support data model. PostgreSQL support is gated behind
> `SCT_DB_ENGINE=postgres` if a compatible driver is installed.

---

## 1. Choosing the engine

The engine is selected at runtime, not compiled in:

| Variable | Values | Default |
|---|---|---|
| `SCT_DB_ENGINE` | `mysql` (default) or `postgres` | `mysql` |

For MySQL, the connection is configured with:

| Variable | Default | Purpose |
|---|---|---|
| `SCT_DB_HOST` | `127.0.0.1` | MySQL server host |
| `SCT_DB_PORT` | `3306` | MySQL port |
| `SCT_DB_USER` | `sct` | MySQL user |
| `SCT_DB_PASSWORD` | *(empty)* | MySQL password |
| `SCT_DB_NAME` | `sct` | Database name |
| `SCT_PYM_DIR` | *(none)* | Extra directory on `PYTHONPATH` if `pymysql` is not importable |
| `SCT_PORT` | `8080` | HTTP listen port |

Place the `pymysql` package on `PYTHONPATH` (e.g. in a virtualenv or a
deployment directory) — it is a pure-Python driver, so no C compilation is
required.

---

## 2. Local development — one-time MySQL provisioning

The app **will not start without a MySQL server**. For local development:

1. Install a MySQL server (any Debian/Ubuntu/Mac/AWS/RACI box is fine):

   ```bash
   sudo apt install mysql-server        # Debian/Ubuntu
   brew install mysql                   # macOS
   ```

2. Start it and confirm:

   ```bash
   sudo systemctl start mysql
   mysql -uroot -e "SELECT 1;"
   ```

3. Create the SCT database, user and schema automatically:

   ```bash
   cp sct-app/.env.example sct-app/.env   # (optional) set SCT_DB_* if your MySQL is not 127.0.0.1:3306/sct
   sudo -u root ./sct-app/scripts/setup-mysql.sh
   ```

   That script creates the `sct` database and the `sct`@`127.0.0.1` user, then
   applies the schema and seeds the demo sign-in accounts.

4. Run the application:

   ```bash
   cd sct-app
   python app.py
   # http://127.0.0.1:8080
   ```

   The first boot applies the MySQL schema automatically via
   `db.apply_migrations()`.

If you would rather provision the database and user by hand, run the following
SQL (as the MySQL root user) and then `./scripts/setup-mysql.sh`:

```sql
CREATE DATABASE IF NOT EXISTS sct CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'sct'@'127.0.0.1' IDENTIFIED BY 'sct';
GRANT ALL PRIVILEGE ON sct.* TO 'sct'@'127.0.0.1';
FLUSH PRIVILEGES;
```

---

## 3. First-run sign-in

When the database is first initialised, three demo accounts are created:

| Email | Role | Notes |
|---|---|---|
| `admin@sct.local` | admin | password `admin123` |
| `tech@sct.local`  | technician | password `tech123` |
| `viewer@sct.local`| viewer | password `view123` |

Change these at console with `db.create_user` before exposing the app to
anyone else (see "Security" below).

---

## 4. What this migration changed

| File | Change |
|---|---|
| `config.py` | Added `DB_ENGINE`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_CHARSET`, plus a tiny `.env` loader. `DATABASE` is now unused for the engine. |
| `db.py` | Full rewrite. MySQL (pure-Python `pymysql`) instead of `sqlite3`. Connection object exposes `execute(sql, params)` → cursor with `fetchone/fetchall/lastrowid`, plus `commit/rollback/close`. `apply_migrations()` creates the DB and applies the schema. |
| `migrations/001_initial.sql` | Re-written for MySQL (`AUTO_INCREMENT`, `ENGINE=InnoDB`, `DECIMAL(12,2)` money, `LONGTEXT`, no `PRAGMA`, `INSERT IGNORE`). |
| `app.py` | Removed `sqlite3` import and the dead `get_db()`. DB access now goes exclusively through `db_mod.get_connection()`. |
| `auth.py` | No change in behaviour — `seed_local_users`/`find_or_create_user` route through `db_mod`. |

---

## 5. Security requirements

- **Never store MySQL credentials or tokens in frontend code.** They live only
  in environment variables / the `.env` file, which is outside the web root.
- Change the default demo passwords before the app is exposed.
- If you switch to `AUTH_MODE=authentik`, roles are mapped from Authentik group
  memberships; admins/technicians/viewers are injected from Authentik, not
  created locally.
- Uploaded files are stored under `uploads/` with randomly generated names and
  extension allow-listing only (already enforced; see `config.ALLOWED_UPLOAD_EXT`).

---

## 6. What is still manual

- **The MySQL server itself** (the app does not bundle one). Provision per §2.
- **Real Hnry credentials** if you enable the Zapier/REST-API sync. Hnry has no
  public API contract, so credentials (webhook secret, API keys where used) must
  be supplied via environment, never committed. See `docs/hnrry-integration.md`.
- **SMS/email places**: the mailer is still in local (file-log) mode in this
  sandbox; point `SCT_EMAIL_METHOD=smtp` + SMTP env vars at your provider once
  live.
