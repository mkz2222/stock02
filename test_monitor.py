import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import monitor as m


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.db"
        self.db = m.database(self.path)
        self.now = datetime(2026, 9, 7, 12, tzinfo=m.UTC)
        self.rule = dict(id="test", market="crypto", symbol="BTC/USD", target=100., band_percent=5.)
        self.sent = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def evaluate(self, price, hours=0, dry=False, sender=None):
        now = self.now + timedelta(hours=hours)
        m.evaluate(self.db, self.rule, "test", price, 100, now, now, 24,
                   sender or self.sent.append, dry)

    def test_restart_dedup_and_reentry_cooldown(self):
        self.evaluate(105)
        self.db.close()
        self.db = m.database(self.path)
        self.evaluate(100, 1)
        self.evaluate(106, 2)
        self.evaluate(95, 3)
        self.assertEqual(len(self.sent), 1)
        self.evaluate(99, 25)
        self.evaluate(100, 50)
        self.assertEqual(len(self.sent), 2)

    def test_send_failure_does_not_consume_alert(self):
        def fail(_):
            raise RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            self.evaluate(100, sender=fail)
        self.evaluate(100, 1)
        self.assertEqual(len(self.sent), 1)

    def test_dry_run_does_not_change_state(self):
        self.evaluate(100, dry=True)
        self.assertEqual(self.db.execute("SELECT count(*) FROM state").fetchone()[0], 0)
        self.assertEqual(self.sent, [])

    def test_rule_change_resets_state(self):
        self.evaluate(100)
        self.rule["band_percent"] = 6
        self.evaluate(100, 1)
        self.assertEqual(len(self.sent), 2)

    def test_sma_excludes_current_week_sorts_and_detects_missing(self):
        boundary = self.now.replace(hour=0)
        bars = [{"t": (boundary - timedelta(weeks=i)).isoformat(), "c": i}
                for i in range(1, 201)]
        bars.append({"t": boundary.isoformat(), "c": 999999})
        self.assertEqual(m.sma(bars, 200, boundary, "W"), 100.5)
        with self.assertRaises(ValueError):
            m.sma(bars[:-2], 200, boundary, "W")
        bars[5]["t"] = (boundary - timedelta(weeks=250)).isoformat()
        with self.assertRaises(ValueError):
            m.sma(bars, 200, boundary, "W")

    def test_examples_and_invalid_file(self):
        m.read_rules("watchlist.md")
        m.read_rules("watchlist.example.txt")
        p = Path(self.tmp.name) / "bad.txt"
        p.write_text('[[watch]]\nid="a"\nmarket="crypto"\nsymbol="BTC/USD"\ntarget=nan\n')
        with self.assertRaises(ValueError):
            m.read_rules(p)

    def test_stale_quote_is_not_sent(self):
        class Provider:
            def price(inner, *args):
                return 100, datetime.now(m.UTC) - timedelta(hours=2)
        settings, _ = m.read_rules("watchlist.md")
        self.assertEqual(m.run(settings, [self.rule], self.db, Provider(), self.sent.append), 1)
        self.assertEqual(self.sent, [])

    def test_pagination(self):
        with patch.dict("os.environ", {"APCA_API_KEY_ID": "x", "APCA_API_SECRET_KEY": "y"}):
            provider = m.Alpaca({"stock_feed": "iex", "crypto_location": "us"})
        pages = [{"bars": {"BTC/USD": [{"c": 1}]}, "next_page_token": "next"},
                 {"bars": {"BTC/USD": [{"c": 2}]}, "next_page_token": None}]
        with patch.object(provider, "get", side_effect=pages) as get:
            result = provider.bars("crypto", "BTC/USD", "W", 200, self.now)
        self.assertEqual(len(result), 2)
        self.assertEqual(get.call_count, 2)

    def test_stock_clock_failure_does_not_block_crypto(self):
        class Provider:
            def stock_open(inner):
                raise RuntimeError("offline")
            def price(inner, *args):
                return 100, datetime.now(m.UTC)
            def source(inner, *args):
                return "test"
        settings, _ = m.read_rules("watchlist.md")
        stock = dict(self.rule, id="stock", market="stock", symbol="AAPL")
        self.assertEqual(m.run(settings, [stock, self.rule], self.db, Provider(), self.sent.append), 1)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
