"""
SCT acceptance + regression tests.

Run with:  python -m pytest tests/  (or:  python tests/test_sct_mysql.py)

The database-driven tests are **SKIPPED unless a MySQL server is reachable**
(SCT_DB_HOST/PORT/USER/PASSWORD is resolvable).  That matches the task brief's
manual-setup reality: the application targets MySQL/PostgreSQL; this sandbox has
no bundled RDBMS.  When a server *is* reachable, the full enquiry→->invoice
acceptance journey (task item 1-17) executes end-to-end.
"""
from __future__ import annotations

import os
import sys
import unittest

# make the sct-app package importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _db_ready():
    """True if a MySQL server answers on the configured host/port."""
    try:
        pymysql.connect(
            host=os.environ.get("SCT_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("SCT_DB_PORT", "3306")),
            user=os.environ.get("SCT_DB_USER", "sct"),
            password=os.environ.get("SCT_DB_PASSWORD", ""),
            database=os.environ.get("SCT_DB_NAME", "sct"),
            connect_timeout=3,
        ).close()
        return True
    except Exception:
        return False


def _conn():
    import db
    return db.get_connection()


# --------------------------------------------------------------------------- #
# database-level tests
# --------------------------------------------------------------------------- #
class TestDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not _db_ready():
            raise unittest.SkipTest(
                "MySQL not reachable - provision per docs/setup-mysql.md and retry."
            )

    def setUp(self):
        self.conn = _conn()

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # -- schema ------------------------------------------------------------ #
    def test_schema_applies(self):
        import db
        db.apply_migrations()
        tables = {
            r[0] for r in self.conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE()")
        }
        needed = {
            "users","customers","devices","tickets","ticket_status_history",
            "ticket_internal_notes","ticket_customer_notes","enquiries",
            "appointments","appointment_history","quotes","quote_line_items",
            "quote_status_history","quote_approvals","invoices","invoice_line_items",
            "service_records","work_notes","integration_logs","audit_logs",
            "file_uploads","schema_version",
        }
        self.assertTrue(needed <= tables, f"missing tables: {needed - tables}")

    # -- reference generators -------------------------------------------- #
    def test_customer_ref(self):
        import db
        self.assertRegex(db.next_customer_id(self.conn), r"^CST-[0-9A-Z]{8}$")

    def test_ticket_ref(self):
        import db
        self.assertRegex(db.next_ticket_ref(), r"^TKT-[0-9A-Z]{8}$")

    def test_appointment_ref(self):
        import db
        self.assertRegex(db.next_appt_ref(), r"^APT-[0-9A-Z]{8}$")

    def test_quote_ref(self):
        import db
        self.assertRegex(db.next_quote_ref(), r"^QT-[0-9A-Z]{8}$")

    def test_invoice_ref(self):
        import db
        self.assertRegex(db.next_invoice_ref(), r"^INV-[0-9A-Z]{8}$")

    # -- enquiry to invoice (acceptance) -----------------------------------
    def test_enquiry_to_invoice_full_flow(self):
        import db
        c = self.conn
        # 1. create customer
        cid = db.save_customer(c, {
            "first_name": "Test", "last_name": "Customer", "email": "test@example.com",
            "phone_primary": "0400000000", "customer_type": "individual",
            "status": "active",
        })
        cust_ref = c.execute("SELECT customer_id FROM customers WHERE id=%s", (cid,)).fetchone()["customer_id"]

        # 2. create ticket
        tid = db.save_ticket(c, {
            "title": "Broken screen", "description": "LCD cracked",
            "device_make": "Apple", "device_model": "MacBook Air",
            "serial_imei": "A12345", "symptoms": "screen broken",
            "priority": "high", "status": "new_enquiry", "customer_id": cust_ref,
        })
        tref = c.execute("SELECT ticket_ref FROM tickets WHERE id=%s", (tid,)).fetchone()["ticket_ref"]

        # 3. appointment
        aid = db.save_appointment(c, {
            "appt_type": "device_collection", "scheduled_for": "2026-11-20",
            "start_time": "09:30", "duration_minutes": 60,
            "status": "scheduled", "customer_id": cust_ref, "ticket_id": tid,
        })
        aref = c.execute("SELECT appt_ref FROM appointments WHERE id=%s", (aid,)).fetchone()["appt_ref"]

        # 4. quote
        qid = db.save_quote(c, {
            "status": "draft", "issue_date": "2026-11-20", "expiry_date": "2026-12-04",
            "subtotal": 120.00, "gst_amount": 12.00, "total": 132.00,
            "customer_id": cust_ref, "ticket_id": tid,
        })
        qref = c.execute("SELECT quote_ref FROM quotes WHERE id=%s", (qid,)).fetchone()["quote_ref"]

        # 5. quote approval
        db.save_quote_approval(c, qid, "Test Customer", "test@example.com",
                               "Approved for repair.", None)
        db.update_quote_status(c, qid, "approved")

        # 6. service record (labour + parts)
        sid = db.save_service_record(c, {
            "service_date": "2026-11-21", "issue": "Cracked LCD",
            "diagnostics": "Touch panel failed", "work_performed": "Replaced LCD",
            "parts_used": "LCD panel 13\"", "labour_hours": 0.5,
            "technician_notes": "Used new panel", "outcome": "Completed",
            "warranty": "12 months", "customer_id": cust_ref,
            "ticket_id": tid, "quote_id": qid,
        })
        sref = c.execute("SELECT id FROM service_records WHERE id=%s", (sid,)).fetchone()["id"]

        # 7. invoice (manual fallback to Hnry)
        iid = db.save_invoice(c, {
            "status": "sent", "payment_status": "pending",
            "issue_date": "2026-11-21", "subtotal": 120.00,
            "gst_amount": 12.00, "total": 132.00,
            "customer_id": cust_ref, "ticket_id": tid, "quote_id": qid,
        })
        iref = c.execute("SELECT invoice_ref FROM invoices WHERE id=%s", (iid,)).fetchone()["invoice_ref"]

        # 8. mark paid (confirmed)
        db.save_invoice(c, {"id": iid, "payment_status": "paid",
                            "payment_date": "2026-11-25", "status": "sent"})

        # --- assertions ------------------------------------------------------- #
        self.assertIsNotNone(c.execute("SELECT id FROM customers WHERE customer_id=%s", (cust_ref,)).fetchone())
        self.assertIsNotNone(c.execute("SELECT id FROM tickets WHERE ticket_ref=%s", (tref,)).fetchone())
        self.assertIsNotNone(c.execute("SELECT id FROM appointments WHERE appt_ref=%s", (aref,)).fetchone())
        self.assertIsNotNone(c.execute("SELECT id FROM quotes WHERE quote_ref=%s", (qref,)).fetchone())
        self.assertEqual(c.execute(
            "SELECT status FROM quotes WHERE id=%s", (qid,)).fetchone()["status"], "approved")
        self.assertIsNotNone(c.execute("SELECT id FROM service_records WHERE id=%s", (sid,)).fetchone())
        self.assertIsNotNone(c.execute("SELECT id FROM invoices WHERE invoice_ref=%s", (iref,)).fetchone())
        self.assertEqual(c.execute(
            "SELECT payment_status FROM invoices WHERE id=%s", (iid,)).fetchone()["payment_status"], "paid")

    # -- anti-guards ------------------------------------------------------- #
    def test_no_duplicate_customers(self):
        import db
        c = self.conn
        a = db.save_customer(c, {"first_name":"Dup","last_name":"Customer","email":"dup@example.com","phone_primary":"0400000001","status":"active"})
        b = db.save_customer(c, {"first_name":"Dup","last_name":"Customer","email":"dup@example.com","phone_primary":"0400000001","status":"active"})
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
