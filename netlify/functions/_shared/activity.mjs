// Public, read-only projection. Never accept table names or device IDs from callers.
export async function activity(request, env, fetcher = fetch) {
  const reply = (body, status = 200) => Response.json(body, { status, headers: {
    'Cache-Control': status === 200 ? 'public, max-age=30, s-maxage=60' : 'no-store',
    'X-Content-Type-Options': 'nosniff',
  }});
  if (request.method !== 'GET') return reply({ error: 'Method not allowed' }, 405);
  const url = (env.get('SUPABASE_URL') || '').replace(/\/$/, '');
  const key = env.get('SUPABASE_SECRET_KEY') || '';
  const device = env.get('STOCKWATCH_DEVICE_ID') || 'raspberrypi';
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(url) || !key.startsWith('sb_secret_') || !/^[A-Za-z0-9_-]{1,64}$/.test(device)) {
    return reply({ error: 'Dashboard connection is not configured yet.' }, 503);
  }
  const read = async (table, select, order, limit) => {
    const params = new URLSearchParams({ select, order, limit: String(limit), device_id: `eq.${device}` });
    const result = await fetcher(`${url}/rest/v1/${table}?${params}`, {
      headers: { apikey: key, Accept: 'application/json' }, signal: AbortSignal.timeout(10000),
    });
    if (!result.ok) throw new Error('Upstream failed');
    const rows = await result.json();
    if (!Array.isArray(rows)) throw new Error('Invalid response');
    return rows;
  };
  try {
    const [runs, alerts] = await Promise.all([
      read('stockwatch_runs', 'id,started_at,finished_at,status,results', 'started_at.desc,id.desc', 30),
      read('stockwatch_alerts', 'local_id,sent_epoch,message', 'sent_epoch.desc,local_id.desc', 30),
    ]);
    // Explicit fields prevent accidental disclosure as the database schema grows.
    return reply({ fetched_at: new Date().toISOString(),
      runs: runs.map(r => ({ id: r.id, started_at: r.started_at, finished_at: r.finished_at, status: r.status,
        results: (Array.isArray(r.results) ? r.results : []).slice(0, 500).map(v => ({
          symbol: v.symbol, status: v.status, price: v.price, quoted_at: v.quoted_at,
          error: typeof v.error === 'string' ? 'Price check failed. See the monitor logs for details.' : undefined,
        })),
      })),
      alerts: alerts.map(a => ({ id: a.local_id, sent_at: new Date(a.sent_epoch * 1000).toISOString(), message: a.message })),
    });
  } catch {
    return reply({ error: 'Activity is temporarily unavailable. Please try again shortly.' }, 502);
  }
}
