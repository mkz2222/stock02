import unittest
from datetime import datetime,timedelta
from unittest.mock import Mock
import monitor as m
import rsi_monitor as r

class RSITests(unittest.TestCase):
 def setUp(self):
  self.db=m.database(':memory:');r.setup(self.db);self.sent=[]
  self.end=datetime(2026,9,8,tzinfo=m.UTC)
 def tearDown(self):self.db.close()
 def test_reference_wilder_seed(self):
  prices=[44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28]
  self.assertAlmostEqual(r.wilder_rsi(prices),70.464135,places=5)
 def test_extremes(self):
  self.assertEqual(r.wilder_rsi(list(range(1,251))),100)
  self.assertEqual(r.wilder_rsi(list(range(250,0,-1))),0)
  self.assertEqual(r.wilder_rsi([100]*250),50)
 def test_notifications_strict_threshold_and_rearm(self):
  for i,v in enumerate([30,29,28,31,29]):
   r.notify(self.db,'ETH/USD',v,(self.end+timedelta(days=i)).isoformat(),'test',self.sent.append)
  self.assertEqual(len(self.sent),2)
  r.notify(self.db,'ETH/USD',29,(self.end+timedelta(days=4)).isoformat(),'backup',self.sent.append)
  self.assertEqual(len(self.sent),2)
 def test_delivery_failure_retries_and_dry_run_is_inert(self):
  send=Mock(side_effect=RuntimeError('offline'))
  with self.assertRaises(RuntimeError):r.notify(self.db,'ETH/USD',20,self.end.isoformat(),'test',send)
  self.assertIsNone(self.db.execute('SELECT * FROM rsi_alert_state').fetchone())
  r.notify(self.db,'ETH/USD',20,self.end.isoformat(),'test',self.sent.append,True)
  self.assertEqual(self.sent,[])
  self.assertIsNone(self.db.execute('SELECT * FROM rsi_alert_state').fetchone())
 def test_cache_and_incremental_daily_fetch(self):
  p=Mock();p.source.return_value='test'
  def bars(market,symbol,period,count,end):
   return [{'t':(end-timedelta(days=i)).isoformat(),'c':100+i} for i in range(1,count+1)]
  p.bars.side_effect=bars
  r.daily_value(self.db,p,'ETH/USD',self.end)
  r.daily_value(self.db,p,'ETH/USD',self.end)
  self.assertEqual(p.bars.call_count,1)
  r.daily_value(self.db,p,'ETH/USD',self.end+timedelta(days=1))
  self.assertEqual(p.bars.call_args.args[3],3)
  self.assertEqual(self.db.execute('SELECT count(*) FROM rsi_daily_candles').fetchone()[0],250)
 def test_missing_days_rejected(self):
  p=Mock();p.source.return_value='test';p.bars.return_value=[]
  with self.assertRaises(ValueError):r.daily_value(self.db,p,'ETH/USD',self.end)
 def test_source_fallback_and_completed_day(self):
  router=Mock();router.coinbase.source.return_value='coinbase';router.coinbase.bars.side_effect=ValueError('offline')
  router.alpaca.source.return_value='alpaca'
  router.alpaca.bars.side_effect=lambda market,symbol,period,count,end:[{'t':(end-timedelta(days=i)).isoformat(),'c':100} for i in range(1,count+1)]
  output=[]
  code=r.run_daily_rsi([{'market':'crypto','symbol':'ETH/USD'}],self.db,router,self.sent.append,output,now=self.end+timedelta(minutes=10))
  self.assertEqual(code,0);self.assertEqual(output[0]['source'],'alpaca')
  self.assertEqual(output[0]['closed_at'],self.end.isoformat());self.assertEqual(self.sent,[])

if __name__=='__main__':unittest.main()
