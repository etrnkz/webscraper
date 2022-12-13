"""SQLite-backed persistent storage for user data at scale"""
import sqlite3
import json
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"

_local = threading.local()


def get_conn():
    if not hasattr(_local, 'conn') or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
    return _local.conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen TEXT,
            last_seen TEXT,
            total_requests INTEGER DEFAULT 0,
            total_errors INTEGER DEFAULT 0,
            blocked_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            usage_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp TEXT,
            success INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS request_stats (
            user_id INTEGER,
            timestamp TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(timestamp);
        CREATE INDEX IF NOT EXISTS idx_requests_user ON request_stats(user_id);
    """)
    conn.commit()


def register_user(user_id, username=None, first_name=None):
    conn = get_conn()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            last_seen = excluded.last_seen,
            username = COALESCE(excluded.username, users.username),
            first_name = COALESCE(excluded.first_name, users.first_name)
    """, (user_id, username, first_name, now, now))
    conn.commit()


def get_user(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        return dict(row)
    return None


def increment_requests(user_id, success=True):
    conn = get_conn()
    now = datetime.now().isoformat()
    if success:
        conn.execute("UPDATE users SET total_requests = total_requests + 1, usage_count = usage_count + 1 WHERE user_id=?", (user_id,))
    else:
        conn.execute("UPDATE users SET total_requests = total_requests + 1, total_errors = total_errors + 1 WHERE user_id=?", (user_id,))
    conn.execute("INSERT INTO request_stats (user_id, timestamp) VALUES (?, ?)", (user_id, now))
    conn.commit()


def record_block(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET blocked_count = blocked_count + 1 WHERE user_id=?", (user_id,))
    conn.commit()


def ban_user(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET is_active = 0 WHERE user_id=?", (user_id,))
    conn.commit()


def unban_user(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET is_active = 1 WHERE user_id=?", (user_id,))
    conn.commit()


def is_banned(user_id):
    conn = get_conn()
    row = conn.execute("SELECT is_active FROM users WHERE user_id=?", (user_id,)).fetchone()
    return row and row['is_active'] == 0


def get_all_users():
    conn = get_conn()
    return conn.execute("SELECT * FROM users ORDER BY last_seen DESC").fetchall()


def get_active_users(hours=24):
    conn = get_conn()
    cutoff = datetime.now().timestamp() - hours * 3600
    return conn.execute(
        "SELECT * FROM users WHERE last_seen IS NOT NULL ORDER BY last_seen DESC"
    ).fetchall()


def get_top_users(limit=10):
    conn = get_conn()
    return conn.execute(
        "SELECT * FROM users ORDER BY total_requests DESC LIMIT ?", (limit,)
    ).fetchall()


def get_statistics():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM users WHERE last_seen >= ?",
                          (datetime.now().isoformat(),)).fetchone()[0]
    reqs = conn.execute("SELECT COALESCE(SUM(total_requests),0) FROM users").fetchone()[0]
    errs = conn.execute("SELECT COALESCE(SUM(total_errors),0) FROM users").fetchone()[0]
    blocks = conn.execute("SELECT COALESCE(SUM(blocked_count),0) FROM users").fetchone()[0]
    return {'total_users': total, 'active_24h': active, 'total_requests': reqs,
            'total_errors': errs, 'total_blocks': blocks}


def log_activity(user_id, action, details=None, success=True):
    conn = get_conn()
    conn.execute(
        "INSERT INTO activity_log (user_id, action, details, timestamp, success) VALUES (?,?,?,?,?)",
        (user_id, action, details, datetime.now().isoformat(), int(success))
    )
    conn.commit()


def get_recent_activities(limit=50):
    conn = get_conn()
    return conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def get_user_activities(user_id, limit=20):
    conn = get_conn()
    return conn.execute(
        "SELECT * FROM activity_log WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()


def cleanup_old_data():
    """Remove stats older than 7 days to keep DB small"""
    conn = get_conn()
    cutoff = (datetime.now().timestamp() - 7 * 86400)
    conn.execute("DELETE FROM request_stats WHERE timestamp < ?", (cutoff,))
    conn.execute("DELETE FROM activity_log WHERE id NOT IN (SELECT id FROM activity_log ORDER BY id DESC LIMIT 10000)")
    conn.commit()


def get_usage_count(user_id):
    conn = get_conn()
    row = conn.execute("SELECT usage_count FROM users WHERE user_id=?", (user_id,)).fetchone()
    return row['usage_count'] if row else 0


def increment_usage(user_id):
    conn = get_conn()
    conn.execute("UPDATE users SET usage_count = usage_count + 1 WHERE user_id=?", (user_id,))
    conn.commit()


init_db()
