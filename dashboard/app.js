const $ = id => document.getElementById(id);
let data = null;
let busy = false;
const date = value => new Date(value);
const validDate = value => Number.isFinite(date(value).getTime());
const fullTime = value => validDate(value) ? date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Unknown time';
const relative = value => {
  const minutes = Math.floor((Date.now() - date(value).getTime()) / 60000);
  if (!Number.isFinite(minutes)) return 'Unknown';
  if (minutes < -1) return 'Clock ahead';
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  return `${Math.floor(minutes / 1440)}d ago`;
};
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const labels = { success: 'Successful', error: 'Check failed', market_closed: 'Market closed', market_clock_error: 'Market clock unavailable', alert_sent: 'Alert sent', already_notified: 'Already notified', outside_range: 'Outside range', cooldown: 'In cooldown' };
function renderRuns() {
  const openIds = new Set([...$('runs').querySelectorAll('details[open]')].map(n => n.dataset.id));
  $('runs').replaceChildren();
  const runs = data.runs.filter(r => $('filter').value === 'all' || r.status === $('filter').value);
  if (!runs.length) $('runs').append(el('p', 'empty', data.runs.length ? 'No runs match this filter.' : 'No runs yet. The next synced check will appear here.'));
  for (const run of runs) {
    const failed = run.status === 'error';
    const details = el('details', 'run'); details.dataset.id = run.id; details.open = openIds.has(run.id);
    const summary = el('summary');
    const icon = el('span', `status-icon${failed ? ' warn' : ''}`, failed ? '!' : '✓'); icon.setAttribute('aria-hidden', 'true');
    const title = el('div'); title.append(el('div', 'run-title', fullTime(run.started_at)));
    const seconds = (date(run.finished_at) - date(run.started_at)) / 1000;
    title.append(el('div', 'run-subtitle', `${run.results.length} checks${Number.isFinite(seconds) && seconds >= 0 ? ` · ${seconds.toFixed(1)}s duration` : ''}`));
    const end = el('div', 'run-end'); end.append(el('span', `badge${failed ? ' warn' : ''}`, failed ? 'Has errors' : 'Successful'));
    end.append(el('span', '', relative(run.started_at)), el('span', 'chevron', '⌄'));
    summary.append(icon, title, end); details.append(summary);
    const results = el('ul', 'results');
    for (const result of run.results) {
      const item = el('li'); const row = el('div', 'result-top');
      row.append(el('strong', '', result.symbol || 'Unknown symbol'), el('span', '', labels[result.status] || result.status || 'Unknown status'));
      item.append(row);
      if (Number.isFinite(result.price)) item.append(el('p', '', `Price: ${result.price.toLocaleString(undefined, { maximumFractionDigits: 4 })}`));
      if (result.quoted_at) item.append(el('p', '', `Quote: ${fullTime(result.quoted_at)}`));
      if (result.error) item.append(el('p', '', result.error));
      results.append(item);
    }
    if (!run.results.length) results.append(el('li', '', 'No individual checks recorded.'));
    details.append(results); $('runs').append(details);
  }
}
function render() {
  const latest = data.runs[0];
  $('last-check').textContent = latest ? relative(latest.finished_at) : 'No runs';
  const age = latest ? Date.now() - date(latest.finished_at) : 0;
  $('last-detail').textContent = !latest ? 'Awaiting first check-in' : age > 90 * 60000 ? 'No check-in for over 90 minutes' : latest.status === 'error' ? 'Latest run has errors' : 'Latest run successful';
  $('run-count').textContent = data.runs.length;
  const errors = data.runs.filter(r => r.status === 'error').length;
  $('run-detail').textContent = `${errors} with errors · latest ${data.runs.length}`;
  $('alert-count').textContent = data.alerts.length;
  renderRuns();
  $('alerts').replaceChildren();
  if (!data.alerts.length) $('alerts').append(el('p', 'empty', 'All quiet for now. Notifications will appear here when the monitor sends an alert.'));
  for (const alert of data.alerts) {
    const item = el('article', 'alert'); const header = el('div', 'alert-head');
    const time = el('time', '', fullTime(alert.sent_at)); time.dateTime = alert.sent_at;
    header.append(el('strong', '', 'Alert sent'), time);
    item.append(header, el('p', 'alert-message', alert.message)); $('alerts').append(item);
  }
}
async function refresh() {
  if (busy) return;
  busy = true; $('refresh').disabled = true;
  try {
    const response = await fetch('/api/activity', { signal: AbortSignal.timeout(15000) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Unable to load activity.');
    if (!Array.isArray(body.runs) || !Array.isArray(body.alerts)) throw new Error('Unexpected activity response.');
    data = body; render(); $('error').hidden = true;
    $('connection').textContent = `Updated ${fullTime(body.fetched_at)}`;
  } catch (error) {
    $('error').textContent = `${error.name === 'TimeoutError' ? 'The connection timed out.' : error.message} ${data ? 'Showing previously loaded activity.' : 'Use Refresh to try again.'}`;
    $('error').hidden = false; $('connection').textContent = data ? 'Connection interrupted · saved view' : 'Unable to connect';
    if (!data) for (const id of ['runs', 'alerts']) $(id).replaceChildren(el('p', 'empty', 'Activity could not be loaded.'));
  } finally {
    busy = false; $('refresh').disabled = false;
    $('runs').setAttribute('aria-busy', 'false'); $('alerts').setAttribute('aria-busy', 'false');
  }
}
$('timezone').textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
$('refresh').addEventListener('click', refresh);
$('filter').addEventListener('change', () => { if (data) renderRuns(); });
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();
