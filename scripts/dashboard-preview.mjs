import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { activity } from '../netlify/functions/_shared/activity.mjs';
const demo=process.argv.includes('--demo');
http.createServer(async(req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(pathname==='/api/activity') {
    if(demo){
      const now=Date.now();
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify({fetched_at:new Date().toISOString(),runs:Array.from({length:5},(_,i)=>({id:String(i),started_at:new Date(now-i*3600000-4500).toISOString(),finished_at:new Date(now-i*3600000).toISOString(),status:i===1?'error':'success',results:[{symbol:'AAPL',status:'market_closed'},{symbol:'BTC/USD',status:i===1?'error':'outside_range',price:64123.45,error:i===1?'Price check failed. See the monitor logs for details.':undefined}]})),alerts:[{id:1,sent_at:new Date(now-7400000).toISOString(),message:'BTC/USD entered the target range\nPrice: 64,123.45\nObservation from the hourly monitor.'}]}));return;
    }
    const response=await activity(new Request('http://localhost'+req.url,{method:req.method}),{get:k=>process.env[k]});
    res.writeHead(response.status,Object.fromEntries(response.headers));res.end(await response.text());return;
  }
  const files={'/':'index.html','/app.js':'app.js','/styles.css':'styles.css'};
  if(!files[pathname]){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',pathname.endsWith('.js')?'text/javascript':pathname.endsWith('.css')?'text/css':'text/html');
  let content=await readFile(new URL('../dashboard/'+files[pathname],import.meta.url),'utf8');
  if(demo && pathname==='/')content=content.replace('Public dashboard','Demo · sample data');
  res.end(content);
}).listen(8888,'127.0.0.1',()=>console.log(`Dashboard: http://localhost:8888 (${demo?'sample data':'configured backend'})`));
