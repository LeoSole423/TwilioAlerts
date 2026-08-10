import os
import tempfile
import unittest

from alert_filter import (
    CameraLock,
    LOCK_DIRECTORY,
    camera_from_filename,
    evaluate_alert,
    get_discarded_alerts,
    record_discarded_alert,
    remember_sent_alert,
)


class AlertFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "alert_filter.sqlite3")
        self.settings = {
            "alert_filter_enabled": True,
            "alert_filter_window_seconds": 30,
            "alert_filter_log_only": False,
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extracts_camera_from_filename(self):
        self.assertEqual(camera_from_filename("Escritor.20260806_081210.jpg"), "Escritor")
        self.assertIsNone(camera_from_filename(".jpg"))

    def test_default_locks_live_under_runtime_directory(self):
        self.assertEqual(os.path.basename(LOCK_DIRECTORY), "alert_locks")
        self.assertEqual(os.path.basename(os.path.dirname(LOCK_DIRECTORY)), ".runtime")

    def test_first_alert_is_allowed_and_same_image_is_discarded(self):
        first = evaluate_alert("Escritor", "Escritor.1.jpg", 100.0, self.settings, self.db_path)
        self.assertTrue(first.should_send)
        self.assertTrue(remember_sent_alert("Escritor", "Escritor.1.jpg", 100.0, self.settings, self.db_path))

        duplicate = evaluate_alert("Escritor", "Escritor.1.jpg", 200.0, self.settings, self.db_path)
        self.assertFalse(duplicate.should_send)
        self.assertTrue(duplicate.would_filter)
        self.assertEqual(duplicate.reason, "same_image")

    def test_cooldown_applies_only_to_the_same_camera(self):
        remember_sent_alert("TanqIngreso", "TanqIngreso.1.jpg", 100.0, self.settings, self.db_path)

        repeated = evaluate_alert("TanqIngreso", "TanqIngreso.2.jpg", 105.0, self.settings, self.db_path)
        other_camera = evaluate_alert("T_Jardin", "T_Jardin.1.jpg", 105.0, self.settings, self.db_path)
        at_boundary = evaluate_alert("TanqIngreso", "TanqIngreso.3.jpg", 130.0, self.settings, self.db_path)
        after_window = evaluate_alert("TanqIngreso", "TanqIngreso.4.jpg", 130.001, self.settings, self.db_path)

        self.assertFalse(repeated.should_send)
        self.assertEqual(repeated.reason, "cooldown")
        self.assertTrue(other_camera.should_send)
        self.assertFalse(at_boundary.should_send)
        self.assertTrue(after_window.should_send)

    def test_stale_event_is_discarded(self):
        remember_sent_alert("Angar", "Angar.2.jpg", 100.0, self.settings, self.db_path)

        stale = evaluate_alert("Angar", "Angar.1.jpg", 99.0, self.settings, self.db_path)

        self.assertFalse(stale.should_send)
        self.assertEqual(stale.reason, "stale_event")

    def test_log_only_reports_without_discarding(self):
        log_only = dict(self.settings, alert_filter_log_only=True)
        remember_sent_alert("Hall", "Hall.1.jpg", 100.0, log_only, self.db_path)

        decision = evaluate_alert("Hall", "Hall.2.jpg", 105.0, log_only, self.db_path)

        self.assertTrue(decision.should_send)
        self.assertTrue(decision.would_filter)
        self.assertEqual(decision.reason, "cooldown")

    def test_disabled_filter_allows_repeated_events(self):
        remember_sent_alert("G_Ingreso", "G_Ingreso.1.jpg", 100.0, self.settings, self.db_path)
        disabled = dict(self.settings, alert_filter_enabled=False)

        decision = evaluate_alert("G_Ingreso", "G_Ingreso.1.jpg", 101.0, disabled, self.db_path)

        self.assertTrue(decision.should_send)
        self.assertFalse(decision.would_filter)

    def test_same_camera_lock_excludes_other_process_but_not_other_camera(self):
        first = CameraLock("Escritor", self.temp_dir.name)
        same_camera = CameraLock("Escritor", self.temp_dir.name)
        other_camera = CameraLock("T_Jardin", self.temp_dir.name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(same_camera.acquire())
            self.assertTrue(other_camera.acquire())
        finally:
            other_camera.release()
            same_camera.release()
            first.release()

    def test_records_and_lists_discarded_alert_history(self):
        self.assertTrue(
            record_discarded_alert(
                "Porche",
                "Porche.2.jpg",
                125.0,
                "cooldown",
                25.0,
                self.db_path,
            )
        )
        self.assertTrue(
            record_discarded_alert(
                "Hall",
                "Hall.2.jpg",
                200.0,
                "camera_busy",
                db_path=self.db_path,
            )
        )

        history = get_discarded_alerts(limit=10, db_path=self.db_path)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0][0:5], ("Hall", "Hall.2.jpg", 200.0, "camera_busy", None))
        self.assertEqual(history[1][0:5], ("Porche", "Porche.2.jpg", 125.0, "cooldown", 25.0))

    def test_log_only_does_not_create_discard_history_automatically(self):
        log_only = dict(self.settings, alert_filter_log_only=True)
        remember_sent_alert("Hall", "Hall.1.jpg", 100.0, log_only, self.db_path)

        decision = evaluate_alert("Hall", "Hall.2.jpg", 105.0, log_only, self.db_path)

        self.assertTrue(decision.should_send)
        self.assertEqual(get_discarded_alerts(db_path=self.db_path), [])


if __name__ == "__main__":
    unittest.main()
