"""Confirmed daily crypto RSI with durable candles and episode notifications."""
import json
from datetime import datetime, timedelta
import monitor as m


def wilder_rsi(closes, period=14):
    prices = [m.positive(c) for c in closes]
    if len(prices) <= period:
        raise ValueError('RSI requires at least 15 closes')
    changes = [b-a for a,b in zip(prices, prices[1:])]
    gain = sum(max(c,0) for c in changes[:period])/period
    loss = sum(max(-c,0) for c in changes[:period])/period
    for change in changes[period:]:
        gain = (gain*(period-1)+max(change,0))/period
        loss = (loss*(period-1)+max(-change,0))/period
    if gain == 0 and loss == 0:
        return 50.0
    return 100.0 if loss == 0 else 100.0-100.0/(1.0+gain/loss)


def setup(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS rsi_daily_candles (
        source TEXT, symbol TEXT, day TEXT, close REAL NOT NULL,
        PRIMARY KEY(source,symbol,day));
      CREATE TABLE IF NOT EXISTS rsi_alert_state (
        symbol TEXT PRIMARY KEY, closed_at TEXT NOT NULL, below INTEGER NOT NULL);
    ''')


def daily_value(db, provider, symbol, boundary):
    source=provider.source('crypto')
    start=boundary-timedelta(days=250)
    rows=db.execute('SELECT day,close FROM rsi_daily_candles WHERE source=? AND symbol=? AND day>=? AND day<? ORDER BY day',
                    (source,symbol,start.isoformat(),boundary.isoformat())).fetchall()
    candles={m.timestamp(day):close for day,close in rows}
    expected=[start+timedelta(days=i) for i in range(250)]
    if not all(day in candles for day in expected):
        count=3 if len(candles)>=247 else 250
        fetched=provider.bars('crypto',symbol,'D',count,boundary)
        for bar in fetched:
            day=m.timestamp(bar['t']).astimezone(m.UTC)
            if start<=day<boundary and day.hour==day.minute==day.second==day.microsecond==0:
                candles[day]=m.positive(bar['c'])
        if not all(day in candles for day in expected):
            raise ValueError('RSI history needs 250 consecutive completed UTC daily candles')
        with db:
            db.executemany('INSERT OR REPLACE INTO rsi_daily_candles VALUES (?,?,?,?)',
                           [(source,symbol,day.isoformat(),candles[day]) for day in expected])
            db.execute('DELETE FROM rsi_daily_candles WHERE source=? AND symbol=? AND day<?',
                       (source,symbol,start.isoformat()))
    return wilder_rsi([candles[day] for day in expected])


def notify(db,symbol,value,closed_at,source,send,dry_run=False):
    below=value<30
    state=db.execute('SELECT closed_at,below FROM rsi_alert_state WHERE symbol=?',(symbol,)).fetchone()
    if state and closed_at<=state[0]:
        return 'already_evaluated'
    should_send=below and (state is None or not state[1])
    status='below_30' if below else 'no_alert'
    if should_send:
        status='would_alert' if dry_run else 'alert_sent'
        if not dry_run:
            message=('{} 日线 RSI(14) 低于 30\nRSI: {:.2f}\n日线收盘时间: {}\n来源: {}\n仅使用已收盘日线').format(symbol,value,closed_at,source)
            send(message)
            with db:
                db.execute('INSERT INTO alerts(rule_id,sent,message) VALUES (?,?,?)',
                           ('rsi14:'+symbol,datetime.now(m.UTC).timestamp(),message))
                db.execute('INSERT OR REPLACE INTO rsi_alert_state VALUES (?,?,?)',(symbol,closed_at,1))
            return status
    if not dry_run:
        with db:
            db.execute('INSERT OR REPLACE INTO rsi_alert_state VALUES (?,?,?)',(symbol,closed_at,int(below)))
    return status


def run_daily_rsi(rules,db,router,send,results,dry_run=False,now=None):
    setup(db)
    now=now or datetime.now(m.UTC)
    # Allow five minutes after UTC midnight for the final daily candle to settle.
    boundary=(now-timedelta(minutes=5)).astimezone(m.UTC).replace(hour=0,minute=0,second=0,microsecond=0)
    failed=0
    for symbol in sorted({r['symbol'] for r in rules if r['market']=='crypto'}):
        result={'rule_id':'rsi14:'+symbol,'symbol':symbol,'market':'crypto','indicator':'RSI',
                'period':14,'timeframe':'1day','closed_at':boundary.isoformat(),'checked_at':now.isoformat()}
        results.append(result)
        errors=[]
        for provider in [router.coinbase,router.alpaca]:
            if provider is None: continue
            try:
                value=daily_value(db,provider,symbol,boundary)
                source=provider.source('crypto')
                break
            except Exception as exc:
                errors.append(str(exc))
        else:
            result.update(status='error',error='RSI data unavailable: '+'; '.join(errors))
            m.LOG.error('%s RSI failed: %s',symbol,result['error']);failed+=1;continue
        result.update(rsi=value,source=source)
        try:
            result['status']=notify(db,symbol,value,boundary.isoformat(),source,send,dry_run)
            m.LOG.info('%s RSI(14) daily=%.4f closed_at=%s source=%s status=%s',symbol,value,boundary.isoformat(),source,result['status'])
        except Exception as exc:
            result.update(status='error',error='RSI notification failed: '+str(exc));failed+=1
            m.LOG.error('%s RSI notification failed',symbol)
    return int(failed>0)
