#!/usr/bin/env python3
"""Hourly market alerts. Python 3.11+ standard library; 3.6+ with legacy deps."""

import argparse
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
try:
    import tomllib
except ImportError:
    import toml as tomllib
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from dateutil.tz import gettz as ZoneInfo

UTC = timezone.utc
LOG = logging.getLogger("stockwatch")


def timestamp(value):
    # Alpaca can return nanoseconds; datetime stores microseconds.
    normalized = value.replace("Z", "+00:00")
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", normalized)
    if hasattr(datetime, "fromisoformat"):
        parsed = datetime.fromisoformat(normalized)
    else:
        normalized = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", normalized)
        fmt = "%Y-%m-%dT%H:%M:%S" + (".%f" if "." in normalized else "") + "%z"
        parsed = datetime.strptime(normalized, fmt)
    if parsed.tzinfo is None:
        raise ValueError("Quote/bar timestamp must include a timezone")
    return parsed


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Expected a finite positive number")
    return number


def read_rules(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    if Path(path).suffix.lower() == ".md":
        blocks = re.findall(r"^```toml\s*\n(.*?)^```\s*$", text, re.M | re.S)
        if len(blocks) != 1:
            raise ValueError("Markdown must contain exactly one fenced toml block")
        text = blocks[0]
    elif Path(path).suffix.lower() != ".txt":
        raise ValueError("Rules file must be .md or .txt")
    config = tomllib.loads(text)
    if set(config) - {"settings", "watch"}:
        raise ValueError("Unknown top-level configuration key")
    settings = {"cooldown_hours": 24, "max_quote_age_minutes": 20,
                "stock_feed": "iex", "crypto_location": "us"}
    supplied = config.get("settings", {})
    if set(supplied) - set(settings):
        raise ValueError("Unknown settings key")
    settings.update(supplied)
    for key in ("cooldown_hours", "max_quote_age_minutes"):
        settings[key] = positive(settings[key])
    if settings["stock_feed"] not in ("iex", "sip"):
        raise ValueError("stock_feed must be iex or sip")
    if settings["crypto_location"] not in ("us", "us-1", "eu-1"):
        raise ValueError("Unsupported crypto_location")
    rules = config.get("watch", [])
    if not rules:
        raise ValueError("At least one [[watch]] entry is required")
    ids = set()
    for rule in rules:
        if set(rule) - {"id", "market", "symbol", "target", "band_percent", "note"}:
            raise ValueError("Unknown watch key")
        for key in ("id", "market", "symbol", "target"):
            if key not in rule:
                raise ValueError(f"Missing watch field: {key}")
        if not isinstance(rule["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]+", rule["id"]):
            raise ValueError("id must use letters, digits, - or _")
        if rule["id"] in ids:
            raise ValueError("Duplicate watch id")
        ids.add(rule["id"])
        if rule["market"] not in ("stock", "crypto"):
            raise ValueError("market must be stock or crypto")
        pattern = r"[A-Z0-9.-]+" if rule["market"] == "stock" else r"[A-Z0-9]+/[A-Z0-9]+"
        if not isinstance(rule["symbol"], str) or not re.fullmatch(pattern, rule["symbol"]):
            raise ValueError("Use AAPL or BTC/USD, without TradingView exchange prefix")
        target = rule["target"]
        if isinstance(target, str) and re.fullmatch(r"SMA_[1-9][0-9]{0,2}[WD]", target):
            pass
        else:
            rule["target"] = positive(target)
        rule["band_percent"] = positive(rule.get("band_percent", 5))
        if rule["band_percent"] >= 100:
            raise ValueError("band_percent must be below 100")
        if not isinstance(rule.get("note", ""), str):
            raise ValueError("note must be text")
    return settings, rules


def http_json(url, headers=None, form=None):
    data = urlencode(form).encode() if form is not None else None
    # Do not retry notification POSTs: a timeout may mean delivery succeeded.
    attempts = 1 if form is not None else 3
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, data=data, headers=headers or {}), timeout=25) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise RuntimeError(f"HTTP {exc.code}; check service credentials/permissions") from None
        except (URLError, TimeoutError):
            if attempt == attempts - 1:
                raise RuntimeError("Network request failed or timed out") from None
        time.sleep(2 ** attempt)


class Alpaca:
    def __init__(self, settings):
        self.settings = settings
        self.headers = {"APCA-API-KEY-ID": os.environ.get("APCA_API_KEY_ID", ""),
                        "APCA-API-SECRET-KEY": os.environ.get("APCA_API_SECRET_KEY", "")}
        if not all(self.headers.values()):
            raise ValueError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY")

    def get(self, path, params=None, trading=False):
        host = "https://paper-api.alpaca.markets" if trading else "https://data.alpaca.markets"
        return http_json(host + path + ("?" + urlencode(params) if params else ""), self.headers)

    def stock_open(self):
        return self.get("/v2/clock", trading=True)["is_open"]

    def source(self, market):
        return "Alpaca/" + (self.settings["stock_feed"] if market == "stock"
                            else self.settings["crypto_location"])

    def base(self, market):
        return "/v2/stocks" if market == "stock" else "/v1beta3/crypto/" + self.settings["crypto_location"]

    def params(self, market, symbol):
        result = {"symbols": symbol}
        if market == "stock":
            result["feed"] = self.settings["stock_feed"]
        return result

    def price(self, market, symbol):
        suffix = "/trades/latest" if market == "stock" else "/latest/trades"
        trade = self.get(self.base(market) + suffix, self.params(market, symbol))["trades"].get(symbol)
        if not trade:
            raise ValueError("No latest trade available")
        return positive(trade["p"]), timestamp(trade["t"])

    def bars(self, market, symbol, period, count, boundary):
        days = count * (7 if period == "W" else 2) + 60
        params = self.params(market, symbol)
        params.update(timeframe="1Week" if period == "W" else "1Day",
                      start=(boundary - timedelta(days=days)).isoformat(),
                      end=(boundary - timedelta(microseconds=1)).isoformat(),
                      sort="asc", limit=10000)
        if market == "stock":
            params["adjustment"] = "split"
        bars = []
        tokens = set()
        while True:
            result = self.get(self.base(market) + "/bars", params)
            bars.extend((result.get("bars") or {}).get(symbol, []))
            token = result.get("next_page_token")
            if not token:
                return bars
            if token in tokens:
                raise ValueError("Repeated pagination token")
            tokens.add(token)
            params["page_token"] = token


def period_boundary(now, market, period):
    zone = ZoneInfo("America/New_York") if market == "stock" else UTC
    local = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "W":
        local -= timedelta(days=local.weekday())
    return local


def sma(bars, count, boundary, period):
    # Sort chronologically, deduplicate, and exclude the unfinished period.
    closes = {timestamp(b["t"]): positive(b["c"]) for b in bars
              if timestamp(b["t"]) < boundary}
    points = sorted(closes.items())[-count:]
    if len(points) < count:
        raise ValueError(f"Insufficient history: need {count} completed bars, got {len(points)}")
    if period == "W":
        dates = [t.astimezone(boundary.tzinfo).date() for t, _ in points]
        if any((b - a).days != 7 for a, b in zip(dates, dates[1:])):
            raise ValueError("Missing weekly bars; cannot calculate consecutive-week SMA")
        if (boundary.date() - dates[-1]).days != 7:
            raise ValueError("Latest completed week is missing")
    elif boundary - points[-1][0] > timedelta(days=5):
        raise ValueError("Daily history is stale")
    return sum(c for _, c in points) / count


def database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS state (id TEXT PRIMARY KEY, fingerprint TEXT,
            armed INTEGER NOT NULL, last_sent REAL);
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, fetched REAL, payload TEXT);
        CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY, rule_id TEXT,
            sent REAL, message TEXT);
    """)
    return db


def target_value(rule, provider, db, now):
    if not isinstance(rule["target"], str):
        return rule["target"]
    count, period = int(rule["target"][4:-1]), rule["target"][-1]
    boundary = period_boundary(now, rule["market"], period)
    key = json.dumps([provider.source(rule["market"]), rule["symbol"], rule["target"], "split"])
    row = db.execute("SELECT fetched,payload FROM cache WHERE key=?", (key,)).fetchone()
    if row and row[0] >= boundary.timestamp() and now.timestamp() - row[0] < 86400:
        bars = json.loads(row[1])
    else:
        bars = provider.bars(rule["market"], rule["symbol"], period, count, boundary)
        value = sma(bars, count, boundary, period)
        with db:
            db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, now.timestamp(), json.dumps(bars)))
        return value
    return sma(bars, count, boundary, period)


def notifier():
    channel = os.environ.get("NOTIFY_CHANNEL", "")
    required = {"telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                "pushover": ("PUSHOVER_APP_TOKEN", "PUSHOVER_USER_KEY")}
    if channel not in required or not all(os.environ.get(k) for k in required[channel]):
        raise ValueError("Configure NOTIFY_CHANNEL and its notification credentials, or use --dry-run")

    def send(message):
        if channel == "telegram":
            result = http_json("https://api.telegram.org/bot" + os.environ["TELEGRAM_BOT_TOKEN"] + "/sendMessage",
                               form={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": message})
            success = result.get("ok") is True
        else:
            result = http_json("https://api.pushover.net/1/messages.json", form={
                "token": os.environ["PUSHOVER_APP_TOKEN"], "user": os.environ["PUSHOVER_USER_KEY"],
                "title": "Stockwatch", "message": message})
            success = result.get("status") == 1
        if not success:
            raise RuntimeError("Notification service rejected message")
    return send


def evaluate(db, rule, source, price, target, quoted, now, cooldown, send, dry_run):
    identity = {k: rule[k] for k in ("market", "symbol", "target", "band_percent")}
    identity["source"] = source
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    row = db.execute("SELECT fingerprint,armed,last_sent FROM state WHERE id=?", (rule["id"],)).fetchone()
    armed, last = (bool(row[1]), row[2]) if row and row[0] == fingerprint else (True, None)
    band = rule["band_percent"] / 100
    inside = target * (1 - band) <= price <= target * (1 + band)
    if not inside:
        armed = True
    eligible = inside and armed and (last is None or now.timestamp() - last >= cooldown * 3600)
    if eligible:
        message = (f"{rule['symbol']} 进入目标范围\n价格: {price:.4f}\n"
                   f"目标 {rule['target']}: {target:.4f}\n"
                   f"范围: {target * (1-band):.4f}–{target * (1+band):.4f}\n"
                   f"距离: {(price/target-1)*100:+.2f}%\n"
                   f"来源: {source}\n行情时间: {quoted.isoformat()}\n{rule.get('note', '')}")
        if dry_run:
            LOG.info("DRY RUN: %s", message)
        else:
            send(message)
            armed, last = False, now.timestamp()
            with db:
                db.execute("INSERT INTO alerts(rule_id,sent,message) VALUES (?,?,?)", (rule["id"], last, message))
                db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?,?)", (rule["id"], fingerprint, int(armed), last))
            return
    if not dry_run:
        with db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?,?)", (rule["id"], fingerprint, int(armed), last))


def run(settings, rules, db, provider, send, dry_run=False):
    failed = 0
    stock_open = None
    if any(r["market"] == "stock" for r in rules):
        try:
            stock_open = provider.stock_open()
        except Exception as exc:
            LOG.error("Stock market clock unavailable: %s", exc)
            failed += 1
    quotes = {}
    for rule in rules:
        if rule["market"] == "stock" and not stock_open:
            LOG.info("%s skipped: stock session closed or unverified", rule["id"])
            continue
        try:
            now = datetime.now(UTC)
            key = (rule["market"], rule["symbol"])
            if key not in quotes:
                quotes[key] = provider.price(*key)
            price, quoted = quotes[key]
            age = (now - quoted).total_seconds()
            if age < -60 or age > settings["max_quote_age_minutes"] * 60:
                raise ValueError("Quote is stale or timestamp is in the future")
            target = target_value(rule, provider, db, now)
            LOG.info("%s price=%.4f target=%.4f", rule["id"], price, target)
            evaluate(db, rule, provider.source(rule["market"]), price, target, quoted,
                     now, settings["cooldown_hours"], send, dry_run)
        except Exception as exc:
            LOG.error("%s failed: %s", rule["id"], exc)
            failed += 1
    LOG.info("Check complete: %d rules, %d errors", len(rules), failed)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", type=Path, default=Path("watchlist.md"))
    parser.add_argument("--db", type=Path, default=Path("state/monitor.sqlite3"))
    parser.add_argument("--validate", action="store_true", help="Validate file offline and exit")
    parser.add_argument("--dry-run", action="store_true", help="Fetch live data without sending or changing alert state")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings, rules = read_rules(args.rules)
        LOG.info("Loaded %d rules from %s", len(rules), args.rules.resolve())
        if args.validate:
            return 0
        args.db.parent.mkdir(parents=True, exist_ok=True)
        # systemd prevents overlapping runs of this unit; flock also covers manual runs.
        with open(str(args.db) + ".lock", "a") as lock:
            if sys.platform == "linux":
                import fcntl
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    LOG.info("Another check is running; skipping")
                    return 0
            send = None if args.dry_run else notifier()
            provider = Alpaca(settings)
            db = database(args.db)
            try:
                return run(settings, rules, db, provider, send, args.dry_run)
            finally:
                db.close()
    except Exception as exc:
        LOG.error("Monitor stopped: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
