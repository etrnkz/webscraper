"""Tests for bot/database.py"""
import pytest
import tempfile
import os
import sqlite3
from bot import database as db


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    # Override DB_PATH
    original_path = db.DB_PATH
    db.DB_PATH = db_path
    db._local = db.threading.local()
    db.init_db()

    yield db

    # Cleanup - close connection first
    db.close_conn()
    db.DB_PATH = original_path
    db._local = db.threading.local()
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestUsageCounting:
    """Test the quota accounting fix."""

    def test_increment_requests_does_not_change_usage_count(self, temp_db):
        """increment_requests should NOT modify usage_count."""
        user_id = 12345

        # Initial usage
        initial_usage = temp_db.get_usage_count(user_id)
        assert initial_usage == 0

        # Call increment_requests (success)
        temp_db.increment_requests(user_id, success=True)
        after_success = temp_db.get_usage_count(user_id)
        assert after_success == initial_usage, "usage_count should not change on success"

        # Call increment_requests (failure)
        temp_db.increment_requests(user_id, success=False)
        after_failure = temp_db.get_usage_count(user_id)
        assert after_failure == initial_usage, "usage_count should not change on failure"

    def test_increment_usage_changes_usage_count(self, temp_db):
        """increment_usage should be the SOLE writer of usage_count."""
        user_id = 12345
        temp_db.register_user(user_id, "test", "Test")

        initial = temp_db.get_usage_count(user_id)
        assert initial == 0

        temp_db.increment_usage(user_id)
        after_first = temp_db.get_usage_count(user_id)
        assert after_first == 1

        temp_db.increment_usage(user_id)
        after_second = temp_db.get_usage_count(user_id)
        assert after_second == 2

    def test_total_requests_and_total_errors_still_work(self, temp_db):
        """increment_requests should still track total_requests and total_errors."""
        user_id = 12345
        temp_db.register_user(user_id, "test", "Test")

        # Success increments total_requests
        temp_db.increment_requests(user_id, success=True)
        user = temp_db.get_user(user_id)
        assert user['total_requests'] == 1
        assert user['total_errors'] == 0

        # Failure increments both
        temp_db.increment_requests(user_id, success=False)
        user = temp_db.get_user(user_id)
        assert user['total_requests'] == 2
        assert user['total_errors'] == 1


class TestCleanupOldData:
    """Test the fixed cleanup_old_data function."""

    def test_cleanup_old_data_removes_old_request_stats(self, temp_db):
        """cleanup_old_data should remove request_stats older than 7 days."""
        user_id = 12345
        temp_db.register_user(user_id, "test", "Test")

        # Insert an old request_stats row (ISO format, 8 days ago)
        import datetime
        old_timestamp = (datetime.datetime.now() - datetime.timedelta(days=8)).isoformat(timespec="microseconds")
        conn = temp_db.get_conn()
        conn.execute(
            "INSERT INTO request_stats (user_id, timestamp) VALUES (?, ?)",
            (user_id, old_timestamp)
        )
        conn.commit()

        # Insert a recent row - use a timestamp 1 second in the future to ensure
        # it's after the cleanup's cutoff (which uses datetime.now())
        recent_timestamp = (datetime.datetime.now() + datetime.timedelta(seconds=1)).isoformat(timespec="microseconds")
        conn.execute(
            "INSERT INTO request_stats (user_id, timestamp) VALUES (?, ?)",
            (user_id, recent_timestamp)
        )
        conn.commit()

        # Verify both exist
        count_before = conn.execute("SELECT COUNT(*) FROM request_stats").fetchone()[0]
        assert count_before == 2

        # Run cleanup
        temp_db.cleanup_old_data()

        # Old row should be gone, recent should remain
        count_after = conn.execute("SELECT COUNT(*) FROM request_stats").fetchone()[0]
        assert count_after == 1

        remaining = conn.execute("SELECT timestamp FROM request_stats").fetchone()[0]
        assert remaining == recent_timestamp

    def test_cleanup_old_data_activity_log_capped(self, temp_db):
        """cleanup_old_data should cap activity_log at 10000 rows."""
        user_id = 12345
        temp_db.register_user(user_id, "test", "Test")

        conn = temp_db.get_conn()

        # Insert 10005 rows
        for i in range(10005):
            conn.execute(
                "INSERT INTO activity_log (user_id, action, details, timestamp, success) VALUES (?, ?, ?, ?, ?)",
                (user_id, f"action_{i}", f"details_{i}", datetime.datetime.now().isoformat(), 1)
            )
        conn.commit()

        count_before = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        assert count_before == 10005

        temp_db.cleanup_old_data()

        count_after = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
        assert count_after == 10000


class TestBusyTimeout:
    """Test that busy_timeout is set."""

    def test_get_conn_sets_busy_timeout(self, temp_db):
        """get_conn should set PRAGMA busy_timeout = 5000."""
        conn = temp_db.get_conn()
        # Verify the pragma was executed by checking it doesn't hang on lock
        # (we can't easily read the pragma value back, but we can verify it works)
        cursor = conn.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        assert timeout == 5000


class TestCloseConn:
    """Test close_conn function."""

    def test_close_conn_closes_connection(self, temp_db):
        """close_conn should close the thread-local connection."""
        conn = temp_db.get_conn()
        assert hasattr(temp_db._local, 'conn')

        temp_db.close_conn()

        # Connection should be closed
        assert not hasattr(temp_db._local, 'conn') or temp_db._local.conn is None


class TestRequestStatsTimestamp:
    """Test that request_stats uses ISO timestamps."""

    def test_request_stats_uses_iso_format(self, temp_db):
        """request_stats.timestamp should be ISO format strings."""
        user_id = 12345
        temp_db.register_user(user_id, "test", "Test")

        temp_db.increment_requests(user_id, success=True)

        conn = temp_db.get_conn()
        row = conn.execute("SELECT timestamp FROM request_stats WHERE user_id=?", (user_id,)).fetchone()

        assert row is not None
        timestamp = row['timestamp']

        # Should be parseable as ISO format
        import datetime
        parsed = datetime.datetime.fromisoformat(timestamp)
        assert isinstance(parsed, datetime.datetime)

    def test_request_stats_not_epoch_float(self, temp_db):
        """request_stats.timestamp should NOT be epoch floats."""
        user_id = 12345
        temp_db.register_user(user_id, "test", "Test")

        temp_db.increment_requests(user_id, success=True)

        conn = temp_db.get_conn()
        row = conn.execute("SELECT timestamp FROM request_stats WHERE user_id=?", (user_id,)).fetchone()

        timestamp = row['timestamp']

        # ISO format contains 'T' or '-', epoch float would be just digits and '.'
        assert 'T' in timestamp or '-' in timestamp
        assert not timestamp.replace('.', '').isdigit()


import datetime


class TestUserStats:
    """Test get_active_users and get_statistics."""

    def test_get_active_users_filters_correctly(self, temp_db):
        now = datetime.datetime.now()
        conn = temp_db.get_conn()

        # User 1 active 2 hours ago
        u1_time = (now - datetime.timedelta(hours=2)).isoformat()
        conn.execute("INSERT INTO users (user_id, first_seen, last_seen) VALUES (101, ?, ?)", (u1_time, u1_time))

        # User 2 active 30 hours ago
        u2_time = (now - datetime.timedelta(hours=30)).isoformat()
        conn.execute("INSERT INTO users (user_id, first_seen, last_seen) VALUES (102, ?, ?)", (u2_time, u2_time))
        conn.commit()

        active_users = temp_db.get_active_users(hours=24)
        active_ids = [u['user_id'] for u in active_users]

        assert 101 in active_ids
        assert 102 not in active_ids

    def test_get_statistics_counts_active_24h(self, temp_db):
        now = datetime.datetime.now()
        conn = temp_db.get_conn()

        u1_time = (now - datetime.timedelta(hours=1)).isoformat()
        conn.execute("INSERT INTO users (user_id, first_seen, last_seen) VALUES (201, ?, ?)", (u1_time, u1_time))

        u2_time = (now - datetime.timedelta(hours=48)).isoformat()
        conn.execute("INSERT INTO users (user_id, first_seen, last_seen) VALUES (202, ?, ?)", (u2_time, u2_time))
        conn.commit()

        stats = temp_db.get_statistics()
        assert stats['total_users'] == 2
        assert stats['active_24h'] == 1
