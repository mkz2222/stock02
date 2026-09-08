import hashlib
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
import monitor as m
from providers import ProviderRouter, Coinbase, TwelveData

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.settings,_=m.read_rules('watchlist.md')
        self.db=m.database(':memory:')
        self.rule=dict(id='eth',market='crypto',symbol='ETH/USD',target=100.,band_percent=5.)
    def tearDown(self): self.db.close()
    def router(self):
        with patch.dict('os.environ',{'APCA_API_KEY_ID':'test','APCA_API_SECRET_KEY':'test'}):
            return ProviderRouter(self.settings)
    def test_prefers_coinbase(self):
        r=self.router()
        with patch.object(r.coinbase,'price',return_value=(100,datetime.now(m.UTC))), patch.object(r.alpaca,'price') as backup:
            self.assertEqual(r.observation(self.rule,self.db,datetime.now(m.UTC))[3],'CoinbaseExchange/USD')
            backup.assert_not_called()
    def test_stale_preferred_price_uses_backup(self):
        r=self.router()
        with patch.object(r.coinbase,'price',return_value=(100,datetime.now(m.UTC)-timedelta(hours=2))),patch.object(r.alpaca,'price',return_value=(101,datetime.now(m.UTC))):
            self.assertEqual(r.observation(self.rule,self.db,datetime.now(m.UTC))[0],101)
    def test_history_failure_switches_price_and_target_together(self):
        r=self.router();self.rule['target']='SMA_1D'
        with patch.object(r.coinbase,'price',return_value=(100,datetime.now(m.UTC))),patch.object(r.alpaca,'price',return_value=(101,datetime.now(m.UTC))),patch('providers.m.target_value',side_effect=[ValueError('missing history'),99]) as target:
            result=r.observation(self.rule,self.db,datetime.now(m.UTC))
            self.assertEqual(result[0],101);self.assertEqual(result[2],99)
            self.assertIs(target.call_args.args[1],r.alpaca)
    def test_prefers_twelve_for_stocks(self):
        r=self.router();self.rule.update(market='stock',symbol='AAPL')
        with patch.object(r.twelve,'price',return_value=(100,datetime.now(m.UTC))),patch.object(r.alpaca,'price') as backup:
            self.assertEqual(r.observation(self.rule,self.db,datetime.now(m.UTC))[3],'TwelveData/split')
            backup.assert_not_called()
    def test_all_stale_fail_closed(self):
        r=self.router();old=datetime.now(m.UTC)-timedelta(days=1)
        with patch.object(r.coinbase,'price',return_value=(100,old)),patch.object(r.alpaca,'price',return_value=(100,old)):
            with self.assertRaisesRegex(ValueError,'All data sources failed'):r.observation(self.rule,self.db,datetime.now(m.UTC))
    def test_source_switch_preserves_legacy_notification_state(self):
        identity={k:self.rule[k] for k in ('market','symbol','target','band_percent')};identity['source']='Alpaca/us'
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        now=datetime.now(m.UTC)
        self.db.execute('INSERT INTO state VALUES (?,?,?,?)',('eth',fingerprint,0,now.timestamp()))
        sent=[]
        m.evaluate(self.db,self.rule,'CoinbaseExchange/USD',100,100,now,now,24,sent.append,False)
        self.assertEqual(sent,[])
    def test_coinbase_weekly_close_and_gap_detection(self):
        p=Coinbase();end=datetime(2026,9,7,tzinfo=m.UTC);start=end-timedelta(days=7)
        rows=[[(start+timedelta(days=i)).timestamp(),1,2,1,100+i,1] for i in range(7)]
        with patch.object(p,'get',return_value=rows):
            bars=p.bars('crypto','ETH/USD','W',1,end)
            self.assertEqual(bars,[{'t':start.isoformat(),'c':106}])
        with patch.object(p,'get',return_value=rows[:-1]):
            with self.assertRaises(ValueError):p.bars('crypto','ETH/USD','W',1,end)
    def test_extended_hours_window(self):
        r=self.router()
        r.alpaca=None
        with patch('providers.datetime') as clock:
            clock.now.return_value=datetime(2026,9,8,22,tzinfo=m.UTC)
            self.assertTrue(r.stock_open())
            r.settings['stock_extended_hours']=False
            self.assertFalse(r.stock_open())
    def test_twelve_extended_request_has_prepost(self):
        p=TwelveData(self.settings)
        with patch('providers.datetime') as clock,patch.object(p,'get',return_value={'close':'100','timestamp':1}) as get:
            clock.now.return_value=datetime(2026,9,8,22,tzinfo=m.UTC)
            p.price('stock','AAPL')
            self.assertEqual(get.call_args.args[1]['prepost'],'true')

if __name__=='__main__': unittest.main()
