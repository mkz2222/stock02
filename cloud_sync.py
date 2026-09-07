"""Supabase REST synchronization; compatible with Python 3.6 and newer."""
import json
import logging
import os
import re
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

LOG = logging.getLogger("stockwatch")


class CloudSync:
    def __init__(self, db, url, key, device):
        if not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url):
            raise ValueError("SUPABASE_URL must be an HTTPS Supabase project URL")
        if not key.startswith("sb_secret_"):
            raise ValueError("SUPABASE_SECRET_KEY must be a server-side sb_secret_ key")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device):
            raise ValueError("Invalid STOCKWATCH_DEVICE_ID")
        self.db, self.url, self.key, self.device = db, url, key, device
        self.destination = url + "/" + device

    @classmethod
    def from_environment(cls, db):
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SECRET_KEY", "")
        if not url and not key:
            return None
        return cls(db, url.rstrip("/"), key, os.environ.get("STOCKWATCH_DEVICE_ID", "raspberrypi"))

    def request(self, table, params=None, body=None, ignore_duplicates=False):
        headers = {"apikey": self.key, "Content-Type": "application/json",
                   "Prefer": "resolution=merge-duplicates,return=minimal"}
        if ignore_duplicates:
            headers["Prefer"] = "resolution=ignore-duplicates,return=minimal"
        url = self.url + "/rest/v1/" + table + ("?" + urlencode(params) if params else "")
        data = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        try:
            with urlopen(Request(url, data=data, headers=headers), timeout=15) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raise RuntimeError("Supabase HTTP {}; check schema and credentials".format(exc.code)) from None
        except (URLError, TimeoutError):
            raise RuntimeError("Supabase network request failed") from None

    def seed(self, rules):
        rows = [dict(device_id=self.device, id=r["id"], market=r["market"], symbol=r["symbol"],
                     target=str(r["target"]), band_percent=r["band_percent"], note=r.get("note", ""),
                     enabled=True) for r in rules]
        self.request("stockwatch_rules", {"on_conflict": "device_id,id"}, rows, ignore_duplicates=True)
        LOG.info("Imported missing rules; existing cloud rules were preserved")

    def load_rules(self, settings, file_rules, validate):
        cache_key = "rules:" + self.destination
        try:
            rows = self.request("stockwatch_rules", {"device_id": "eq." + self.device,
                "select": "id,market,symbol,target,band_percent,note,enabled", "order": "id", "limit": 501})
            if not isinstance(rows, list) or len(rows) > 500:
                raise ValueError("Supabase watchlist must contain at most 500 rules")
            rules = []
            for row in rows:
                row = dict(row)
                enabled = row.pop("enabled")
                if not isinstance(enabled, bool):
                    raise ValueError("Invalid enabled value")
                if enabled:
                    rules.append(row)
            settings, rules = validate({"settings": settings, "watch": rules}, allow_empty=True)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO cloud_cache VALUES (?,?)", (cache_key, json.dumps(rules)))
            LOG.info("Loaded %d rules from Supabase", len(rules))
            return settings, rules, "supabase"
        except Exception as exc:
            LOG.warning("Cloud watchlist unavailable: %s", exc)
            cached = self.db.execute("SELECT payload FROM cloud_cache WHERE key=?", (cache_key,)).fetchone()
            if cached:
                settings, rules = validate({"settings": settings, "watch": json.loads(cached[0])}, allow_empty=True)
                return settings, rules, "cached_supabase"
            # First connection failure uses the existing, validated file.
            return settings, file_rules, "file_fallback"

    def enqueue(self, event):
        self.db.execute("INSERT INTO cloud_outbox VALUES (?,?,?)",
                        (event["id"], self.destination, json.dumps(event)))

    def flush(self):
        try:
            rows = self.db.execute("SELECT id,payload FROM cloud_outbox WHERE destination=? ORDER BY rowid LIMIT 100",
                                   (self.destination,)).fetchall()
            if rows:
                self.request("stockwatch_runs", {"on_conflict": "id"}, [json.loads(row[1]) for row in rows])
                with self.db:
                    self.db.executemany("DELETE FROM cloud_outbox WHERE id=?", [(row[0],) for row in rows])
            cursor_key = "alerts:" + self.destination
            cursor = self.db.execute("SELECT payload FROM cloud_cache WHERE key=?", (cursor_key,)).fetchone()
            last = int(cursor[0]) if cursor else 0
            alerts = self.db.execute("SELECT id,rule_id,sent,message FROM alerts WHERE id>? ORDER BY id LIMIT 200", (last,)).fetchall()
            if alerts:
                payload = [dict(device_id=self.device, local_id=a[0], rule_id=a[1], sent_epoch=a[2], message=a[3]) for a in alerts]
                self.request("stockwatch_alerts", {"on_conflict": "device_id,local_id"}, payload)
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO cloud_cache VALUES (?,?)", (cursor_key, str(alerts[-1][0])))
            LOG.info("Cloud sync complete: %d runs, %d alerts", len(rows), len(alerts))
            return True
        except Exception as exc:
            LOG.warning("Cloud sync pending; local records retained: %s", exc)
            return False
