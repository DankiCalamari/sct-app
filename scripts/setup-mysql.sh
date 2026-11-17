#!/usr/bin/env bash
#
# SCT - one-time MySQL provisioning script.
#
# Creates the SCT database and application user (if missing), then applies the
# schema and seeds the local-demo sign-in accounts.
#
# Run as the MySQL `root` user (read the password from MYSQL_PWD, or use
# `sudo -E -u root`).  No password is ever written to disk.
#
set -euo pipefail

# --- defaults (override with env vars if your MySQL is not 127.0.0.1:3306) ------
: "${SCT_DB_HOST:=127.0.0.1}"
: "${SCT_DB_PORT:=3306}"
: "${SCT_DB_USER:=sct}"
: "${SCT_DB_PASSWORD:=sct}"
: "${SCT_DB_NAME:=sct}"
# -----------------------------------------------------------------------------------

MYSQL=(mysql -h "$SCT_DB_HOST" -P "$SCT_DB_PORT" -u root --batch --skip-column-names)

# Require root so that schema/user creation is done safely.
if [ -z "${MYSQL_PWD:-}" ] && [ -t 0 ]; then
    echo "ERROR: MYSQL_PWD is required to run this script as the MySQL root user." >&2
    echo "Example:" >&2
    echo "  MYSQL_PWD=$(mysql -h $SCT_DB_HOST -P $SCT_DB_PORT -u root -p'Pick_A_Secret' -N -e \"SELECT 1\" 2>/dev/null)" >&2
    echo "  MYSQL_PWD=pass ./sct-app/scripts/setup-mysql.sh" >&2
    exit 1
fi

echo "==> Creating database user and database (if missing)..."
$MYSQL <<SQL
CREATE USER IF NOT EXISTS '$SCT_DB_USER'@'%' IDENTIFIED BY '${SCT_DB_PASSWORD}';
CREATE USER IF NOT EXISTS '$SCT_DB_USER'@'localhost' IDENTIFIED BY '${SCT_DB_PASSWORD}';
CREATE DATABASE IF NOT EXISTS \`$SCT_DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON \`$SCT_DB_NAME\`.* TO '$SCT_DB_USER'@'%';
GRANT ALL PRIVILEGES ON \`$SCT_DB_NAME\`.* TO '$SCT_DB_USER'@'localhost';
FLUSH PRIVILEGES;
SQL

echo "==> Applying schema..."
python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("SCT_DB_ENGINE", "mysql")
os.environ.setdefault("SCT_DB_HOST", "127.0.0.1")
os.environ.setdefault("SCT_DB_PORT", "3306")
os.environ.setdefault("SCT_DB_USER", "sct")
os.environ.setdefault("SCT_DB_PASSWORD", "sct")
os.environ.setdefault("SCT_DB_NAME", "sct")

import pymysql
pymysql.connect(
    host=os.environ["SCT_DB_HOST"], port=int(os.environ["SCT_DB_PORT"]),
    user=os.environ["SCT_DB_USER"], password=os.environ["SCT_DB_PASSWORD"],
    database=os.environ["SCT_DB_NAME"], connect_timeout=10).close()

import db
db.apply_migrations()
print("   schema applied to database `%s`." % os.environ["SCT_DB_NAME"])
PY

echo "==> Seeding local-demo sign-in accounts..."
python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
os.environ.setdefault("SCT_DB_ENGINE", "mysql")
os.environ.setdefault("SCT_DB_HOST", "127.0.0.1")
os.environ.setdefault("SCT_DB_PORT", "3306")
os.environ.setdefault("SCT_DB_USER", "sct")
os.environ.setdefault("SCT_DB_PASSWORD", "sct")
os.environ.setdefault("SCT_DB_NAME", "sct")

import pymysql
pymysql.connect(
    host=os.environ["SCT_DB_HOST"], port=int(os.environ["SCT_DB_PORT"]),
    user=os.environ["SCT_DB_USER"], password=os.environ["SCT_DB_PASSWORD"],
    database=os.environ["SCT_DB_NAME"], connect_timeout=10).close()

import auth, db
seed_conn = db.get_connection()
try:
    auth.seed_local_users(seed_conn)
finally:
    seed_conn.close()
print("   demo users seeded (admin@sct.local / tech@sct.local / viewer@sct.local).")
PY

echo "==> Done. Try:  cd sct-app && python app.py"
