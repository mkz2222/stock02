# Stockwatch

An hourly US stock and cryptocurrency monitor for Raspberry Pi. Python evaluates fixed-price or moving-average rules, sends Telegram/Pushover notifications, and stores state in SQLite. Optional Supabase sync lets you edit the watchlist remotely and inspect activity before adding a Netlify dashboard.

## Architecture

- **Raspberry Pi + systemd:** runs the monitor once each hour and after boot.
- **SQLite:** price history cache, alert deduplication, completed run history, cached cloud rules, and pending cloud uploads.
- **Supabase (optional):** authoritative watchlist, uploaded run results, and notification history.
- **Phone:** Telegram or Pushover notifications.

Python 3.11+ uses the standard library only. Python 3.6–3.10 needs the pinned dependencies in `requirements-legacy.txt`. Legacy support has been tested on ARMv7 Raspbian Stretch with Python 3.6.5 and systemd 232; a newer Raspberry Pi OS is recommended for ongoing maintenance.

## Alert behavior

A target of 100 with `band_percent = 5` triggers between 95 and 105, including boundaries. An initial observation inside the range triggers once. Remaining inside does not trigger again, even after 24 hours. An observed exit rearms the rule; a subsequent entry can alert after the 24-hour cooldown. An entry during cooldown is deferred while the price remains inside.

State survives restarts. Changing the symbol, market, target, or band resets that rule's state. Automatic provider switches preserve alert state and cooldown. Changing the note does not. Use a new ID for an unrelated rule; deleting a rule does not erase its historical state.

Hourly polling can miss brief touches between checks. Network timeouts after a notification is accepted can cause a duplicate on retry; exactly-once delivery across an external service and SQLite is not guaranteed.

Stocks are checked on weekdays from 04:00 to 20:00 New York time by default. An available Alpaca trading calendar excludes holidays; if it is unavailable, the weekday window and strict quote freshness checks apply. Set `stock_extended_hours = false` to use the 09:30–16:00 window. These windows do not themselves establish that an exchange is open. Crypto is checked around the clock. If its latest trade is missing or stale, the monitor tries a fresh bid/ask midpoint from the same Alpaca location. Crossed quotes and spreads above 1% are rejected. The same freshness limit applies to this fallback; the midpoint is an indicative price, not an executed trade. Logs identify fallback use and data age. Stale quotes and insufficient history are rejected. One rule failing does not stop the other rules.

## Watchlist

`watchlist.md` contains exactly one fenced TOML block. A `.txt` file contains the same TOML without fences. The program reads the explicitly selected file every run; it does not interpret free-form prose.

```toml
[settings]
cooldown_hours = 24
max_quote_age_minutes = 20
stock_feed = "iex"
crypto_location = "us"

[[watch]]
id = "apple-target"
market = "stock"
symbol = "AAPL"
target = 100
band_percent = 5
note = "Example only: replace with your own target"

[[watch]]
id = "ether-weekly"
market = "crypto"
symbol = "ETH/USD"
target = "SMA_200W"
band_percent = 5
```

Supported targets are a positive price or `SMA_NW` / `SMA_ND`, with N from 1 to 999. SMA uses completed bars only. Stock history is split-adjusted, not dividend-adjusted. Weekly boundaries use Monday, New York time for stocks and UTC for crypto; the previous week is included after the next Monday begins. History is refreshed at period boundaries and at least daily to pick up adjustments. Short or missing weekly history is rejected.

Alpaca IEX is a single-exchange stock feed, not consolidated market data. SIP requires appropriate subscription access. Crypto locations `us`, `us-1`, and `eu-1` depend on provider support and history availability. This is not a TradingView data integration; match exchange, pair, adjustments, and period boundaries when comparing values.

## Local checks

```bash
python3 monitor.py --rules watchlist.md --validate
python3 -m unittest -v
python3 monitor.py --rules watchlist.md --dry-run
```

`--validate` is offline. `--dry-run` fetches real data and may refresh caches, but does not send notifications, change alert state, or upload run history. It does not test notification credentials.

## Raspberry Pi installation

On Raspberry Pi OS with Python 3.11+, copy or clone this repository and run from its directory:

```bash
sudo apt update
sudo apt install -y python3 tzdata ca-certificates
sudo useradd --system --user-group --home-dir /var/lib/stockwatch --no-create-home --shell /usr/sbin/nologin stockwatch
sudo install -d /opt/stockwatch
sudo install -d -m 0750 -o root -g stockwatch /etc/stockwatch
sudo install -d -m 0700 -o stockwatch -g stockwatch /var/lib/stockwatch
sudo install -m 0644 monitor.py cloud_sync.py providers.py rsi_monitor.py /opt/stockwatch/
sudo install -m 0640 -o root -g stockwatch watchlist.md /etc/stockwatch/watchlist.md
sudo install -m 0600 deploy/stockwatch.env.example /etc/stockwatch.env
sudo install -m 0644 deploy/stockwatch.service deploy/stockwatch.timer /etc/systemd/system/
```

Skip `useradd` if the account already exists. On updates, preserve your configuration, credentials, and SQLite database; do not overwrite them with examples.

For a legacy Pi with Python at `/usr/local/bin/python3.6`, additionally install the application-local dependencies and the compatible unit:

```bash
sudo /usr/local/bin/python3.6 -m pip install --target /opt/stockwatch/vendor -r requirements-legacy.txt
sudo install -m 0644 deploy/stockwatch-legacy.service /etc/systemd/system/stockwatch.service
```

Edit credentials and actual targets in your own SSH terminal:

```bash
sudo nano /etc/stockwatch.env
sudo nano /etc/stockwatch/watchlist.md
```

Set `TWELVE_API_KEY` in `/etc/stockwatch.env` for the preferred stock feed. Coinbase public crypto data needs no key. Use Alpaca **paper-account** API credentials for `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. The program does not place orders.

For Telegram, set `NOTIFY_CHANNEL=telegram`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`. Create a bot with official @BotFather, message it `/start`, and obtain your chat ID through its Bot API `getUpdates` response. For Pushover, set `NOTIFY_CHANNEL=pushover`, `PUSHOVER_APP_TOKEN`, and `PUSHOVER_USER_KEY`.

```bash
sudo systemctl daemon-reload
sudo systemctl start stockwatch.service
sudo journalctl -u stockwatch.service -n 50 --no-pager
sudo systemctl enable --now stockwatch.timer
systemctl list-timers stockwatch.timer
```

A successful oneshot service becomes inactive after finishing; the timer stays active. Runs occur within approximately one minute of each hour and about two minutes after boot. Failures retry at the next scheduled run, not in an unlimited restart loop.

## Supabase setup

The hosted database was provisioned on 2026-09-07 in **P01_stock**
(`rcmpuisjnyhbeqccokhh`, US East Ohio), using the recorded migration
`create_stockwatch_tables` from `supabase/schema.sql`.
[Open the Table Editor](https://supabase.com/dashboard/project/rcmpuisjnyhbeqccokhh/editor).
For this project, skip step 1 and use
`SUPABASE_URL=https://rcmpuisjnyhbeqccokhh.supabase.co` in step 2.
The tables are empty; sample watchlist rules were not imported. Configure the
server secret key on the Pi and import its actual rules before enabling cloud sync.
Live service-role inserts, reads, and updates were verified in a rolled-back
transaction. All 18 local tests passed. Pi-to-Supabase REST connectivity still
requires the setup check below. The security advisor reports only informational
"RLS Enabled No Policy" notices, expected for this server-only access model
([advisor explanation](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)).

1. Choose a Supabase project and run `supabase/schema.sql` once in its SQL Editor. This is a bootstrap script, not a recorded CLI migration. It creates only the three `stockwatch_*` tables; if those names already exist, review them before applying.
2. In `/etc/stockwatch.env`, add:

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=sb_secret_your_server_key
STOCKWATCH_DEVICE_ID=raspberrypi
```

Keep the secret key only in this root-readable file. Never commit it or expose it in a dashboard/browser. The integration uses the REST Data API and the server-side `apikey` header; no modern Supabase Python SDK is required on the legacy Pi.

3. Import the current file rules **before the next normal cloud-enabled run**. Stop the timer while setting this up, and wait for any running service to finish. For the deployed legacy Pi:

```bash
sudo systemctl stop stockwatch.timer
sudo systemd-run --unit=stockwatch-import --wait \
  -p User=stockwatch -p Group=stockwatch \
  -p EnvironmentFile=/etc/stockwatch.env \
  -p Environment=PYTHONPATH=/opt/stockwatch/vendor \
  /usr/local/bin/python3.6 /opt/stockwatch/monitor.py \
  --rules /etc/stockwatch/watchlist.md \
  --db /var/lib/stockwatch/monitor.sqlite3 --seed-cloud
```

On a modern Pi, use `/usr/bin/python3` and omit the vendor environment property. The import inserts missing IDs only and does not overwrite existing cloud edits. Check the command exit status and SQL/Table Editor results before proceeding.

4. Start a check, inspect logs for `Loaded ... rules from Supabase` and `Cloud sync complete`, and re-enable the timer:

```bash
sudo systemctl start stockwatch.service
sudo journalctl -u stockwatch.service -n 50 --no-pager
sudo systemctl start stockwatch.timer
```

### Editing without SSH

Open **Supabase Table Editor → stockwatch_rules**. Add or edit rows for your `device_id`; targets are text such as `319` or `SMA_200W`. Toggle `enabled` to pause a rule. The Pi picks up changes on its next hourly run. General settings such as cooldown and feeds remain in the local configuration file.

An empty cloud watchlist, or all-disabled rules, intentionally pauses price checks. It does not restore file rules. Invalid/unreachable cloud data uses the last validated cached cloud watchlist; if none has ever been downloaded, it uses the local file. The file must still be valid because it supplies general settings. Cache age is unlimited so an outage does not automatically stop monitoring; check `rule_source` to identify this condition.

### Activity data for a future dashboard

| Table | Contents |
|---|---|
| `stockwatch_rules` | Editable rule rows, keyed by device and ID |
| `stockwatch_runs` | Start/finish timestamps, status, rule source, and per-rule JSON results |
| `stockwatch_alerts` | Successfully recorded notifications, including pre-sync local history |

Per-rule statuses include `alert_sent`, `already_notified`, `outside_range`, `cooldown`, `market_closed`, `market_clock_error`, and `error`. Results include observed price, target, quote timestamp, and percentage distance when available. This lets a dashboard explain why an hourly check did not send a notification.

Completed runs are written to SQLite first. Failed uploads remain in an outbox; retries use stable primary keys to avoid duplicate cloud rows. Each run uploads up to 100 pending runs and 200 old alerts. Outboxes are scoped to the project/device so changing projects does not accidentally transfer queued runs to another project. Existing local notification history is imported separately for the configured destination. Keep the device ID and SQLite database together; use a new device ID if starting with a fresh database.

Both SQLite and Supabase history currently have no automatic retention policy. Plan cleanup/retention before collecting large watchlists for long periods. Startup failures before the monitoring loop and power loss are not recorded as completed runs; a dashboard should flag overdue check-ins, rather than assume the last successful status means the Pi is online. Cloud failures appear in the Pi's journal; they do not make a successful local monitoring cycle fail.

### Access control

All three tables have RLS enabled and explicit grants for `service_role` only. Anonymous and authenticated browser roles have no access or permissive policies. Supabase's Table Editor remains available to project administrators. A future Netlify dashboard needs an authenticated server-side API or explicitly designed owner-scoped RLS policies; never embed the secret key in frontend code. A secret key has project-wide privilege, so prefer a dedicated project for this monitor.

A public, read-only dashboard for recent runs and alerts is included in `dashboard/`, with a Netlify Function in `netlify/functions/`. See [Netlify deployment instructions](DEPLOY-DASHBOARD.md). No external downtime notification service is included.

## Maintenance

```bash
journalctl -u stockwatch.service --since today
systemctl list-timers stockwatch.timer
sudo systemctl disable --now stockwatch.timer
```

The database is `/var/lib/stockwatch/monitor.sqlite3`. To back it up, stop the timer, wait for the active service to finish, copy the database, then restart the timer. Existing alert state is preserved when upgrading; the new local tables are added automatically.

To change frequency, edit the timer (`OnCalendar=*-*-* 00/2:00:00` means every two hours), run `sudo systemctl daemon-reload`, then restart the timer. The Pi connects outward to cloud APIs; no new inbound port is needed.

## Verification and limitations

Tests cover monitoring behavior plus cloud fallback, empty/invalid watchlists, durable retries, alert synchronization cursors, destination isolation, and non-overwriting imports. Mocked cloud tests do not prove live Supabase connectivity or SQL grants: complete the live setup check above. Actual market-data coverage and notification delivery depend on your accounts.

Official references:
- [Alpaca historical stock bars](https://docs.alpaca.markets/us/v1.4.2/reference/stockbars)
- [Alpaca historical crypto bars](https://docs.alpaca.markets/us/reference/cryptobars-1)
- [Supabase API security](https://supabase.com/docs/guides/api/securing-your-api)
- [Supabase API keys](https://supabase.com/docs/guides/getting-started/api-keys)

## Preferred and backup market data

Stocks use Twelve Data first, then Alpaca. Crypto uses Coinbase Exchange first,
then Alpaca. A missing key, rejected request, stale price, or insufficient history
causes a whole-observation retry: the backup supplies both price and any SMA
history. A notification failure does not trigger provider failover.

Twelve Data uses `TWELVE_API_KEY` (also accepts `TWELVE_DATA_API_KEY`), requests
split-adjusted daily/weekly history, and spaces requests eight seconds apart to
respect the free tier's per-minute budget. Daily credits and endpoint access
still depend on the plan. Coinbase daily candles are fetched in bounded batches;
weekly bars use complete Monday–Sunday UTC weeks and reject missing days.
History caches are isolated by provider, instrument, and target.

Extended stock checks request Twelve Data `prepost=true` outside regular hours.
Its documented real-time extended session is 07:00–20:00 ET on Pro or higher.
Alpaca remains the fallback using the configured `iex` or `sip` feed; available
coverage depends on the account. Closed/stale data is never relabeled as current.
The timer still runs hourly, so it does not check the exact 16:00 close or every
movement during extended hours. Today being closed cannot validate a future
live extended-hours quote; verify timestamps and sources during the next session.

Switches preserve existing notification state, including migration of the old
Alpaca-bound fingerprints. Prices and provider-specific SMA values can differ
between exchanges. The source is recorded in run results and the Pi journal.
API keys stay on the Pi. Public display licensing is still subject to each
provider's plan; API access alone is not a grant to redistribute data.

## Daily RSI(14), first version

Each distinct enabled crypto symbol also gets a confirmed UTC daily RSI(14)
check, independently of its price/SMA check. Stocks are not included in this
first version. Coinbase is preferred; Alpaca supplies a separate complete
history if Coinbase fails. The latest 250 consecutive completed daily closes
warm up Wilder smoothing. A flat series returns 50; all gains return 100;
all losses return 0. Missing days fail the check rather than filling prices.

Candles are persisted in SQLite `rsi_daily_candles` per provider and symbol.
At the next daily boundary, the last three days are fetched and merged; within
that day the cached history is reused. A five-minute UTC-midnight grace period
allows the final candle to settle. The existing hourly timer determines when
this first runs after the grace period. RSI is not a live intraday estimate.

RSI strictly below 30 sends one notification on first observation/entry. Exactly
30 or above does not send; it rearms a later below-30 episode. Persistent
`rsi_alert_state` prevents repeats across hourly checks, restarts, duplicate
watchlist symbols, and provider changes. A notification failure leaves the day
unprocessed for retry; external timeout-after-delivery can still duplicate a
message. Existing price notifications remain independently enabled.

RSI results are included in `stockwatch_runs.results` with indicator, rsi,
period, timeframe, source, and closed_at fields. They require no new Supabase
tables. Notifications use the existing alert outbox/sync. The website-only
repository displays RSI in run summaries and expanded details.
