import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import requests

from message_stats import get_latest_recorded_counts, record_message_sent
from pilares_sync import _try_sync_lock, sync_pilares_now


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class PilaresSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stats_path = os.path.join(self.temp_dir.name, "message_stats.sqlite3")
        self.lock_path = os.path.join(self.temp_dir.name, "pilares_sync.lock")
        self.settings = {
            "instance_id": "Pilares",
            "pilares_sync_enabled": True,
            "pilares_sync_url": "https://n8n.example.test/webhook/pilares-counter-v1",
            "pilares_sync_token": "test-token",
            "pilares_sync_interval_seconds": 300,
            "pilares_sync_timeout_seconds": 8,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def _record_month(self, year, month, count=1):
        sent_at = datetime(year, month, 15, 12, tzinfo=timezone.utc)
        for _ in range(count):
            self.assertTrue(record_message_sent(sent_at, self.stats_path))

    def test_reads_three_latest_non_empty_months_in_chronological_order(self):
        self._record_month(2026, 3)
        self._record_month(2026, 5)
        self._record_month(2026, 6, count=2)
        self._record_month(2026, 8, count=3)

        self.assertEqual(
            get_latest_recorded_counts(db_path=self.stats_path),
            [("2026-05", 1), ("2026-06", 2), ("2026-08", 3)],
        )

    def test_sends_expected_payload_only_on_http_200(self):
        self._record_month(2026, 8, count=2)
        post = mock.Mock(return_value=FakeResponse(200))

        self.assertTrue(
            sync_pilares_now(
                settings=self.settings,
                stats_path=self.stats_path,
                lock_path=self.lock_path,
                post=post,
            )
        )

        post.assert_called_once_with(
            self.settings["pilares_sync_url"],
            json={
                "schema_version": 1,
                "instance_id": "Pilares",
                "reports": [{"period": "2026-08", "sent_count": 2}],
            },
            headers={"X-Pilares-Token": "test-token"},
            timeout=8,
        )

    def test_http_errors_and_timeouts_leave_sqlite_unchanged(self):
        self._record_month(2026, 8, count=2)
        before = Path(self.stats_path).read_bytes()

        for post in (
            mock.Mock(return_value=FakeResponse(409)),
            mock.Mock(return_value=FakeResponse(500)),
            mock.Mock(side_effect=requests.Timeout("timeout")),
        ):
            self.assertFalse(
                sync_pilares_now(
                    settings=self.settings,
                    stats_path=self.stats_path,
                    lock_path=self.lock_path,
                    post=post,
                )
            )
            self.assertEqual(Path(self.stats_path).read_bytes(), before)

    def test_does_not_post_when_another_sync_holds_the_lock(self):
        self._record_month(2026, 8)
        post = mock.Mock(return_value=FakeResponse(200))

        with _try_sync_lock(self.lock_path) as acquired:
            self.assertTrue(acquired)
            self.assertFalse(
                sync_pilares_now(
                    settings=self.settings,
                    stats_path=self.stats_path,
                    lock_path=self.lock_path,
                    post=post,
                )
            )

        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
