"""SCT Customer & Service Management System - HTTP application server.

Zero external dependencies (stdlib only). Routing is decorator-driven, templates
use Jinja2, persistence uses MySQL (see ``db.py``).  The app boots with a
single-process, multi-threaded HTTP server; sessions are HMAC-signed cookies.

Run:
    python app.py
"""
import contextvars
import hashlib
import hmac
import json
import os
import re
import mimetypes
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email import policy
from email.parser import BytesParser

import jinja2

import auth
import config
import db as db_mod
from emailer import send_email

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE, "templates")
STATIC_DIR = os.path.join(BASE, "static")

env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_DIR), autoescape=True)
env.globals["brand"] = {
    "primary": "#20252B", "orange": "#D96C36", "amber": "#F2B544",
    "cream": "#F7F3EA", "green": "#718477",
}

STATUS_COLORS = {
    "new_enquiry": "bg-slate-400 text-white",
    "awaiting_assessment": "bg-blue-100 text-blue-800",
    "awaiting_quote": "bg-amber-100 text-amber-800",
    "awaiting_customer_approval": "bg-purple-100 text-purple-800",
    "approved": "bg-green-100 text-green-800",
    "scheduled": "bg-blue-200 text-blue-800",
    "in_progress": "bg-amber-100 text-amber-800",
    "awaiting_parts": "bg-orange-100 text-orange-800",
    "awaiting_customer": "bg-yellow-100 text-yellow-800",
    "completed": "bg-emerald-100 text-emerald-800",
    "ready_for_collection": "bg-teal-100 text-teal-800",
    "closed": "bg-slate-400 text-white",
    "cancelled": "bg-red-100 text-red-800",
}
PAGINATE = 12
PRIORITY_COLORS = {
    "low": "bg-emerald-100 text-emerald-800",
    "medium": "bg-blue-100 text-blue-800",
    "high": "bg-orange-100 text-orange-800",
    "urgent": "bg-red-100 text-red-800",
}
STATUS_LABELS = {
    "new_enquiry": "New enquiry", "awaiting_assessment": "Awaiting assessment",
    "awaiting_quote": "Awaiting quote", "awaiting_customer_approval": "Awaiting customer approval",
    "approved": "Approved", "scheduled": "Scheduled", "in_progress": "In progress",
    "awaiting_parts": "Awaiting parts", "awaiting_customer": "Awaiting customer",
    "completed": "Completed", "ready_for_collection": "Ready for collection",
    "closed": "Closed", "cancelled": "Cancelled",
}

g = {}
_request_ctx = contextvars.ContextVar("request", default=None)


# --------------------------------------------------------------------------- #
# Request wrapper
# --------------------------------------------------------------------------- #
class Request:
    """Thin, thread-safe view over the raw ``BaseHTTPRequestHandler``."""

    def __init__(self, handler):
        self.handler = handler
        self.method = handler.command
        self.path = handler.path
        self.headers = handler.headers
        self._form = None
        qs = self.path.split("?", 1)
        self.query = urllib.parse.parse_qs(qs[1] if len(qs) > 1 else "", keep_blank_values=True)

    # -- bodies ------------------------------------------------------------ #
    def _read_bytes(self):
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            n = int(length)
        except (TypeError, ValueError):
            return b""
        return self.handler.rfile.read(n)

    def _read_text(self):
        return self._read_bytes().decode("utf-8", errors="replace")

    @property
    def json(self):
        if self._json is None:
            ctype = self.headers.get("Content-Type", "")
            if "application/json" in ctype:
                try:
                    self._json = json.loads(self._read_text())
                except Exception:
                    self._json = {}
            else:
                self._json = {}
        return self._json

    def _parse_urlencoded(self):
        body = self._read_text()
        return urllib.parse.parse_qs(body, keep_blank_values=True)

    def _parse_multipart(self):
        ctype = self.headers.get("Content-Type", "")
        boundary = None
        if "boundary=" in ctype:
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
        if not boundary:
            return self._parse_urlencoded()
        raw = self._read_bytes()
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        fields, files = {}, []
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            fn = part.get_filename()
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if fn:
                files.append({"name": name, "filename": fn, "content": payload or b"",
                              "ctype": ctype})
            elif payload is not None:
                fields.setdefault(name, []).append(payload.decode("utf-8", errors="replace"))
        return {"field": fields, "file": files}

    @property
    def form(self):
        if self._form is None:
            ctype = self.headers.get("Content-Type", "")
            self._form = self._parse_multipart() if "multipart/form-data" in ctype else self._parse_urlencoded()
        return self._form


# --------------------------------------------------------------------------- #
# Session / template helpers
# --------------------------------------------------------------------------- #
def current_request():
    return _request_ctx.get()


def get_session():
    token = request_cookies().get("sct_session")
    return auth.get_session_user(token)


def request_cookies():
    req = current_request()
    raw = req.headers.get("Cookie", "") if req else ""
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k] = v
    return out


def set_g_user(user):
    g["user"] = user


def flash(category, message):
    g.setdefault("flash", []).append({"category": category, "message": message})


def get_flashed():
    out = g.get("flash", [])
    g["flash"] = []
    return out


def redirect(url):
    return {"__redirect__": url}


def url_for(name, **params):
    if name == "static" and "filename" in params:
        return f"/static/{params['filename']}"
    return f"/{name}" + ("/" + "/".join(f"{k}/{v}" for k, v in params.items()) if params else "")


def escape(s):
    if s is None:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def render(template, **ctx):
    t = env.get_template(template)
    return t.render(
        ctx,
        g=g,
        escape=escape,
        url_for=url_for,
        STATUS_COLORS=STATUS_COLORS,
        PRIORITY_COLORS=PRIORITY_COLORS,
        STATUS_LABELS=STATUS_LABELS,
        config=config,
        datetime=datetime,
        get_flashed=get_flashed,
        flash=flash,
        flash_list=g.get("flash", []),
    )


def require(*roles):
    def deco(handler):
        def wrapper(req, **params):
            user = get_session()
            if user is None or user["role"] not in roles:
                nxt = urllib.parse.quote(req.path) if req else ""
                return redirect("/login" + (f"?next={nxt}" if nxt else ""))
            set_g_user(user)
            return handler(req, **params)

        return wrapper

    return deco


def admin_required(handler):
    return require("admin")(handler)


def login_required(handler):
    def wrapper(req, **params):
        user = get_session()
        if user is None:
            nxt = urllib.parse.quote(req.path) if req else ""
            return redirect("/login" + (f"?next={nxt}" if nxt else ""))
        set_g_user(user)
        return handler(req, **params)

    return wrapper


def render_result(result):
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "__redirect__" in result:
        return result
    ctx = dict(result)
    template = ctx.pop("template", "base.html")
    return render(template, **ctx)


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
class Router:
    def __init__(self):
        self.routes = []

    def route(self, path, methods=None):
        methods = methods or ["GET"]

        def deco(fn):
            self.routes.append((path, methods, fn))
            return fn

        return deco

    def match(self, path, method):
        for route_path, methods, fn in self.routes:
            if method in methods:
                m = re.match(re_path(route_path), path)
                if m:
                    return fn, m.groupdict()
        return None, None


def re_path(p):
    def _replace(m):
        param = m.group(1)
        if ":" in param:
            ctype, name = param.split(":", 1)
        else:
            ctype, name = None, param
        if ctype == "path":
            return f"(?P<{name}>.+?)"
        return f"(?P<{name}>[^/]+?)"
    return re.sub(r"<([^>]+)>", _replace, p) + "$"


re_compile = re.compile


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class SCTHandler(BaseHTTPRequestHandler):
    server_version = "SCTApp/1.0"

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        req = Request(self)
        _request_ctx.set(req)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        handler, params = ROUTER.match(path, method)
        if handler is None:
            self.send_json(404, {"error": "Not found"})
            return
        try:
            result = handler(req, **params)
        except Exception as exc:  # noqa: BLE001
            self.send_json(500, {"error": "Internal error", "details": str(exc)})
            return
        if isinstance(result, dict):
            cookie = result.get("__cookie__")
            if "__redirect__" in result:
                self.send_response(302)
                if cookie:
                    self.send_session_cookie(cookie)
                self.send_header("Location", result["__redirect__"])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if "__json__" in result:
                self.send_json(200, result["__json__"])
                return
        self.send_html(render_result(result))

    def send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def send_json(self, status, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_session_cookie(self, token):
        self.send_header("Set-Cookie", f"sct_session={token}; Path=/; HttpOnly; SameSite=Lax")

    def log_message(self, fmt, *args):  # quieter logging
        pass


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
ROUTER = Router()
@ROUTER.route("/static/<path:filename>", ["GET"])
def static_file(req, filename):
    """Serve static assets with the correct MIME type (CSS/JS) so browsers do not block them."""
    static_dir = STATIC_DIR
    safe_path = os.path.realpath(os.path.join(static_dir, filename))
    if not safe_path.startswith(static_dir):
        return {"error": "Not found"}, 404
    if not os.path.isfile(safe_path):
        return {"error": "Not found"}, 404
    ext = os.path.splitext(safe_path)[1].lower()
    mime = mimetypes.guess_type(safe_path)[0] or "application/octet-stream"
    with open(safe_path, "rb") as f:
        data = f.read()
    req.send_response(200)
    req.send_header("Content-Type", mime)
    req.send_header("Content-Length", len(data))
    req.end_headers()
    req.wfile.write(data)
    return None



@ROUTER.route("/login", ["GET", "POST"])
def login(req):
    if get_session():
        return redirect("/")
    if req.method == "POST":
        email = (req.form.get("email") or [""])[0].strip().lower()
        password = (req.form.get("password") or [""])[0]
        conn = db_mod.get_connection()
        try:
            row = db_mod.user_by_email(conn, email)
            if row and auth.verify_password(password, row["password_hash"]) and row["is_active"]:
                token = auth.create_session(row["id"], row["role"], row["name"])
                auth.seed_local_users(conn)
                return {"__redirect__": "/", "__cookie__": token}
            flash("error", "Invalid email or password.")
        finally:
            conn.close()
        return {"__redirect__": "/login"}
    return {"template": "auth/login.html"}


@ROUTER.route("/logout", ["POST"])
def logout(req):
    token = request_cookies().get("sct_session")
    auth.delete_session(token)
    return {"__redirect__": "/login"}


@ROUTER.route("/", ["GET"])
@login_required
def dashboard(req):
    conn = db_mod.get_connection()
    try:
        filters = {
            "search": (req.query.get("search") or [""])[0],
            "status": (req.query.get("status") or [""])[0],
            "customer_id": (req.query.get("customer_id") or [""])[0],
            "technician": (req.query.get("technician") or [""])[0],
            "from": (req.query.get("from") or [""])[0],
            "to": (req.query.get("to") or [""])[0],
        }
        q = req.query.get("q") or [""][0]
        # KPIs
        kpi = {
            "new_enquiries": db_mod.count_tickets(conn, status="new_enquiry"),
            "open_tickets": db_mod.count_tickets(conn, status=None),
            "awaiting_approval": db_mod.count_tickets(conn, status="awaiting_customer_approval"),
            "upcoming_appointments": len(db_mod.upcoming_appointments(conn)),
            "in_progress": db_mod.count_tickets(conn, status="in_progress"),
            "awaiting_parts": db_mod.count_tickets(conn, status="awaiting_parts"),
            "completed_pending_invoice": db_mod.count_tickets(conn, status="completed"),
            "outstanding_invoices": len([i for i in db_mod.invoices_list(conn) if i["payment_status"] != "paid"]),
            "recent_services": len(db_mod.service_records(conn, limit=5)),
        }
        customers = db_mod.customers_list(conn, search=filters["search"], limit=10)
        tickets = db_mod.tickets_list(conn, search=q, limit=8)
        users = conn.execute("SELECT id, name, role FROM users WHERE is_active=1 ORDER BY role, name").fetchall()
    finally:
        conn.close()
    return {"template": "dashboard.html", "kpi": kpi, "filters": filters,
            "customers": customers, "tickets": tickets, "users": users}


# ============================= CUSTOMERS ==================================== #
@ROUTER.route("/customers", ["GET"])
@login_required
def customers_list(req):
    conn = db_mod.get_connection()
    try:
        search = (req.query.get("search") or [""])[0]
        status = (req.query.get("status") or [""])[0]
        rows = db_mod.customers_list(conn, search=search, status=status, limit=PAGINATE)
        total = db_mod.count_customers(conn, status=status or None)
    finally:
        conn.close()
    return {"template": "customers/list.html", "customers": rows, "search": search,
            "status": status, "total": total}


@ROUTER.route("/customers/new", ["GET", "POST"])
@login_required
def customer_new(req):
    if req.method == "POST":
        conn = db_mod.get_connection()
        try:
            fields = {
                "first_name": (req.form.get("first_name") or [""])[0],
                "last_name": (req.form.get("last_name") or [""])[0],
                "email": (req.form.get("email") or [""])[0],
                "phone_primary": (req.form.get("phone_primary") or [""])[0],
                "phone_alternative": (req.form.get("phone_alternative") or [""])[0],
                "customer_type": (req.form.get("customer_type") or "individual"),
                "billing_address": (req.form.get("billing_address") or "") or None,
                "service_address": (req.form.get("service_address") or "") or None,
                "preferred_contact": (req.form.get("preferred_contact") or "") or None,
                "notes": (req.form.get("notes") or "") or None,
                "status": "active",
            }
            if not fields["first_name"] or not fields["last_name"] or not fields["email"]:
                flash("error", "First name, last name and email are required.")
                return {"template": "customers/form.html", "form": {**fields, "customer_id": (req.form.get("customer_id") or [""])[0]}}
            dup = db_mod.count_duplicates(conn, fields["first_name"], fields["last_name"],
                                          fields["email"], fields["phone_primary"])
            if dup > 0:
                flash("error", f"Near-duplicate customer record(s) exist ({dup}). Do not create anew.")
                return {"template": "customers/form.html", "form": {**fields, "customer_id": (req.form.get("customer_id") or [""])[0]}}
            cid = db_mod.save_customer(conn, fields)
            flash("success", "Customer created.")
            return redirect("/customers")
        finally:
            conn.close()
    # GET: show the form (prefilled for editing when customer_id is supplied)
    customer_id = (req.query.get("customer_id") or [""])[0]
    form_fields = {
        "customer_id": customer_id,
        "first_name": "", "last_name": "", "business_name": "", "email": "",
        "phone_primary": "", "phone_alternative": "", "customer_type": "individual",
        "billing_address": "", "service_address": "", "preferred_contact": "",
        "notes": "",
    }
    if customer_id:
        conn = db_mod.get_connection()
        try:
            c = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if c:
                for k in form_fields:
                    if k != "customer_id":
                        form_fields[k] = c[k] if c[k] is not None else ""
        finally:
            conn.close()
    return {"template": "customers/form.html", "form": form_fields}


@ROUTER.route("/customers/<int:cid>", ["GET"])
@login_required
def customer_detail(req, cid):
    conn = db_mod.get_connection()
    try:
        c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
        if not c:
            return {"__redirect__": "/customers"}
        tickets = db_mod.tickets_list(conn, customer_id=cid)
        appointments = db_mod.appointments_list(conn, customer_id=cid)
        quotes = db_mod.quotes_list(conn, customer_id=cid)
        invoices = db_mod.invoices_list(conn, customer_id=cid)
        services = db_mod.service_records(conn, customer_id=cid, limit=50)
        notes = db_mod.work_notes(conn, customer_id=cid)
    finally:
        conn.close()
    return {"template": "customers/detail.html", "c": c, "tickets": tickets,
            "appointments": appointments, "quotes": quotes, "invoices": invoices,
            "services": services, "notes": notes}


# ============================== TICKETS ===================================== #
@ROUTER.route("/tickets", ["GET"])
@login_required
def tickets_list(req):
    conn = db_mod.get_connection()
    try:
        search = (req.query.get("search") or [""])[0]
        status = (req.query.get("status") or [""])[0]
        rows = db_mod.tickets_list(conn, search=search, status=status, limit=PAGINATE)
    finally:
        conn.close()
    return {"template": "tickets/list.html", "tickets": rows, "search": search, "status": status}


@ROUTER.route("/tickets/new", ["GET", "POST"])
@login_required
def ticket_new(req):
    if req.method == "POST":
        conn = db_mod.get_connection()
        try:
            fields = {
                "title": (req.form.get("title") or "") or None,
                "description": (req.form.get("description") or "") or None,
                "device_make": (req.form.get("device_make") or "") or None,
                "device_model": (req.form.get("device_model") or "") or None,
                "serial_imei": (req.form.get("serial_imei") or "") or None,
                "symptoms": (req.form.get("symptoms") or "") or None,
                "diagnosis": (req.form.get("diagnosis") or "") or None,
                "priority": (req.form.get("priority") or "medium"),
                "status": "new_enquiry",
                "customer_id": (req.form.get("customer_id") or "") or None,
                "enquiry_id": None,
            }
            if not fields["title"]:
                flash("error", "Ticket title is required.")
                return {"template": "tickets/form.html", "form": fields}
            cid = db_mod.save_ticket(conn, fields)
            flash("success", f"Ticket {fields['priority'].upper()} created.")
            return redirect("/tickets")
        finally:
            conn.close()
    # GET: show the form
    conn = db_mod.get_connection()
    try:
        customers = conn.execute("SELECT * FROM customers ORDER BY last_name").fetchall()
        enquiries = conn.execute("SELECT * FROM enquiries ORDER BY received_at DESC").fetchall()
    finally:
        conn.close()
    return {"template": "tickets/form.html", "form": form, "customers": customers, "enquiries": enquiries}


@ROUTER.route("/tickets/<int:tid>", ["GET"])
@login_required
def ticket_detail(req, tid):
    conn = db_mod.get_connection()
    try:
        t = conn.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
        if not t:
            return {"__redirect__": "/tickets"}
        hist = db_mod.ticket_status_history(conn, tid)
        internal = db_mod.ticket_internal_notes(conn, tid)
        customer_notes = db_mod.ticket_customer_notes(conn, tid)
        files = db_mod.file_uploads_by_ref(conn, "ticket", tid)
        customers = [dict(r) for r in conn.execute("SELECT * FROM customers ORDER BY last_name").fetchall()]
    finally:
        conn.close()
    return {"template": "tickets/detail.html", "t": t, "histories": hist,
            "internal_notes": internal, "customer_notes": customer_notes, "files": files,
            "customers": customers}


@ROUTER.route("/tickets/<int:tid>/status", ["POST"])
@login_required
def ticket_status(req, tid):
    conn = db_mod.get_connection()
    try:
        t = conn.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
        if t:
            new_status = req.form.get("status") or t["status"]
            if new_status != t["status"]:
                db_mod.update_ticket_status(conn, tid, new_status)
        flash("success", "Ticket status updated.")
    finally:
        conn.close()
    return redirect(f"/tickets/{tid}")


@ROUTER.route("/tickets/<int:tid>/internal-note", ["POST"])
@login_required
def ticket_internal_note(req, tid):
    conn = db_mod.get_connection()
    try:
        form = req.form.get("note") or [""][0]
        if form.strip():
            db_mod.save_ticket_internal_note(conn, tid, get_g_user()["id"], form.strip())
            flash("success", "Internal note added.")
        else:
            flash("error", "Note cannot be empty.")
    finally:
        conn.close()
    return redirect(f"/tickets/{tid}")


@ROUTER.route("/tickets/<int:tid>/customer-note", ["POST"])
@login_required
def ticket_customer_note(req, tid):
    conn = db_mod.get_connection()
    try:
        form = req.form.get("note") or [""][0]
        if form.strip():
            db_mod.save_ticket_customer_note(conn, tid, get_g_user()["id"], form.strip())
            flash("success", "Customer update added.")
        else:
            flash("error", "Note cannot be empty.")
    finally:
        conn.close()
    return redirect(f"/tickets/{tid}")


@ROUTER.route("/tickets/<int:tid>/assign", ["POST"])
@login_required
def ticket_assign(req, tid):
    conn = db_mod.get_connection()
    try:
        user_id = int(req.form.get("assigned_to") or 0)
        if user_id:
            db_mod.update_ticket_assigned_to(conn, tid, user_id)
        flash("success", "Ticket assigned.")
    finally:
        conn.close()
    return redirect(f"/tickets/{tid}")


# =========================== APPOINTMENTS =================================== #
@ROUTER.route("/appointments", ["GET"])
@login_required
def appointments_list(req):
    conn = db_mod.get_connection()
    try:
        search = (req.query.get("search") or [""])[0]
        rows = db_mod.appointments_list(conn, search=search, limit=PAGINATE)
    finally:
        conn.close()
    return {"template": "appointments/list.html", "appointments": rows, "search": search}


@ROUTER.route("/appointments/calendar", ["GET"])
@login_required
def calendar(req):
    return {"template": "appointments/calendar.html"}


# ============================== QUOTES ====================================== #
@ROUTER.route("/quotes", ["GET"])
@login_required
def quotes_list(req):
    conn = db_mod.get_connection()
    try:
        search = (req.query.get("search") or [""])[0]
        rows = db_mod.quotes_list(conn, search=search, limit=PAGINATE)
    finally:
        conn.close()
    return {"template": "quotes/list.html", "quotes": rows, "search": search}


@ROUTER.route("/quotes/new", ["GET", "POST"])
@login_required
def quote_new(req):
    if req.method == "POST":
        conn = db_mod.get_connection()
        try:
            fields = {
                "ticket_id": (req.form.get("ticket_id") or "") or None,
                "issued_to_email": (req.form.get("issued_to_email") or "") or None,
                "terms": (req.form.get("terms") or "") or None,
            }
            qty = int(req.form.get("qty") or [1])[0]
            unit = float(req.form.get("unit") or [0])[0]
            desc = req.form.get("desc") or [""][0]
            if qty and unit and desc:
                qid = db_mod.save_quote(conn, {"issued_to_email": fields["issued_to_email"]})
                db_mod.save_quote_line_item(conn, qid, desc, int(qty), unit, qty * unit)
                flash("success", "Line item added to quote.")
            return redirect("/quotes/new")
        finally:
            conn.close()
    return {"template": "quotes/form.html"}


@ROUTER.route("/quotes/<int:qid>", ["GET"])
@login_required
def quote_detail(req, qid):
    conn = db_mod.get_connection()
    try:
        q = conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone()
        if not q:
            return {"__redirect__": "/quotes"}
        items = db_mod.quote_line_items(conn, qid)
        via = db_mod.ticket_by_id(conn, q["ticket_id"]) if q["ticket_id"] else None
    finally:
        conn.close()
    return {"template": "quotes/detail.html", "q": q, "items": items, "via": via}


@ROUTER.route("/quotes/<int:qid>/approve", ["GET"])
@login_required
def quote_approve(req, qid):
    conn = db_mod.get_connection()
    try:
        q = conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone()
        if not q:
            return {"__redirect__": "/quotes"}
        items = db_mod.quote_line_items(conn, qid)
    finally:
        conn.close()
    return {"template": "quotes/approve.html", "q": q, "items": items,
            "approval_token": secrets.token_urlsafe(16)}


@ROUTER.route("/quotes/<int:qid>/approve", ["POST"])
@login_required
def quote_record_approval(req, qid):
    conn = db_mod.get_connection()
    try:
        q = conn.execute("SELECT * FROM quotes WHERE id = ?", (qid,)).fetchone()
        if q:
            db_mod.save_quote_approval(conn, qid, req.form.get("approver_name") or "",
                                       req.form.get("approver_email") or "",
                                       req.form.get("comment") or "")
            db_mod.update_quote_status(conn, qid, req.form.get("decision") or "approved")
        flash("success", "Quote decision recorded.")
    finally:
        conn.close()
    return redirect("/quotes")


# ============================== INVOICES ==================================== #
@ROUTER.route("/invoices", ["GET"])
@login_required
def invoices_list(req):
    conn = db_mod.get_connection()
    try:
        search = (req.query.get("search") or [""])[0]
        rows = db_mod.invoices_list(conn, search=search, limit=PAGINATE)
    finally:
        conn.close()
    return {"template": "invoices/list.html", "invoices": rows, "search": search}


@ROUTER.route("/invoices/new", ["GET", "POST"])
@login_required
def invoice_new(req):
    if req.method == "POST":
        conn = db_mod.get_connection()
        try:
            fields = {
                "customer_id": (req.form.get("customer_id") or "") or None,
                "ticket_id": (req.form.get("ticket_id") or "") or None,
                "qty": int(req.form.get("qty") or [1])[0],
                "unit": float(req.form.get("unit") or [0])[0],
                "desc": req.form.get("desc") or [""][0],
            }
            if fields["customer_id"] and fields["desc"]:
                qid = db_mod.save_invoice(conn, {"customer_id": fields["customer_id"],
                                                 "status": "sent", "payment_status": "pending",
                                                 "issue_date": now().split("T")[0]})
                db_mod.save_invoice_line_item(conn, qid, fields["desc"],
                                              fields["qty"], fields["unit"],
                                              fields["qty"] * fields["unit"])
                flash("success", f"Invoice {qid} prepared (manual entry).")
            return redirect("/invoices/new")
        finally:
            conn.close()
    return {"template": "invoices/form.html"}


@ROUTER.route("/invoices/<int:iid>", ["GET"])
@login_required
def invoice_detail(req, iid):
    conn = db_mod.get_connection()
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            return {"__redirect__": "/invoices"}
        items = db_mod.invoice_line_items(conn, iid)
    finally:
        conn.close()
    return {"template": "invoices/detail.html", "inv": inv, "items": items,
            "download": url_for("invoice_pdf", iid=iid)}


@ROUTER.route("/invoices/<int:iid>/sync-hnry", ["POST"])
@login_required
def invoice_sync_hnry(req, iid):
    conn = db_mod.get_connection()
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            return {"__redirect__": "/invoices"}
        result = sync_quote_to_hnry(conn, iid)
        flash("info", f"Hnry sync: {result.get('status', 'error')}")
    finally:
        conn.close()
    return redirect("/invoices")


@ROUTER.route("/invoices/<int:iid>/mark-paid", ["POST"])
@login_required
def invoice_mark_paid(req, iid):
    conn = db_mod.get_connection()
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            return {"__redirect__": "/invoices"}
        if inv["payment_status"] == "paid":
            flash("info", "Invoice already marked paid.")
            return redirect("/invoices")
        db_mod.save_invoice(conn, {
            "id": iid, "status": "sent",
            "payment_status": "paid", "payment_date": now().split("T")[0],
        })
        flash("success", "Invoice marked paid (confirmed).")
    finally:
        conn.close()
    return redirect("/invoices")


@ROUTER.route("/invoices/<int:iid>/pdf", ["GET"])
@login_required
def invoice_pdf(req, iid):
    import io
    conn = db_mod.get_connection()
    try:
        inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (iid,)).fetchone()
        if not inv:
            return {"__redirect__": "/invoices"}
        items = conn.execute(
            "SELECT description, quantity, unit_price, amount FROM invoice_line_items WHERE invoice_id = ? ORDER BY id",
            (iid,),).fetchall()
        header = f"SCT Sun Country Tech\nInvoice {inv['invoice_ref']}\nIssue date: {inv['issue_date']}\n"
        lines = [header]
        for it in items:
            lines.append(f"  {it['description']} x{it['quantity']} @ {float(it['unit_price']):.2f}")
        lines += [
            f"Subtotal: {float(inv['subtotal'] or 0):.2f}",
            f"GST (10%): {float(inv['gst_amount'] or 0):.2f}",
            f"TOTAL: {float(inv['total'] or 0):.2f}",
        ]
        body = "\n".join(lines)
        pdf = _render_pdf(header, body, f"{inv['invoice_ref']}_invoice.pdf")
    finally:
        conn.close()
    if not pdf:
        return {"__redirect__": "/invoices"}
    return {
        "__json__": {
            "pdf": pdf.decode("latin-1"),
            "headers": {
                "Content-Type": "application/pdf",
                "Content-Disposition": f"attachment; filename={inv['invoice_ref']}_invoice.pdf",
            },
        }
    }


# ============================== SERVICE ===================================== #
@ROUTER.route("/customers/<int:cid>/service", ["GET", "POST"])
@login_required
def service_records(req, cid):
    conn = db_mod.get_connection()
    try:
        c = conn.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
        if not c:
            return {"__redirect__": "/customers"}
        if req.method == "POST":
            db_mod.save_service_record(conn, {
                "customer_id": c["id"], "service_date": now().split("T")[0],
                "issue": (req.form.get("issue") or "") or None,
                "diagnostics": (req.form.get("diagnostics") or "") or None,
                "work_performed": (req.form.get("work_performed") or "") or None,
                "parts_used": (req.form.get("parts_used") or "") or None,
                "labour_hours": float(req.form.get("labour_hours") or [0])[0],
                "technician_notes": (req.form.get("technician_notes") or "") or None,
                "photos": (req.form.get("photos") or "") or None,
                "outcome": (req.form.get("outcome") or "") or None,
                "warranty": (req.form.get("warranty") or "") or None,
            })
            flash("success", "Service record created.")
            return redirect(f"/customers/{cid}/service")
        rows = db_mod.service_records(conn, customer_id=cid)
    finally:
        conn.close()
    return {"template": "service/records.html", "customer": c, "records": rows}


@ROUTER.route("/service-records/<int:sid>/report", ["GET"])
@login_required
def service_report(req, sid):
    conn = db_mod.get_connection()
    try:
        r = conn.execute("SELECT * FROM service_records WHERE id = ?", (sid,)).fetchone()
        if not r:
            return {"__redirect__": "/customers"}
        pdf = build_service_pdf(conn, r)
    finally:
        conn.close()
    if not pdf:
        return {"__redirect__": "/customers"}
    return {"__json__": {"pdf": pdf.decode("latin-1")}}


# ============================== USERS (admin) ================================ #
@ROUTER.route("/admin/users", ["GET", "POST"])
@login_required
@admin_required
def admin_users(req):
    if req.method == "POST":
        conn = db_mod.get_connection()
        try:
            db_mod.create_user(conn, req.form.get("email") or [""][0],
                               auth.hash_password(req.form.get("password") or [""][0]),
                               req.form.get("name") or [""][0],
                               req.form.get("role") or "viewer")
            flash("success", "User created.")
        finally:
            conn.close()
        return redirect("/admin/users")
    conn = db_mod.get_connection()
    try:
        rows = conn.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    finally:
        conn.close()
    return {"template": "admin/users.html", "users": rows}


# ============================== AUDIT LOG =================================== #
@ROUTER.route("/audit", ["GET"])
@login_required
@admin_required
def audit(req):
    conn = db_mod.get_connection()
    try:
        rows = db_mod.audit_logs(conn, limit=200)
    finally:
        conn.close()
    return {"template": "audit.html", "logs": rows}


# ============================== FILE UPLOAD ================================ #
@ROUTER.route("/upload", ["POST"])
@login_required
def upload(req):
    files = req.form.get("file", [])
    if not isinstance(files, list):
        files = [files]
    uploaded = []
    for f in files:
        if not f.get("filename"):
            continue
        data = f.get("content") or b""
        ext = os.path.splitext(f.get("filename"))[1].lower()
        if ext not in config.ALLOWED_UPLOAD_EXT:
            flash("error", f"File type .{ext} not allowed.")
            continue
        if len(data) > config.MAX_CONTENT_LENGTH:
            flash("error", "File too large.")
            continue
        stored = secrets.token_hex(16) + ext
        with open(os.path.join(config.UPLOAD_DIR, stored), "wb") as fh:
            fh.write(data)
        conn = db_mod.get_connection()
        try:
            db_mod.save_upload(conn, f.get("filename"), stored, f.get("ctype"), len(data),
                               get_g_user()["id"], "ticket", 0)
        finally:
            conn.close()
        uploaded.append(f.get("filename"))
    if uploaded:
        flash("success", f"Uploaded: {', '.join(uploaded)}")
    return redirect("/tickets")


# --------------------------------------------------------------------------- #
# PDF generation helpers
# --------------------------------------------------------------------------- #
def _wrap(text, maxw):
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if _approx(trial) <= maxw:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _approx(s):
    return len(s) * 0.52 + 2


def _escape(s):
    return (s.replace("\\", "\\5c").replace("(", "\\28").replace(")", "\\29")
            .replace("\n", "\\n"))


def _render_pdf_text(header, body, width, top, right, left):
    lines = _wrap(body, int((right - left) / 0.52))
    start_y = top - 14 - (len(lines) - 1) * 14
    stream = f"BT /F1 12 Tf 1 0 0 1 {left} {start_y} Td\n({_escape(header)}) Tj\n"
    for ln in lines:
        stream += f"0 -14 0 1 Tm ({_escape(ln)}) Tj\n"
    stream += "ET"
    return stream


def build_service_pdf(conn, rec):
    header = (f"SCT Sun Country Tech - Service Completion Report\n"
              f"Customer: {rec['customer_id']} | Ticket: {rec['ticket_id'] or '-'}\n"
              f"Device: {rec['device_id'] or '-'} | Service date: {rec['service_date']}")
    body = f"Issue: {rec['issue']}\n"
    if rec["diagnostics"]:
        body += f"Diagnostics: {rec['diagnostics']}\n"
    body += f"Work performed: {rec['work_performed']}\n"
    if rec["parts_used"]:
        body += f"Parts: {rec['parts_used']}\n"
    if rec["technician_notes"]:
        body += f"Notes: {rec['technician_notes']}\n"
    if rec["outcome"]:
        body += f"Outcome: {rec['outcome']}\n"
    if rec["warranty"]:
        body += f"Warranty: {rec['warranty']}\n"
    filename = f"service-report-{rec['id']}.pdf"
    try:
        return _render_pdf(header, body, filename)
    except Exception:
        return None


def _quote_pdf(conn, inv):
    items = conn.execute(
        "SELECT description, quantity, unit_price, amount FROM invoice_line_items WHERE invoice_id = ? ORDER BY id",
        (inv["id"],),
    ).fetchall()
    header = f"SCT Sun Country Tech\nInvoice {inv['invoice_ref']}\nIssue date: {inv['issue_date']}\n"
    lines = [header]
    for it in items:
        lines.append(f"  {it['description']} x{it['quantity']} @ {float(it['unit_price']):.2f}")
    lines += [
        f"Subtotal: {float(inv['subtotal'] or 0):.2f}",
        f"GST (10%): {float(inv['gst_amount'] or 0):.2f}",
        f"TOTAL: {float(inv['total'] or 0):.2f}",
    ]
    body = "\n".join(lines)
    filename = f"{inv['invoice_ref']}_invoice.pdf"
    try:
        return _render_pdf(header, body, filename)
    except Exception:
        return None


def _render_pdf(header, body, filename):
    import io

    W, H = 595.0, 842.0
    left, right, top, bottom = 40.0, W - 40.0, 50.0, H - 50.0
    stream = _render_pdf_text(header, body, right, top, left)
    objects = [
        f"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        f"<< /Type /Font /Subtype /Helvetica /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} 0 R >>",
    ]
    buf = io.BytesIO()
    b = buf.write
    b(b"%PDF-1.4\n")

    def emit(obj_no, raw):
        nonlocal b
        b(f"{obj_no} 0 obj\n{raw}\nendobj\n")

    emit(1, objects[0])
    emit(2, objects[1])
    emit(3, objects[2])
    emit(4, objects[3])
    objects[4] = f"<< /Length {len(stream)} 0 R >>"
    emit(5, f"<< /Length {len(stream)} 0 R >>\nstream\n{stream}\nendstream")

    xref = len(objects) + 1
    b(f"xref\n0 {xref}\n0000000000 65535 f \n")
    for i, o in enumerate(objects, start=1):
        b(f"{b(o, 10)} {i} 0000000000 z \n")
    b(f"trailer\n<< /Size {xref} /Root {len(objects)} 0 R >>\nstartxref\n{xref}\n%%%%EOF")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    db_mod.apply_migrations()
    auth.seed_local_users(db_mod.get_connection())
    port = int(os.environ.get("SCT_PORT", "8080"))
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("0.0.0.0", port), SCTHandler)
    print(f"SCT app listening on http://0.0.0.0:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
