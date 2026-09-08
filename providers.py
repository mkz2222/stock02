"""Market-data providers and whole-observation failover (Python 3.6+)."""
import os
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
import monitor as m


def fresh(quoted, settings):
    age = (datetime.now(m.UTC) - quoted).total_seconds()
    if age < -60 or age > settings['max_quote_age_minutes'] * 60:
        raise ValueError('Price timestamp age {:.0f}s is outside freshness bounds'.format(age))


class TwelveData:
    def __init__(self, settings):
        self.settings = settings
        self.key = os.environ.get('TWELVE_API_KEY', '') or os.environ.get('TWELVE_DATA_API_KEY', '')
        self.last_request = None

    def source(self, market):
        return 'TwelveData/split'

    def get(self, endpoint, params):
        if not self.key:
            raise ValueError('TWELVE_API_KEY is not configured')
        # Stay below the Basic plan's eight credits/minute, including history calls.
        if self.last_request is not None:
            time.sleep(max(0, 8.0 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        result = m.http_json('https://api.twelvedata.com/' + endpoint + '?' + urlencode(params),
                             {'Authorization': 'apikey ' + self.key})
        if result.get('status') == 'error':
            # Never return raw provider errors: they may contain request credentials.
            raise ValueError('Twelve Data request rejected (code {})'.format(result.get('code', 'unknown')))
        return result

    def price(self, market, symbol):
        local = datetime.now(m.UTC).astimezone(m.ZoneInfo('America/New_York'))
        minute = local.hour * 60 + local.minute
        extended = not (570 <= minute < 960)
        params = {'symbol': symbol, 'interval': '1min'}
        if extended and self.settings.get('stock_extended_hours', True):
            params['prepost'] = 'true'
        data = self.get('quote', params)
        return m.positive(data['close']), datetime.fromtimestamp(int(data['timestamp']), m.UTC)

    def bars(self, market, symbol, period, count, boundary):
        data = self.get('time_series', {'symbol': symbol, 'interval': '1week' if period == 'W' else '1day',
            'outputsize': min(count + 10, 5000), 'end_date': (boundary - timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'timezone': 'America/New_York', 'adjust': 'splits', 'order': 'asc'})
        rows = []
        for value in data.get('values', []):
            stamp = datetime.strptime(value['datetime'][:10], '%Y-%m-%d').replace(tzinfo=m.ZoneInfo('America/New_York'))
            rows.append({'t': stamp.isoformat(), 'c': m.positive(value['close'])})
        return rows


class Coinbase:
    def source(self, market):
        return 'CoinbaseExchange/USD'

    def get(self, symbol, endpoint, params=None):
        url = 'https://api.exchange.coinbase.com/products/' + quote(symbol.replace('/', '-'), safe='-') + '/' + endpoint
        return m.http_json(url + ('?' + urlencode(params) if params else ''), {'User-Agent': 'Stockwatch/1.0'})

    def price(self, market, symbol):
        item = self.get(symbol, 'ticker')
        return m.positive(item['price']), m.timestamp(item['time'])

    def bars(self, market, symbol, period, count, boundary):
        end = boundary.astimezone(m.UTC)
        start = end - timedelta(days=count * (7 if period == 'W' else 1))
        cursor, days = start, {}
        while cursor < end:
            stop = min(cursor + timedelta(days=299), end)
            rows = self.get(symbol, 'candles', {'granularity': 86400, 'start': cursor.isoformat(), 'end': stop.isoformat()})
            for row in rows:
                stamp = datetime.fromtimestamp(row[0], m.UTC)
                if start <= stamp < end:
                    days[stamp] = m.positive(row[4])
            cursor = stop
            if cursor < end:
                time.sleep(0.35)
        # Never conceal missing daily candles while constructing a weekly SMA.
        expected = (end - start).days
        if len(days) != expected or any(start + timedelta(days=i) not in days for i in range(expected)):
            raise ValueError('Coinbase history has missing daily candles')
        if period == 'D':
            return [{'t': t.isoformat(), 'c': c} for t, c in sorted(days.items())]
        return [{'t': (start + timedelta(weeks=i)).isoformat(),
                 'c': days[start + timedelta(weeks=i, days=6)]} for i in range(count)]


class ProviderRouter:
    def __init__(self, settings):
        self.settings = settings
        self.twelve = TwelveData(settings)
        self.coinbase = Coinbase()
        try:
            self.alpaca = m.Alpaca(settings)
        except ValueError:
            self.alpaca = None
        self.prices = {}
        self.failures = {}

    def stock_open(self):
        local = datetime.now(m.UTC).astimezone(m.ZoneInfo('America/New_York'))
        minute = local.hour * 60 + local.minute
        lo, hi = (240, 1200) if self.settings.get('stock_extended_hours', True) else (570, 960)
        if local.weekday() >= 5 or not lo <= minute < hi:
            return False
        if self.alpaca is not None:
            try:
                day = local.strftime('%Y-%m-%d')
                sessions = self.alpaca.get('/v2/calendar', {'start': day, 'end': day}, trading=True)
                if not isinstance(sessions, list):
                    raise ValueError('Invalid trading calendar response')
                return bool(sessions)
            except Exception:
                m.LOG.warning('Trading calendar unavailable; using weekday window with strict price freshness')
        return True

    def observation(self, rule, db, now):
        preferred = self.twelve if rule['market'] == 'stock' else self.coinbase
        errors = []
        for provider in [preferred, self.alpaca]:
            if provider is None:
                continue
            source = provider.source(rule['market'])
            key = (source, rule['market'], rule['symbol'])
            try:
                if key in self.failures:
                    raise ValueError(self.failures[key])
                if key not in self.prices:
                    try:
                        self.prices[key] = provider.price(rule['market'], rule['symbol'])
                        fresh(self.prices[key][1], self.settings)
                    except Exception as exc:
                        self.failures[key] = str(exc)
                        raise
                price, quoted = self.prices[key]
                fresh(quoted, self.settings)
                target = m.target_value(rule, provider, db, now)
                if errors:
                    m.LOG.warning('%s switched to %s after %s', rule['symbol'], source, '; '.join(errors))
                return price, quoted, target, source
            except Exception as exc:
                errors.append('{}: {}'.format(source, exc))
        raise ValueError('All data sources failed: ' + '; '.join(errors))
