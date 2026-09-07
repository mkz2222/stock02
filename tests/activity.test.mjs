import test from 'node:test';
import assert from 'node:assert/strict';
import { activity } from '../netlify/functions/_shared/activity.mjs';
const env = { get: k => ({SUPABASE_URL:'https://example.supabase.co',SUPABASE_SECRET_KEY:'sb_secret_test',STOCKWATCH_DEVICE_ID:'raspberrypi'}[k]) };
const request = (method='GET') => new Request('https://example.test/api/activity?device_id=other&limit=999', {method});
test('public projection is fixed, bounded, device-scoped and strips private fields', async () => {
  const urls=[];
  const response=await activity(request(),env,async (url,options)=>{
    urls.push(new URL(url)); assert.equal(options.headers.apikey,'sb_secret_test');
    return Response.json(url.includes('stockwatch_runs') ? [{id:'1',status:'error',device_id:'private',results:[{symbol:'BTC/USD',target:123,rule_id:'private',error:'credential details'}]}] : [{local_id:1,sent_epoch:0,message:'<script>alert(1)</script>'}]);
  });
  const body=await response.json(); assert.equal(response.status,200);
  assert.equal(body.runs[0].device_id,undefined); assert.equal(body.runs[0].results[0].target,undefined);
  assert.ok(!JSON.stringify(body).includes('credential details'));
  for(const url of urls){assert.equal(url.searchParams.get('limit'),'30');assert.equal(url.searchParams.get('device_id'),'eq.raspberrypi');assert.ok(!url.pathname.includes('rules'));}
});
test('rejects mutations without touching Supabase',async()=>{
  assert.equal((await activity(request('POST'),env,()=>{throw Error('must not run')})).status,405);
});
test('missing configuration fails closed',async()=>{
  assert.equal((await activity(request(),{get:()=>undefined})).status,503);
});
test('upstream errors never leak response content',async()=>{
  const response=await activity(request(),env,async()=>new Response('sb_secret_something',{status:401}));
  assert.equal(response.status,502); assert.ok(!(await response.text()).includes('sb_secret'));
  assert.equal(response.headers.get('cache-control'),'no-store');
});
test('empty history is valid',async()=>{
  const response=await activity(request(),env,async()=>Response.json([]));
  const body=await response.json(); assert.deepEqual(body.runs,[]);assert.deepEqual(body.alerts,[]);
});
