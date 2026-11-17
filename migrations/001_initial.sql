-- SCT v1 initial schema (MySQL).
--
-- Applied exactly once by db.apply_migrations() (db.init_db()).  All timestamps are
-- stored as UTC ISO-8601 strings; the UI converts to Australia/Melbourne for display.
-- Engine is InnoDB; foreign keys are enforced by default.

-- ---------------------------------------------------------------------------
-- Users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  email           VARCHAR(255) NOT NULL UNIQUE,
  password_hash   TEXT NOT NULL,
  name            VARCHAR(255) NOT NULL,
  role            VARCHAR(20) NOT NULL CHECK (role IN ('admin','technician','viewer')),
  is_active       INTEGER NOT NULL DEFAULT 1,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Customers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  customer_id     VARCHAR(32) NOT NULL UNIQUE,
  first_name      VARCHAR(255) NOT NULL,
  last_name       VARCHAR(255) NOT NULL,
  business_name   TEXT,
  email           VARCHAR(255),
  phone_primary   VARCHAR(64),
  phone_alternative VARCHAR(64),
  customer_type   VARCHAR(20) NOT NULL CHECK (customer_type IN ('individual','business','organisation')),
  billing_address LONGTEXT,
  service_address LONGTEXT,
  preferred_contact VARCHAR(20) CHECK (preferred_contact IN ('email','phone','both','none')),
  notes           LONGTEXT,
  status          VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','archived')),
  date_created    TEXT NOT NULL,
  last_interaction TEXT,
  updated_at      TEXT NOT NULL,
  INDEX idx_customers_status (status),
  INDEX idx_customers_type (customer_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Tickets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tickets (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  ticket_ref      VARCHAR(32) NOT NULL UNIQUE,
  customer_id     INTEGER NOT NULL,
  source          VARCHAR(20) DEFAULT 'manual' CHECK (source IN ('manual','enquiry')),
  enquiry_id      INTEGER,
  title           VARCHAR(255) NOT NULL,
  description     LONGTEXT,
  device_make     VARCHAR(255),
  device_model    VARCHAR(255),
  serial_imei     VARCHAR(255),
  symptoms        LONGTEXT,
  diagnosis       LONGTEXT,
  priority        VARCHAR(20) NOT NULL CHECK (priority IN ('low','medium','high','urgent')),
  status          VARCHAR(30) NOT NULL DEFAULT 'new_enquiry' CHECK (
    status IN ('new_enquiry','awaiting_assessment','awaiting_quote',
               'awaiting_customer_approval','approved','scheduled','in_progress',
               'awaiting_parts','awaiting_customer','completed',
               'ready_for_collection','closed','cancelled')),
  assigned_to     INTEGER,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  resolution      LONGTEXT,
  INDEX idx_tickets_customer (customer_id),
  INDEX idx_tickets_status (status),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Enquiries
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enquiries (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  customer_id     INTEGER,
  ticket_id       INTEGER,
  source          VARCHAR(20),
  contact_method  VARCHAR(30),
  contact_details VARCHAR(255),
  message         LONGTEXT,
  received_at     TEXT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Ticket status history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket_status_history (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  ticket_id       INTEGER NOT NULL,
  from_status     VARCHAR(30),
  to_status       VARCHAR(30) NOT NULL,
  changed_by      INTEGER,
  note            LONGTEXT,
  changed_at      TEXT NOT NULL,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Devices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  customer_id     INTEGER NOT NULL,
  ticket_id       INTEGER,
  make            VARCHAR(255),
  model           VARCHAR(255),
  serial_imei     VARCHAR(255),
  device_info     LONGTEXT,
  created_at      TEXT NOT NULL,
  INDEX idx_devices_customer (customer_id),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Ticket internal notes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket_internal_notes (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  ticket_id       INTEGER NOT NULL,
  author          INTEGER,
  note            LONGTEXT NOT NULL,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  FOREIGN KEY (author) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Ticket customer notes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket_customer_notes (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  ticket_id       INTEGER NOT NULL,
  author          INTEGER,
  note            LONGTEXT NOT NULL,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE,
  FOREIGN KEY (author) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Appointments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointments (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  appt_ref        VARCHAR(32) NOT NULL UNIQUE,
  customer_id     INTEGER NOT NULL,
  ticket_id       INTEGER,
  appt_type       VARCHAR(30) NOT NULL CHECK (appt_type IN ('workshop_repair','on_site_support',
               'remote_support','device_collection','device_dropoff','customer_consultation')),
  scheduled_for   TEXT NOT NULL,
  start_time      TEXT NOT NULL,
  duration_minutes INTEGER NOT NULL DEFAULT 60,
  location        VARCHAR(255),
  notes           LONGTEXT,
  assigned_to     INTEGER,
  status          VARCHAR(20) NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','completed','cancelled','no_show','rescheduled')),
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  INDEX idx_appointments_customer (customer_id),
  INDEX idx_appointments_status (status),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
  FOREIGN KEY (assigned_to) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Appointment history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointment_history (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  appt_id         INTEGER NOT NULL,
  from_status     VARCHAR(20),
  to_status       VARCHAR(20) NOT NULL,
  changed_by      INTEGER,
  note            LONGTEXT,
  scheduled_for   TEXT,
  changed_at      TEXT NOT NULL,
  FOREIGN KEY (appt_id) REFERENCES appointments(id) ON DELETE CASCADE,
  FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Quotes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quotes (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  quote_ref       VARCHAR(32) NOT NULL UNIQUE,
  customer_id     INTEGER NOT NULL,
  ticket_id       INTEGER,
  subtotal        DECIMAL(12,2) NOT NULL DEFAULT 0,
  gst_amount      DECIMAL(12,2) NOT NULL DEFAULT 0,
  total           DECIMAL(12,2) NOT NULL DEFAULT 0,
  issue_date      TEXT NOT NULL,
  expiry_date     TEXT NOT NULL,
  status          VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (
    status IN ('draft','sent','viewed','approved','declined','expired','cancelled')),
  issued_to_email VARCHAR(255),
  terms           LONGTEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  INDEX idx_quotes_customer (customer_id),
  INDEX idx_quotes_status (status),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Quote line items
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quote_line_items (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  quote_id        INTEGER NOT NULL,
  description     TEXT NOT NULL,
  quantity        INTEGER NOT NULL DEFAULT 1,
  unit_price      DECIMAL(12,2) NOT NULL DEFAULT 0,
  amount          DECIMAL(12,2) NOT NULL DEFAULT 0,
  gst_included    INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Quote status history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quote_status_history (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  quote_id        INTEGER NOT NULL,
  from_status     VARCHAR(20),
  to_status       VARCHAR(20) NOT NULL,
  changed_by      INTEGER,
  note            LONGTEXT,
  occurred_at     TEXT NOT NULL,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
  FOREIGN KEY (changed_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Quote approvals
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quote_approvals (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  quote_id        INTEGER NOT NULL,
  approver_name   VARCHAR(255),
  approver_email  VARCHAR(255),
  comment         LONGTEXT,
  approved_at     TEXT,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Invoices
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoices (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  invoice_ref     VARCHAR(32) NOT NULL UNIQUE,
  customer_id     INTEGER NOT NULL,
  ticket_id       INTEGER,
  quote_id        INTEGER,
  subtotal        DECIMAL(12,2) NOT NULL DEFAULT 0,
  gst_amount      DECIMAL(12,2) NOT NULL DEFAULT 0,
  total           DECIMAL(12,2) NOT NULL DEFAULT 0,
  issue_date      TEXT NOT NULL,
  due_date        TEXT,
  status          VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','viewed','paid','overdue','void')),
  payment_status  VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (payment_status IN ('pending','paid','overdue','refunded','disputed')),
  payment_date    TEXT,
  hnry_ref        VARCHAR(255),
  hnry_status     VARCHAR(50),
  notes           LONGTEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  INDEX idx_invoices_customer (customer_id),
  INDEX idx_invoices_status (status),
  INDEX idx_invoices_payment (payment_status),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Invoice line items
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invoice_line_items (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  invoice_id      INTEGER NOT NULL,
  description     TEXT NOT NULL,
  quantity        INTEGER NOT NULL DEFAULT 1,
  unit_price      DECIMAL(12,2) NOT NULL DEFAULT 0,
  amount          DECIMAL(12,2) NOT NULL DEFAULT 0,
  gst_rate        DECIMAL(5,2) NOT NULL DEFAULT 0.10,
  FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Service records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS service_records (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  customer_id     INTEGER NOT NULL,
  ticket_id       INTEGER,
  device_id       INTEGER,
  service_date    TEXT NOT NULL,
  issue           LONGTEXT,
  diagnostics     LONGTEXT,
  work_performed  LONGTEXT,
  parts_used      LONGTEXT,
  labour_hours    DECIMAL(5,2),
  technician_notes LONGTEXT,
  photos          TEXT,
  outcome         LONGTEXT,
  warranty        LONGTEXT,
  quote_id        INTEGER,
  invoice_id      INTEGER,
  created_at      TEXT NOT NULL,
  INDEX idx_service_customer (customer_id),
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
  FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE SET NULL,
  FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE SET NULL,
  FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Work notes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS work_notes (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  customer_id     INTEGER NOT NULL,
  ticket_id       INTEGER,
  service_record_id INTEGER,
  note_type       VARCHAR(20) NOT NULL CHECK (note_type IN ('internal','customer')),
  author          INTEGER,
  note            LONGTEXT NOT NULL,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
  FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL,
  FOREIGN KEY (service_record_id) REFERENCES service_records(id) ON DELETE SET NULL,
  FOREIGN KEY (author) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Integration logs (Hnry sync + others)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integration_logs (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  integration     VARCHAR(60) NOT NULL,
  action          VARCHAR(60) NOT NULL,
  payload         LONGTEXT,
  result          LONGTEXT,
  error           LONGTEXT,
  attempted_at    TEXT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Audit logs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  user_id         INTEGER,
  action          VARCHAR(60) NOT NULL,
  entity_type     VARCHAR(60),
  entity_id       INTEGER,
  details         LONGTEXT,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- File uploads
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS file_uploads (
  id              INTEGER PRIMARY KEY AUTO_INCREMENT,
  original_name   VARCHAR(255) NOT NULL,
  stored_name     VARCHAR(255) NOT NULL,
  mime_type       VARCHAR(100) NOT NULL,
  size_bytes      BIGINT NOT NULL DEFAULT 0,
  uploaded_by     INTEGER,
  uploaded_at     TEXT NOT NULL,
  reference_type  VARCHAR(60),
  reference_id    INTEGER,
  FOREIGN KEY (uploaded_by) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ---------------------------------------------------------------------------
-- Schema migration bookkeeping
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT IGNORE INTO schema_version (version) VALUES (1);
