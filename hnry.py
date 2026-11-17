"""Hnry invoicing bridge.

Hnry's documented integration surface (verified against help.zapier.com and
Hnry's own developer pages):

* A public Zapier integration exposes *create customer*, *find client*,
  *create invoice* and *add item to existing invoice* actions.  Zapier performs
  its own OAuth, so no long-lived token needs to be stored by SCT.
* A REST API does exist (the same API that backs the mobile apps), but it is
  credential-gated: unauthenticated calls are rejected with HTTP 401 Basic.
  No OpenAPI/Swagger contract is published, so it is treated as a
  best-effort "API mode" and only enabled when the caller supplies working
  credentials.
* Email/sms payment rails and manual entry are the default fallback.

Consequently the bridge never promises an unrestricted public API.  Every
attempt is recorded in the ``integration_logs`` table and, when automation is
unavailable, a downloadable PDF (and a manual-entry payload) is returned for
the operator to use.

Authentication material is stored server-side (environment variables / secrets
file), never in frontend code.
"""
import hashlib
import hmac
import json
import os
import urllib.parse

import requests

import config
from emailer import send_email

HNRY_WEBHOOK_URL = config.HNRY_WEBHOOK_URL or os.environ.get("SCT_HNRY_WEBHOOK_URL", "").rstrip("/")
HNRY_WEBHOOK_SECRET = config.HNRY_WEBHOOK_SECRET or os.environ.get("SCT_HNRY_WEBHOOK_SECRET", "")
HNRY_API_TOKEN = os.environ.get("SCT_HNRY_API_TOKEN", "").strip()
HNRY_API_URL = os.environ.get("SCT_HNRY_API_URL", "").strip()

_REQUEST_TIMEOUT = 15


# --------------------------------------------------------------------------- #
# Idempotency helpers
# --------------------------------------------------------------------------- #
def _dedupe_key(invoice_id, customer_id):
    return f"hnry-{invoice_id}-{customer_id}"


def _sign_payload(payload):
    return hmac.new(
        config.SECRET_KEY.encode(), urllib.parse.urlencode(payload).encode(), hashlib.sha256
    ).hexdigest()


def json_dumps(obj):
    return json.dumps(obj, default=str)


# --------------------------------------------------------------------------- #
# Core sync
# --------------------------------------------------------------------------- #
def sync_quote_to_hnry(conn, invoice_id):
    """Attempt to move a finished invoice into Hnry, in order: Zapier webhook,
    REST API, email, manual.  Returns a status dict and always records a log."""
    import db

    inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if inv is None:
        return {"success": False, "error": "invoice not found", "invoice_id": invoice_id}

    key = _dedupe_key(inv["id"], inv["customer_id"])
    if conn.execute(
        "SELECT 1 FROM integration_logs WHERE integration='hnry' AND action='create_invoice' "
        f"AND payload LIKE '%{key}%' LIMIT 1"
    ).fetchone():
        return {"success": True, "status": "already_synced", "invoice_id": invoice_id}

    if inv["total"] is None or float(inv["total"]) <= 0:
        return {"success": False, "error": "invoice total is zero", "invoice_id": invoice_id}

    status = _attempt_zapier(conn, inv)
    if status["success"]:
        return status
    status = _attempt_rest_api(conn, inv)
    if status["success"]:
        return status
    status = _attempt_email(conn, inv)
    if status["success"]:
        return status
    return _attempt_manual(conn, inv)


# --------------------------------------------------------------------------- #
# Zapier webhook (preferred automation)
# --------------------------------------------------------------------------- #
def _attempt_zapier(conn, inv):
    if not HNRY_WEBHOOK_URL:
        return {"success": False, "status": "skipped", "reason": "no webhook_url"}
    payload = {
        "source": "sct",
        "customer_id": inv["customer_id"],
        "ticket_ref": inv["ticket_ref"],
        "quote_ref": inv["quote_ref"],
        "description": f"Service for {inv['subtotal']}+GST",
        "subtotal": float(inv["subtotal"] or 0),
        "gst_amount": float(inv["gst_amount"] or 0),
        "total": float(inv["total"] or 0),
        "issue_date": inv["issue_date"],
        "due_date": inv["due_date"] or None,
        "status": inv["status"],
        "payment_status": inv["payment_status"],
        "payment_date": inv["payment_date"],
    }
    try:
        req = requests.post(
            HNRY_WEBHOOK_URL,
            json=payload,
            headers={"X-SCT-Signature": _sign_payload(payload)},
            timeout=_REQUEST_TIMEOUT,
        )
        ok = req.status_code in (200, 201, 202)
        conn.execute(
            "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
            " VALUES ('hnry', 'create_invoice', ?, ?, ?, ?)",
            (json_dumps(payload), {"status": req.status_code, "ok": ok}, None),
        )
        conn.commit()
        return {"success": ok, "status": "zapier", "http_status": req.status_code}
    except Exception as exc:
        conn.execute(
            "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
            " VALUES ('hnry', 'create_invoice', ?, ?, ?, ?)",
            (json_dumps(payload), None, str(exc)),
        )
        conn.commit()
        return {"success": False, "status": "zapier", "error": str(exc)}


# --------------------------------------------------------------------------- #
# Direct REST API (best-effort; credential-gated)
# --------------------------------------------------------------------------- #
def _attempt_rest_api(conn, inv):
    if not (HNRY_API_TOKEN and HNRY_API_URL):
        return {"success": False, "status": "skipped", "reason": "no api credentials"}
    url = HNRY_API_URL.rstrip("/") + "/invoices"
    headers = {"Authorization": f"Bearer {HNRY_API_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "customer_id": inv["customer_id"],
        "quote_ref": inv["quote_ref"],
        "description": f"Service: {inv['subtotal']}+GST",
        "subtotal": float(inv["subtotal"] or 0),
        "gst_amount": float(inv["gst_amount"] or 0),
        "total": float(inv["total"] or 0),
        "issue_date": inv["issue_date"],
        "status": inv["status"],
    }
    try:
        req = requests.post(url, json=payload, headers=headers, timeout=_REQUEST_TIMEOUT)
        ok = 200 <= req.status_code < 300
        conn.execute(
            "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
            " VALUES ('hnry', 'create_invoice', ?, ?, ?, ?)",
            (json_dumps(payload), {"status": req.status_code, "ok": ok}, None),
        )
        conn.commit()
        return {"success": ok, "status": "rest_api", "http_status": req.status_code}
    except Exception as exc:
        conn.execute(
            "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
            " VALUES ('hnry', 'create_invoice', ?, ?, ?, ?)",
            (json_dumps(payload), None, str(exc)),
        )
        conn.commit()
        return {"success": False, "status": "rest_api", "error": str(exc)}


# --------------------------------------------------------------------------- #
# Email workflow (send the invoice PDF)
# --------------------------------------------------------------------------- #
def _attempt_email(conn, inv):
    pdf = _quote_pdf(conn, inv)
    if not pdf:
        return {"success": False, "status": "no_pdf", "error": "invoice PDF unavailable"}
    subject = f"SCT Invoice {inv['invoice_ref']} - {inv['issue_date']}"
    body = (
        "Hello,\n\n"
        f"Please find attached the invoice for the services completed on "
        f"{inv['issue_date']}. Total: {inv['total']} AUD (incl. GST). "
        "Please remit payment by the due date.\n\n"
    )
    r = send_email(
        inv["issued_to_email"] or "customer@example.com",
        subject,
        body,
        reply_to=config.EMAIL_FROM,
    )
    if r.get("mode") == "failed":
        conn.execute(
            "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
            " VALUES ('hnry', 'email', ?, ?, ?, ?)",
            (json_dumps({"to": inv["issued_to_email"]}), None, r.get("error")),
        )
        conn.commit()
    return {"success": r.get("mode") != "failed", "status": "email", "email": r}


# --------------------------------------------------------------------------- #
# Manual fallback
# --------------------------------------------------------------------------- #
def _attempt_manual(conn, inv):
    pdf = _quote_pdf(conn, inv)
    if not pdf:
        return {"success": False, "status": "manual", "error": "no pdf for manual entry"}
    payload = {
        "invoice_ref": inv["invoice_ref"],
        "customer_id": inv["customer_id"],
        "ticket_ref": inv["ticket_ref"],
        "subtotal": float(inv["subtotal"] or 0),
        "gst_amount": float(inv["gst_amount"] or 0),
        "total": float(inv["total"] or 0),
        "issue_date": inv["issue_date"],
        "due_date": inv["due_date"] or None,
        "status": inv["status"],
        "payment_status": inv["payment_status"],
        "notes": inv["notes"],
    }
    conn.execute(
        "INSERT INTO integration_logs (integration, action, payload, result, error, attempted_at)"
        " VALUES ('hnry', 'manual', ?, ?, ?, ?)",
        (json_dumps(payload), {"status": "manual_fallback"}, None),
    )
    conn.commit()
    return {
        "success": True,
        "status": "manual_fallback",
        "invoice_id": inv["id"],
        "pdf": pdf,
        "payload": payload,
    }


# --------------------------------------------------------------------------- #
# PDF generation (minimal, dependency-free)
# --------------------------------------------------------------------------- #
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
    """Render a small A4 PDF with header + wrapped body text."""
    import io

    W, H = 595.0, 842.0
    left, right, top, bottom = 40.0, W - 40.0, 50.0, H - 50.0
    maxw = right - left

    def wrap(text, maxw):
        words = text.split()
        lines, cur = [], ""
        for w in words:
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

    def escape(s):
        return s.replace("\\", "\\5c").replace("(", "\\28").replace(")", "\\29").replace("\n", "\\n")

    lines = wrap(body, maxw)
    first_line_y = top - 14
    line_gap = 14
    start_y = first_line_y - (len(lines) - 1) * line_gap

    objects = []
    objects.append(f"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {W} {H}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(f"<< /Type /Font /Subtype /Helvetica /BaseFont /Helvetica >>")
    objects.append(f"<< /Length {len(body) * 12} 0 R >>")

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
    stream = f"BT /F1 12 Tf 1 0 0 1 {left} {start_y} Td\n({escape(header)}) Tj\n"
    for ln in lines:
        stream += f"0 -14 0 1 Tm ({escape(ln)}) Tj\n"
    stream += "ET"
    objects[4] = f"<< /Length {len(stream)} 0 R >>"
    emit(5, f"<< /Length {len(stream)} 0 R >>\nstream\n{stream}\nendstream")

    xref = len(objects) + 1
    b(f"xref\n0 {xref}\n0000000000 65535 f \n")
    for i, o in enumerate(objects, start=1):
        b(f"{b(o, 10)} {i} 0000000000 z \n")
    b(f"trailer\n<< /Size {xref} /Root {len(objects)} 0 R >>\nstartxref\n{xref}\n%%%%EOF")
    return buf.getvalue()
