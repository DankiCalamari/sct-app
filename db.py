"""MySQL persistence layer for SCT.

Replaces the SQLite backend with a real RDBMS (MySQL, via the pure-Python
``pymysql`` driver).  PostgreSQL support is gated behind ``SCT_DB_ENGINE=postgres``
if a suitable driver is installed.

Public API is the same as the old ``db.py``: the connection object exposes
``execute(sql, params=...)`` which returns a cursor-like object with
``fetchone()`` / ``fetchall()`` / ``lastrowid``, plus ``commit`` / ``rollback`` /
``close`` and context-manager support, so the rest of the application does not
need to change.
"""

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------------------- #
# driver bootstrap
# --------------------------------------------------------------------------- #
def _ensure_pymysql():
    """Import pymysql (or ``SCT_PYM_DIR`` if it is not on the default path)."""
    try:
        import pymysql  # noqa: F401
        return
    except ImportError:
        pass
    enc = os.environ.get("SCT_PYM_DIR")
    if enc and enc not in sys.path:
        sys.path.insert(0, enc)
    import pymysql  # noqa: F401


_ensure_pymysql()
import pymysql  # noqa: E402
from pymysql import cursors as _cursors  # noqa: E402

import config  # noqa: E402

_SCHEMA_PATH = os.path.join(config.PROJECT_ROOT, "migrations", "001_initial.sql")

# --------------------------------------------------------------------------- #
# timestamps
# --------------------------------------------------------------------------- #
_MELBOURNE_OFFSET = timezone(timedelta(hours=11))  # AEST (+10)/AEDT (+11)


def now_iso():
    """Current UTC instant as an ISO-8601 string (second precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_melbourne(iso_str):
    """Render a UTC ISO-8601 instant in Australia/Melbourne for display."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        melbourne = dt.astimezone(_MELBOURNE_OFFSET)
        return melbourne.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str


def short_date(iso_str):
    return (iso_str or "")[:10]


def short_time(iso_str):
    return (iso_str or "")[11:16]


# --------------------------------------------------------------------------- #
# connection layer
# --------------------------------------------------------------------------- #
def _new_connection(db_name=None):
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=db_name,
        charset=config.DB_CHARSET,
        cursorclass=_cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


class _Cursor:
    """Minimal cursor-like object carrying results from a pymysql cursor."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class MySQLConnection:
    """Thin DB-API-2.0 compatible wrapper around a pymysql connection."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return _Cursor(cur)

    def executescript(self, sql):
        """Run a multi-statement schema script (when ``conn.execute`` cannot)."""
        for stmt in split_sql_script(sql):
            if stmt.strip():
                self._conn.cursor().execute(stmt)
        self._conn.commit()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def get_connection(db_path=None):
    """Open a connection to the configured MySQL database, with a dict row factory."""
    return MySQLConnection(_new_connection(db_name=config.DB_NAME))


def _connect_without_db():
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        charset=config.DB_CHARSET,
        cursorclass=_cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )


def apply_migrations(db_path=None):
    """Create the database (if needed) and apply the full schema."""
    # 1. create the database if it does not exist yet
    conn = _connect_without_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` "
                "CHARACTER SET %s COLLATE %s" % (config.DB_NAME, config.DB_CHARSET, config.DB_COLLATION)
            )
        conn.commit()
    finally:
        conn.close()

    # 2. apply the schema
    conn = MySQLConnection(_new_connection(db_name=config.DB_NAME))
    try:
        _apply_schema(conn)
    finally:
        conn.close()


def _apply_schema(conn):
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        sql = fh.read()
    for stmt in split_sql_script(sql):
        if stmt.strip():
            conn.execute(stmt).close()
    conn.commit()


def split_sql_script(sql):
    """Split a SQL script on statement boundaries, dropping comments."""
    sql = _COMMENT_RE.sub(" ", sql)
    chunks = []
    for line in sql.splitlines():
        line = line.strip()
        if line.startswith("--"):
            continue
        chunks.append(line)
    sql = "\n".join(chunks)
    parts = sql.split(";")
    return [p.strip() for p in parts if p.strip()]


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


# --------------------------------------------------------------------------- #
# row helpers
# --------------------------------------------------------------------------- #
def _first_one(conn, sql, params=()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return row[0] if isinstance(row, tuple) else next(iter(row.values()))


def _fetch_all(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


# --------------------------------------------------------------------------- #
# Public accessor functions (one per domain)
# --------------------------------------------------------------------------- #
# --- Users ------------------------------------------------------------------ #
def user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


def user_by_email(conn, email):
    return conn.execute(
        "SELECT * FROM users WHERE lower(email) = lower(%s)", (email,)
    ).fetchone()


def create_user(conn, email, password_hash, name, role="admin"):
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, name, role, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (email, password_hash, name, role, now, now),
    )
    conn.commit()
    return cur.lastrowid


def update_user(conn, user_id, **fields):
    allowed = {"name", "email", "role", "is_active"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    sets = ", ".join("%s = %%s" % k for k in data)
    data["updated_at"] = now_iso()
    data["id"] = user_id
    conn.execute("UPDATE users SET %s WHERE id = %%s" % sets, list(data.values()) + [user_id])
    conn.commit()


def user_count(conn):
    return _first_one(conn, "SELECT COUNT(*) FROM users")


# --- Customers --------------------------------------------------------------- #
def next_customer_id(conn):
    return "CST-%s" % uuid.uuid4().hex[:8].upper()


def save_customer(conn, fields):
    """Create or update a customer via a flat dict. Returns the customer id."""
    customer_id = fields.get("customer_id") or next_customer_id(conn)
    now = now_iso()
    keys = [
        "first_name", "last_name", "business_name", "email", "phone_primary",
        "phone_alternative", "customer_type", "billing_address", "service_address",
        "preferred_contact", "notes", "status", "last_interaction",
    ]
    cols = ["customer_id"] + keys + ["date_created", "updated_at"]
    placeholders = ", ".join(["%s"] * len(cols))
    vals = [customer_id] + [fields.get(k) for k in keys] + [now, now]
    conn.execute(
        "INSERT INTO customers (%s) VALUES (%s)"
        % (", ".join(cols), placeholders),
        vals,
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def customer_by_id(conn, customer_id):
    return conn.execute("SELECT * FROM customers WHERE id = %s", (customer_id,)).fetchone()


def customers_list(conn, search="", status=None, type_=None, limit=200):
    sql = ("SELECT * FROM customers" + (" WHERE 1=1" if search or status or type_ else ""))
    params = []
    if search:
        sql += (" AND (first_name LIKE %s OR last_name LIKE %s OR email LIKE %s "
                "OR customer_id LIKE %s)")
        like = "%" + search + "%"
        params = [like] * 4
    if status:
        sql += " AND status = %s"
        params.append(status)
    if type_:
        sql += " AND customer_type = %s"
        params.append(type_)
    sql += " ORDER BY last_name ASC LIMIT %s"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def count_customers(conn, status=None, type_=None):
    sql = "SELECT COUNT(*) FROM customers"
    params = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    if type_:
        sql += (" WHERE status = %s AND customer_type = %s"
                if status else " WHERE customer_type = %s")
        params.append(type_)
    return _first_one(conn, sql, params)


def fuzzy_phone(phone):
    """Normalise a phone number for fuzzy comparisons."""
    if not phone:
        return ""
    return "".join(c for c in str(phone) if c.isdigit())


def find_duplicates(conn, first_name="", last_name="", email="", phone=""):
    """Return customer rows that are near-duplicates (case-insensitive)."""
    email_norm = (email or "").strip().lower()
    phone_norm = fuzzy_phone(phone)
    results = []
    sql = "SELECT * FROM customers WHERE status != 'archived'"
    params = []
    if first_name or last_name or email or phone:
        sql += (" AND (first_name LIKE %s OR last_name LIKE %s OR email LIKE %s "
                "OR customer_id LIKE %s)")
        like = "%" + (first_name or "") + "%"
        params += [like] * 4
    rows = conn.execute(sql, params).fetchall()
    for c in rows:
        reason = []
        if email_norm:
            c_email = (c.get("email") or "").lower()
            if email_norm == c_email or email_norm in c_email or c_email in email_norm:
                reason.append("email")
        if phone_norm:
            c_phone = fuzzy_phone(c.get("phone_primary") or "")
            if phone_norm == c_phone or phone_norm in c_phone:
                reason.append("phone")
            c_alt = fuzzy_phone(c.get("phone_alternative") or "")
            if phone_norm == c_alt or phone_norm in c_alt:
                reason.append("phone")
        if (first_name or "").lower() == (c.get("first_name") or "").lower() and \
           (last_name or "").lower() == (c.get("last_name") or "").lower():
            reason.append("name")
        if reason:
            results.append((c, ", ".join(reason)))
    return results


def count_duplicates(conn, first_name="", last_name="", email="", phone=""):
    return len(find_duplicates(conn, first_name, last_name, email, phone))


def save_device(conn, customer_id, ticket_id, make, model, serial_imei, device_info):
    customer_id = _resolve_customer_id(conn, customer_id)
    ticket_id = resolve_ticket_id(conn, ticket_id)
    conn.execute(
        "INSERT INTO devices (customer_id, ticket_id, make, model, serial_imei, device_info, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (customer_id, ticket_id, make, model, serial_imei, device_info, now_iso()),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def devices_by_customer(conn, customer_id):
    return conn.execute(
        "SELECT * FROM devices WHERE customer_id = %s ORDER BY created_at DESC",
        (customer_id,),
    ).fetchall()


# --- Tickets ----------------------------------------------------------------- #
def next_ticket_ref():
    return "TKT-%s" % uuid.uuid4().hex[:8].upper()


def save_ticket(conn, fields):
    ticket_ref = fields.get("ticket_ref") or next_ticket_ref()
    now = now_iso()
    customer_id = _resolve_customer_id(conn, fields.get("customer_id"))
    enquiry_id = resolve_enquiry_id(conn, fields.get("enquiry_id"))
    keys = [
        "source", "customer_id", "enquiry_id", "title", "description", "device_make",
        "device_model", "serial_imei", "symptoms", "diagnosis", "priority", "status",
        "assigned_to", "resolution",
    ]
    cols = ["ticket_ref"] + keys + ["created_at", "updated_at"]
    vals = [ticket_ref]
    for k in keys:
        v = fields.get(k)
        if v is None:
            v = {"source": "manual", "status": "new_enquiry"}.get(k)
        if k == "customer_id":
            v = customer_id
        if k == "enquiry_id":
            v = enquiry_id
        vals.append(None if v is None else str(v))
    vals += [now, now]
    conn.execute(
        "INSERT INTO tickets (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join(["%s"] * len(cols))),
        vals,
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_id()").fetchone()[0]
    _append_status_history(conn, new_id, None, fields.get("status") or "new_enquiry",
                           fields.get("assigned_to"))
    return new_id


def ticket_by_id(conn, ticket_id):
    return conn.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,)).fetchone()


def tickets_list(conn, search="", status=None, customer_id=None, assigned_to=None,
                 priority=None, limit=500):
    sql = ("SELECT tickets.*, customers.customer_id AS customer_id FROM tickets "
           "JOIN customers ON customers.id = tickets.customer_id")
    params = []
    if search:
        sql += (" AND (tickets.title LIKE %s OR tickets.description LIKE %s "
                "OR tickets.device_make LIKE %s OR tickets.ticket_ref LIKE %s)")
        like = "%" + search + "%"
        params = [like] * 4
    if status:
        sql += " AND tickets.status = %s"
        params.append(status)
    if customer_id:
        sql += " AND tickets.customer_id = %s"
        params.append(customer_id)
    if assigned_to:
        sql += " AND tickets.assigned_to = %s"
        params.append(assigned_to)
    if priority:
        sql += " AND tickets.priority = %s"
        params.append(priority)
    sql += (" ORDER BY tickets.status NOT IN ('completed','closed','cancelled'), "
            "tickets.created_at DESC, tickets.id DESC LIMIT %s")
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def count_tickets(conn, **filters):
    sql = ("SELECT COUNT(*) FROM tickets JOIN customers ON customers.id = tickets.customer_id")
    params = []
    conditions = []
    if filters.get("status"):
        conditions.append("tickets.status = %s")
        params.append(filters["status"])
    if filters.get("customer_id"):
        conditions.append("tickets.customer_id = %s")
        params.append(filters["customer_id"])
    if filters.get("assigned_to"):
        conditions.append("tickets.assigned_to = %s")
        params.append(filters["assigned_to"])
    if filters.get("priority"):
        conditions.append("tickets.priority = %s")
        params.append(filters["priority"])
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return _first_one(conn, sql, params)


def _append_status_history(conn, ticket_id, from_status, to_status, changed_by=None, note=""):
    conn.execute(
        "INSERT INTO ticket_status_history (ticket_id, from_status, to_status, changed_by, note, changed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (ticket_id, from_status, to_status, changed_by, note, now_iso()),
    )
    conn.commit()


def ticket_status_history(conn, ticket_id):
    return conn.execute(
        "SELECT * FROM ticket_status_history WHERE ticket_id = %s ORDER BY id ASC",
        (ticket_id,),
    ).fetchall()


def next_appt_ref():
    return "APT-%s" % uuid.uuid4().hex[:8].upper()


def save_appointment(conn, fields):
    appt_ref = fields.get("appt_ref") or next_appt_ref()
    now = now_iso()
    customer_id = _resolve_customer_id(conn, fields.get("customer_id"))
    ticket_id = resolve_ticket_id(conn, fields.get("ticket_id"))
    keys = [
        "ticket_id", "appt_type", "scheduled_for", "start_time", "duration_minutes",
        "location", "notes", "assigned_to", "status",
    ]
    cols = ["appt_ref", "customer_id"] + keys + ["created_at", "updated_at"]
    vals = [appt_ref, customer_id]
    for k in keys:
        v = fields.get(k)
        if v is None:
            v = {"status": "scheduled"}.get(k)
        if k == "ticket_id":
            v = ticket_id
        vals.append(None if v is None else str(v))
    vals += [now, now]
    conn.execute(
        "INSERT INTO appointments (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join(["%s"] * len(cols))),
        vals,
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def appointment_by_id(conn, appt_id):
    return conn.execute("SELECT * FROM appointments WHERE id = %s", (appt_id,)).fetchone()


def appointments_list(conn, search="", status=None, customer_id=None, ticket_id=None,
                      limit=500):
    sql = ("SELECT appointments.*, customers.customer_id AS customer_id FROM appointments "
           "JOIN customers ON customers.id = appointments.customer_id")
    params = []
    if search:
        sql += (" AND (appointments.notes LIKE %s OR appointments.location LIKE %s "
                "OR appointments.appt_type LIKE %s OR appointments.appt_ref LIKE %s)")
        like = "%" + search + "%"
        params = [like] * 4
    if status:
        sql += " AND appointments.status = %s"
        params.append(status)
    if customer_id:
        sql += " AND appointments.customer_id = %s"
        params.append(customer_id)
    if ticket_id:
        sql += " AND appointments.ticket_id = %s"
        params.append(ticket_id)
    sql += (" ORDER BY appointments.scheduled_for ASC, appointments.start_time ASC "
            "LIMIT %s")
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def upcoming_appointments(conn, now=None, limit=200):
    """Appointments from now until two weeks ahead, sorted by start time."""
    now = now or now_iso()
    return conn.execute(
        (
            "SELECT appointments.*, customers.customer_id AS customer_id FROM appointments "
            "JOIN customers ON customers.id = appointments.customer_id "
            "WHERE STR_TO_DATE(CONCAT(appointments.scheduled_for, ' ', appointments.start_time), '%%Y-%%m-%%d %%H:%%i:%%S') "
            ">= STR_TO_DATE(%s, '%%Y-%%m-%%dT%%H:%%i:%%S') "
            "ORDER BY appointments.scheduled_for ASC, appointments.start_time ASC LIMIT %s"
        ),
        (now, limit),
    ).fetchall()


# --- Quotes ------------------------------------------------------------------ #
def next_quote_ref():
    return "QT-%s" % uuid.uuid4().hex[:8].upper()


def save_quote(conn, fields):
    quote_ref = fields.get("quote_ref") or next_quote_ref()
    now = now_iso()
    customer_id = _resolve_customer_id(conn, fields.get("customer_id"))
    ticket_id = resolve_ticket_id(conn, fields.get("ticket_id"))
    keys = [
        "ticket_id", "subtotal", "gst_amount", "total", "issue_date", "expiry_date",
        "status", "issued_to_email", "terms",
    ]
    cols = ["quote_ref", "customer_id"] + keys + ["created_at", "updated_at"]
    vals = [quote_ref, customer_id]
    for k in keys:
        v = fields.get(k)
        if v is None:
            v = {"status": "draft"}.get(k)
        if k == "ticket_id":
            v = ticket_id
        vals.append(None if v is None else str(v))
    vals += [now, now]
    conn.execute(
        "INSERT INTO quotes (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join(["%s"] * len(cols))),
        vals,
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def quote_by_id(conn, quote_id):
    return conn.execute("SELECT * FROM quotes WHERE id = %s", (quote_id,)).fetchone()


def quote_by_ref(conn, quote_ref):
    return conn.execute("SELECT * FROM quotes WHERE quote_ref = %s", (quote_ref,)).fetchone()


def quote_approvals_list(conn, quote_id=None):
    sql = "SELECT * FROM quote_approvals"
    params = []
    if quote_id:
        sql += " WHERE quote_id = %s"
        params.append(quote_id)
    sql += " ORDER BY id DESC"
    return conn.execute(sql, params).fetchall()


def save_quote_approval(conn, quote_id, approver_name, approver_email, comment):
    conn.execute(
        "INSERT INTO quote_approvals (quote_id, approver_name, approver_email, comment, created_at)"
        " VALUES (%s, %s, %s, %s, %s)",
        (quote_id, approver_name, approver_email, comment, now_iso()),
    )
    conn.commit()


def update_quote_status(conn, quote_id, status):
    conn.execute(
        "UPDATE quotes SET status = %s, updated_at = %s WHERE id = %s",
        (status, now_iso(), quote_id),
    )
    conn.commit()


def quote_line_items(conn, quote_id):
    return conn.execute(
        "SELECT * FROM quote_line_items WHERE quote_id = %s ORDER BY id", (quote_id,)
    ).fetchall()


def save_quote_line_item(conn, quote_id, description, quantity, unit_price, amount, gst_included=0):
    conn.execute(
        "INSERT INTO quote_line_items (quote_id, description, quantity, unit_price, amount, gst_included)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (quote_id, description, quantity, unit_price, amount, gst_included),
    )
    conn.commit()


# --- Invoices ---------------------------------------------------------------- #
def next_invoice_ref():
    return "INV-%s" % uuid.uuid4().hex[:8].upper()


def save_invoice(conn, fields):
    invoice_ref = fields.get("invoice_ref") or next_invoice_ref()
    now = now_iso()
    customer_id = _resolve_customer_id(conn, fields.get("customer_id"))
    ticket_id = resolve_ticket_id(conn, fields.get("ticket_id"))
    quote_id = resolve_quote_id(conn, fields.get("quote_id"))
    keys = [
        "ticket_id", "quote_id", "subtotal", "gst_amount", "total", "issue_date",
        "due_date", "status", "payment_status", "payment_date", "hnry_ref", "hnry_status",
        "notes",
    ]
    cols = ["invoice_ref", "customer_id"] + keys + ["created_at", "updated_at"]
    vals = [invoice_ref, customer_id]
    for k in keys:
        v = fields.get(k)
        if v is None:
            v = {"status": "draft", "payment_status": "pending"}.get(k)
        if k == "ticket_id":
            v = ticket_id
        if k == "quote_id":
            v = quote_id
        vals.append(None if v is None else str(v))
    vals += [now, now]
    conn.execute(
        "INSERT INTO invoices (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join(["%s"] * len(cols))),
        vals,
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def invoice_by_id(conn, invoice_id):
    return conn.execute("SELECT * FROM invoices WHERE id = %s", (invoice_id,)).fetchone()


def invoice_by_ref(conn, invoice_ref):
    return conn.execute("SELECT * FROM invoices WHERE invoice_ref = %s", (invoice_ref,)).fetchone()


def invoices_list(conn, search="", status=None, customer_id=None, payment_status=None,
                  limit=500):
    sql = ("SELECT invoices.*, customers.customer_id AS customer_id FROM invoices "
           "JOIN customers ON customers.id = invoices.customer_id")
    params = []
    if search:
        sql += (" AND (invoices.invoice_ref LIKE %s OR invoices.notes LIKE %s "
                "OR invoices.hnry_ref LIKE %s)")
        like = "%" + search + "%"
        params = [like] * 3
    if status:
        sql += " AND invoices.status = %s"
        params.append(status)
    if payment_status:
        sql += " AND invoices.payment_status = %s"
        params.append(payment_status)
    if customer_id:
        sql += " AND invoices.customer_id = %s"
        params.append(customer_id)
    sql += (" ORDER BY invoices.issue_date DESC, invoices.id DESC LIMIT %s")
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def invoice_line_items(conn, invoice_id):
    return conn.execute(
        "SELECT * FROM invoice_line_items WHERE invoice_id = %s ORDER BY id", (invoice_id,)
    ).fetchall()


def save_invoice_line_item(conn, invoice_id, description, quantity, unit_price, amount, gst_rate=0.10):
    conn.execute(
        "INSERT INTO invoice_line_items (invoice_id, description, quantity, unit_price, amount, gst_rate)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (invoice_id, description, quantity, unit_price, amount, gst_rate),
    )
    conn.commit()


# --- Service records --------------------------------------------------------- #
def save_service_record(conn, fields):
    now = now_iso()
    customer_id = _resolve_customer_id(conn, fields.get("customer_id"))
    ticket_id = resolve_ticket_id(conn, fields.get("ticket_id"))
    device_id = resolve_device_id(conn, fields.get("device_id"))
    quote_id = resolve_quote_id(conn, fields.get("quote_id"))
    invoice_id = resolve_invoice_id(conn, fields.get("invoice_id"))
    keys = [
        "customer_id", "ticket_id", "device_id", "service_date", "issue", "diagnostics",
        "work_performed", "parts_used", "labour_hours", "technician_notes", "photos",
        "outcome", "warranty", "quote_id", "invoice_id",
    ]
    vals = [now, now]
    for k in keys:
        if k == "customer_id":
            v = customer_id
        elif k == "ticket_id":
            v = ticket_id
        elif k == "device_id":
            v = device_id
        elif k == "quote_id":
            v = quote_id
        elif k == "invoice_id":
            v = invoice_id
        else:
            v = fields.get(k)
        vals.append(None if v is None else str(v))
    cols = ["created_at", "updated_at"] + keys
    conn.execute(
        "INSERT INTO service_records (%s) VALUES (%s)"
        % (", ".join(cols), ", ".join(["%s"] * len(cols))),
        vals,
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def service_records(conn, customer_id=None, ticket_id=None, limit=500):
    sql = "SELECT * FROM service_records"
    params = []
    if customer_id:
        sql += " WHERE customer_id = %s"
        params.append(customer_id)
    if ticket_id:
        sql += " AND ticket_id = %s"
        params.append(ticket_id)
    sql += (" ORDER BY service_date DESC, id DESC LIMIT %s")
    params.append(limit)
    return conn.execute(sql, params).fetchall()


# --- Work notes -------------------------------------------------------------- #
def save_work_note(conn, customer_id, ticket_id, service_record_id, note_type, author, note):
    conn.execute(
        "INSERT INTO work_notes (customer_id, ticket_id, service_record_id, note_type, author, note, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (customer_id, ticket_id, service_record_id, note_type, author, note, now_iso()),
    )
    conn.commit()


def work_notes(conn, customer_id, ticket_id=None, service_record_id=None, note_type=None, limit=200):
    sql = "SELECT * FROM work_notes WHERE customer_id = %s"
    params = [customer_id]
    if ticket_id:
        sql += " AND ticket_id = %s"
        params.append(ticket_id)
    if service_record_id:
        sql += " AND service_record_id = %s"
        params.append(service_record_id)
    if note_type:
        sql += " AND note_type = %s"
        params.append(note_type)
    sql += (" ORDER BY created_at ASC, id ASC LIMIT %s")
    params.append(limit)
    return conn.execute(sql, params).fetchall()


# --- Audit logs -------------------------------------------------------------- #
def log_audit(conn, user_id, action, entity_type, entity_id, details=None):
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, entity_type, entity_id, details, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, action, entity_type, entity_id, json.dumps(details or {}, default=str), now_iso()),
    )
    conn.commit()


def audit_logs(conn, limit=200):
    return conn.execute(
        "SELECT * FROM audit_logs ORDER BY id DESC LIMIT %s", (limit,)
    ).fetchall()


# --- File uploads ------------------------------------------------------------ #
def save_upload(conn, original_name, stored_name, mime_type, size_bytes, user_id,
                reference_type, reference_id):
    conn.execute(
        "INSERT INTO file_uploads (original_name, stored_name, mime_type, size_bytes, uploaded_by, uploaded_at, reference_type, reference_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (original_name, stored_name, mime_type, size_bytes, user_id, now_iso(), reference_type, reference_id),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_id()").fetchone()[0]


def file_uploads_by_ref(conn, reference_type, reference_id):
    return conn.execute(
        "SELECT * FROM file_uploads WHERE reference_type = %s AND reference_id = %s ORDER BY id DESC",
        (reference_type, reference_id),
    ).fetchall()


# --- Integration logs -------------------------------------------------------- #
def integration_log(conn, integration, action, payload=None, result=None, error=None):
    conn.execute(
        "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (integration, action, json.dumps(payload or {}, default=str),
         json.dumps(result or {}, default=str), error, now_iso()),
    )
    conn.commit()


def integration_logs(conn, limit=200):
    return conn.execute(
        "SELECT * FROM integration_logs ORDER BY id DESC LIMIT %s", (limit,)
    ).fetchall()


def schema_versions(conn):
    """Return the applied schema versions (migration bookkeeping)."""
    return conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()


# --------------------------------------------------------------------------- #
# Foreign-key coercion helpers
# --------------------------------------------------------------------------- #
def _resolve_by_ref(conn, table, ref):
    if not ref:
        return None
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        row = conn.execute("SELECT id FROM %s WHERE %s_ref = %%s" % (table, table[:-1]), (ref,)).fetchone()
        if row is not None:
            return row["id"]
        try:
            return int(ref)
        except ValueError:
            return None
    return None


def _resolve_customer_id(conn, val):
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        row = conn.execute("SELECT id FROM customers WHERE customer_id = %s", (val,)).fetchone()
        if row is not None:
            return row["id"]
        try:
            return int(val)
        except ValueError:
            return None
    return None


def resolve_ticket_id(conn, val):
    return _resolve_by_ref(conn, "tickets", val)


def resolve_quote_id(conn, val):
    return _resolve_by_ref(conn, "quotes", val)


def resolve_invoice_id(conn, val):
    return _resolve_by_ref(conn, "invoices", val)


def resolve_appt_id(conn, val):
    return _resolve_by_ref(conn, "appointments", val)


def resolve_enquiry_id(conn, val):
    return _resolve_by_ref(conn, "enquiries", val)


def resolve_device_id(conn, val):
    """Devices are referenced by integer id in service records."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return None
    return None


def _append_value(fk_cols, monetary_cols, k, v):
    """Normalise a field value for INSERT binding."""
    if k in fk_cols and v is None:
        return None
    if k in monetary_cols and v is not None:
        return v
    return None if v is None else str(v)
