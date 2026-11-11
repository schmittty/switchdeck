"""SQLite storage. One connection per thread; WAL mode for concurrent poller + web access."""
import json
import sqlite3
import threading
import time

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    site_id INTEGER REFERENCES sites(id),
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER,
    scheme TEXT DEFAULT 'http',
    family TEXT NOT NULL,              -- sispm | lss
    username TEXT NOT NULL,
    secret TEXT NOT NULL,              -- encrypted password
    model TEXT DEFAULT '', mac TEXT DEFAULT '', serial TEXT DEFAULT '',
    firmware TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',     -- pending | online | offline | error
    last_seen REAL, last_error TEXT DEFAULT '',
    adopted INTEGER DEFAULT 0,
    created REAL
);
CREATE TABLE IF NOT EXISTS snapshots (
    device_id INTEGER PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    ts REAL, data TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    device_id INTEGER, ts REAL, cpu REAL, temp REAL, poe_w REAL, rx_bps REAL, tx_bps REAL
);
CREATE INDEX IF NOT EXISTS metrics_dev_ts ON metrics(device_id, ts);
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY, device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    ts REAL, sha TEXT, content TEXT, trigger TEXT, note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts REAL, device_id INTEGER, level TEXT, source TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, ts REAL, user TEXT, action TEXT, target TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, pwhash TEXT NOT NULL, role TEXT DEFAULT 'admin'
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, username TEXT, expires REAL
);
CREATE TABLE IF NOT EXISTS discovered (
    host TEXT PRIMARY KEY, family TEXT, port INTEGER, scheme TEXT, hint TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS firmware (
    id INTEGER PRIMARY KEY, filename TEXT UNIQUE, family TEXT, models TEXT, version TEXT,
    size INTEGER, uploaded REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY, name TEXT, token_hash TEXT UNIQUE, prefix TEXT,
    created REAL, last_used REAL, created_by TEXT
);
CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, username TEXT NOT NULL, secret TEXT NOT NULL,
    notes TEXT DEFAULT '', created REAL
);
CREATE TABLE IF NOT EXISTS port_roles (
    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE, port INTEGER, role TEXT,
    PRIMARY KEY (device_id, port)
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


# Columns added after the first release; applied to existing databases on start-up.
MIGRATIONS = {
    "users": [("source", "TEXT DEFAULT 'local'"), ("must_change", "INTEGER DEFAULT 0"),
              ("disabled", "INTEGER DEFAULT 0"), ("last_login", "REAL"), ("created", "REAL"),
              ("full_name", "TEXT DEFAULT ''")],
    "devices": [("credential_id", "INTEGER REFERENCES credentials(id)")],
    "sessions": [("ip", "TEXT DEFAULT ''"), ("created", "REAL")],
}


def init():
    c = conn()
    c.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, ddl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    if not c.execute("SELECT 1 FROM sites").fetchone():
        c.execute("INSERT INTO sites(name) VALUES ('Default')")
    c.commit()


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def x(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur.lastrowid


def event(device_id, level, message, source="system"):
    x("INSERT INTO events(ts, device_id, level, source, message) VALUES (?,?,?,?,?)",
      (time.time(), device_id, level, source, message[:2000]))


def audit(user, action, target="", detail=""):
    if not isinstance(detail, str):
        detail = json.dumps(detail)
    x("INSERT INTO audit(ts, user, action, target, detail) VALUES (?,?,?,?,?)",
      (time.time(), user, action, str(target), detail[:4000]))


def setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))
