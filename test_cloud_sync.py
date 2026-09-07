import json
import unittest
from unittest.mock import patch
import monitor
from cloud_sync import CloudSync


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.db = monitor.database(":memory:")
        self.cloud = CloudSync(self.db, "https://example.supabase.co", "sb_secret_test", "pi")
        self.settings, self.rules = monitor.read_rules("watchlist.md")

    def tearDown(self):
        self.db.close()

    def test_empty_cloud_watchlist_stays_empty_when_offline(self):
        with patch.object(self.cloud, "request", return_value=[]):
            self.assertEqual(self.cloud.load_rules(self.settings, self.rules, monitor.validate_config)[1], [])
        with patch.object(self.cloud, "request", side_effect=RuntimeError("offline")):
            _, rules, source = self.cloud.load_rules(self.settings, self.rules, monitor.validate_config)
        self.assertEqual(rules, [])
        self.assertEqual(source, "cached_supabase")

    def test_invalid_cloud_rules_preserve_valid_cache(self):
        rows = [dict(self.rules[0], enabled=True)]
        with patch.object(self.cloud, "request", return_value=rows):
            self.cloud.load_rules(self.settings, self.rules, monitor.validate_config)
        with patch.object(self.cloud, "request", return_value=[dict(rows[0], target="bad")]):
            _, rules, source = self.cloud.load_rules(self.settings, self.rules, monitor.validate_config)
        self.assertEqual(source, "cached_supabase")
        self.assertEqual(rules, [self.rules[0]])

    def test_first_outage_falls_back_to_file(self):
        with patch.object(self.cloud, "request", side_effect=RuntimeError("offline")):
            _, rules, source = self.cloud.load_rules(self.settings, self.rules, monitor.validate_config)
        self.assertEqual(source, "file_fallback")
        self.assertEqual(rules, self.rules)

    def test_failed_upload_retained_then_idempotently_retried(self):
        event = {"id": "event1", "results": []}
        with self.db:
            self.cloud.enqueue(event)
        with patch.object(self.cloud, "request", side_effect=RuntimeError("timeout")):
            self.assertFalse(self.cloud.flush())
        self.assertEqual(self.db.execute("SELECT count(*) FROM cloud_outbox").fetchone()[0], 1)
        with patch.object(self.cloud, "request") as request:
            self.assertTrue(self.cloud.flush())
            self.assertEqual(request.call_args[0][1], {"on_conflict": "id"})
        self.assertEqual(self.db.execute("SELECT count(*) FROM cloud_outbox").fetchone()[0], 0)

    def test_alert_upload_cursor_advances_only_after_success(self):
        self.db.execute("INSERT INTO alerts VALUES (1,'a',123,'message')")
        self.db.commit()
        with patch.object(self.cloud, "request", side_effect=RuntimeError("offline")):
            self.assertFalse(self.cloud.flush())
        with patch.object(self.cloud, "request") as request:
            self.assertTrue(self.cloud.flush())
            self.assertEqual(request.call_args[0][2][0]["local_id"], 1)
        with patch.object(self.cloud, "request") as request:
            self.cloud.flush()
            request.assert_not_called()

    def test_device_change_does_not_upload_other_destination(self):
        with self.db:
            self.cloud.enqueue({"id": "old"})
        other = CloudSync(self.db, "https://other.supabase.co", "sb_secret_test", "pi")
        with patch.object(other, "request") as request:
            other.flush()
            request.assert_not_called()

    def test_seed_does_not_overwrite_existing_rows(self):
        with patch.object(self.cloud, "request") as request:
            self.cloud.seed(self.rules)
            self.assertTrue(request.call_args[1]["ignore_duplicates"])

    def test_rejects_insecure_url_and_public_key(self):
        for url, key in [("http://example.supabase.co", "sb_secret_test"),
                         ("https://example.supabase.co", "sb_publishable_test")]:
            with self.assertRaises(ValueError):
                CloudSync(self.db, url, key, "pi")


if __name__ == "__main__":
    unittest.main()
