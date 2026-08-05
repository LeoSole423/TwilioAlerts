import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from importlib import util
from pathlib import Path
from unittest import mock

from message_stats import build_counter_message, get_recent_counts, month_key, record_message_sent


class MessageStatsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "message_stats.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_records_successful_messages_in_the_same_month(self):
        sent_at = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
        self.assertTrue(record_message_sent(sent_at, self.db_path))
        self.assertTrue(record_message_sent(sent_at, self.db_path))
        self.assertEqual(get_recent_counts(value=sent_at, db_path=self.db_path)[0], ("2026-08", 2))

    def test_month_boundary_uses_utc_minus_three(self):
        before_midnight = datetime(2026, 9, 1, 2, 59, tzinfo=timezone.utc)
        after_midnight = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
        self.assertEqual(month_key(before_midnight), "2026-08")
        self.assertEqual(month_key(after_midnight), "2026-09")
        record_message_sent(before_midnight, self.db_path)
        record_message_sent(after_midnight, self.db_path)
        self.assertEqual(
            get_recent_counts(value=after_midnight, db_path=self.db_path),
            [("2026-09", 1), ("2026-08", 1), ("2026-07", 0)],
        )

    def test_counter_message_projects_its_own_successful_response(self):
        sent_at = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
        record_message_sent(sent_at, self.db_path)
        message = build_counter_message(sent_at, self.db_path)
        self.assertIn("agosto de 2026: 2", message)
        self.assertIn("julio de 2026: 0", message)
        self.assertIn("junio de 2026: 0", message)


class WebhookCounterCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stats_path = os.path.join(self.temp_dir.name, "message_stats.sqlite3")
        self.state_path = os.path.join(self.temp_dir.name, "user_state.json")
        self.sender = "whatsapp:+5491100000000"
        settings = {
            "twilio_account_sid": "AC" + "a" * 32,
            "twilio_auth_token": "a" * 32,
            "twilio_from_whatsapp": "whatsapp:+14155238886",
            "recipients": [self.sender],
            "session_duration_hours": 24,
        }
        module_path = Path(__file__).parents[1] / "twilio_webhook.py"
        spec = util.spec_from_file_location("twilio_webhook_counter_test", module_path)
        self.webhook = util.module_from_spec(spec)
        with mock.patch("os.path.exists", return_value=True), mock.patch(
            "builtins.open", mock.mock_open(read_data=json.dumps(settings))
        ):
            spec.loader.exec_module(self.webhook)

        self.webhook.STATE_FILE = self.state_path
        Path(self.state_path).write_text(
            '{"whatsapp:+5491100000000": {"paused": true, "session_until": "2030-01-01T00:00:00+00:00"}}',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_counter_counts_its_response_without_changing_user_state(self):
        original_state = Path(self.state_path).read_text(encoding="utf-8")
        with mock.patch("message_stats.STATS_FILE", self.stats_path), mock.patch.object(
            self.webhook.client.messages, "create"
        ) as create_message:
            response = self.webhook.app.test_client().post(
                "/webhook", data={"From": self.sender, "Body": "CONTADOR"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Path(self.state_path).read_text(encoding="utf-8"), original_state)
        self.assertEqual(get_recent_counts(db_path=self.stats_path)[0][1], 1)
        self.assertIn("Mensajes enviados", create_message.call_args.kwargs["body"])

    def test_failed_command_response_is_not_counted(self):
        with mock.patch("message_stats.STATS_FILE", self.stats_path), mock.patch.object(
            self.webhook.client.messages, "create", side_effect=RuntimeError("Twilio error")
        ):
            self.webhook.app.test_client().post(
                "/webhook", data={"From": self.sender, "Body": "CONTADOR"}
            )

        self.assertEqual(get_recent_counts(db_path=self.stats_path)[0][1], 0)


if __name__ == "__main__":
    unittest.main()
