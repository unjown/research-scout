"""Research Scout localhost approval dashboard. Does not load Ollama on startup."""
import logging
from collections import Counter
import os
import secrets
import threading
import webbrowser
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from supabase import create_client
from local.worker import run_approved, retry_pending_summaries
from local.audience_profiles import get_audience_profile, load_audience_profiles
from local.settings_manager import SettingsUpdate, clear_gemini_key, gemini_key_configured, load_settings, sanitized, save_settings, set_gemini_key
from local.gemini_provider import check_gemini_model

load_dotenv()
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="RS.ai - Local Approval", description="RS.ai dashboard metadata")
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
BUSY = False
CATEGORIES = {"opportunities", "patents", "news"}


def db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def check(request: Request):
    if request.headers.get("X-Scout-Token") != TOKEN:
        raise HTTPException(403, "Invalid dashboard token")
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
        raise HTTPException(403, "Invalid dashboard origin")


def admin_check(request: Request):
    check(request)
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.headers.get("origin") and request.headers.get("referer") is None:
        raise HTTPException(403, "Origin check failed")


class Approval(BaseModel):
    categories: list[str]
    max_records: int = Field(default=50, ge=1, le=100)
    minimum_score: int = Field(default=0, ge=0, le=10, description="Legacy field; inclusive policy ignores it")


class GeminiKey(BaseModel):
    key: str = Field(min_length=1, max_length=512)


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML.replace("__TOKEN__", TOKEN)


@app.get("/api/status")
def status(request: Request):
    check(request)
    client = db()
    findings = client.table("findings").select("id,category,title,url,discovered_at").eq("status", "pending").order("discovered_at", desc=True).limit(100).execute().data
    runs = client.table("local_analysis_runs").select("id,status,approved_at,finished_at,error_message").order("id", desc=True).limit(5).execute().data
    active = next((r for r in runs if r['status'] == 'running'), None)
    progress = None
    if active:
        entries = client.table('local_analysis_items').select('finding_id,status').eq('run_id', active['id']).execute().data
        by_status = Counter(x['status'] for x in entries)
        progress = {'run_id': active['id'], 'total': len(entries), 'completed': by_status['completed'],
                    'failed': by_status['failed'], 'running': by_status['running'],
                    'waiting': by_status['approved']}
    # Historical counters are separate from the pending queue.
    historical = client.table('findings').select('id,status,review_status').limit(1000).execute().data
    review_counts = dict(Counter(x['review_status'] for x in historical if x.get('review_status')))
    status_counts = dict(Counter(x['status'] for x in historical))
    retryable_items = client.table('local_analysis_items').select('finding_id,status').eq('status', 'failed').limit(1000).execute().data
    retryable_ids = {x['finding_id'] for x in retryable_items}
    completed_items = client.table('local_analysis_items').select('finding_id,status').eq('status', 'completed').limit(1000).execute().data
    completed_ids = {x['finding_id'] for x in completed_items}
    retryable_count = len(retryable_ids - completed_ids)
    summaries = client.table('local_run_summaries').select('run_id,delivery_status,attempts,last_error').order('run_id', desc=True).limit(10).execute().data
    try:
        classified = client.table('findings').select(
            'id,category,title,url,review_status,discord_status,ai_score,ai_reason,'
            'importance_score,evidence_quality,verification_status,uncertainty_labels'
        ).eq('status', 'reviewed').order('analyzed_at', desc=True).limit(40).execute().data
    except Exception:
        # Extended score columns appear after the inclusive-policy migration.
        classified = client.table('findings').select(
            'id,category,title,url,review_status,discord_status,ai_score,ai_reason'
        ).eq('status', 'reviewed').order('analyzed_at', desc=True).limit(40).execute().data
    dashboard_counts = {
        "pending": status_counts.get("pending", 0),
        "published": review_counts.get("published", 0),
        "excluded": review_counts.get("rejected", 0),
        "uncertain": review_counts.get("needs_review", 0),
        "failed": retryable_count,
    }
    return {"pending": findings, "classified": classified, "runs": runs, "worker_busy": BUSY, "progress": progress, "review_counts": review_counts, "status_counts": status_counts, "dashboard_counts": dashboard_counts, "retryable_count": retryable_count, "summaries": summaries}


@app.get("/api/audiences")
def audiences():
    """Read-only audience configuration for dashboard readiness."""
    profiles = load_audience_profiles()
    return {"default": "developer", "profiles": {key: value.model_dump() for key, value in profiles.items()}}


@app.get("/api/settings")
def settings(request: Request):
    admin_check(request)
    return sanitized()


@app.patch("/api/settings")
def update_settings(request: Request, config: SettingsUpdate):
    admin_check(request)
    current = load_settings()
    saved = save_settings(config)
    return {"settings": sanitized(), "restart_required": saved.dashboard_port != current.dashboard_port}


@app.put("/api/settings/gemini-key")
def update_gemini_key(request: Request, payload: GeminiKey):
    admin_check(request)
    set_gemini_key(payload.key)
    return {"gemini_key_configured": True}


@app.delete("/api/settings/gemini-key")
def delete_gemini_key(request: Request):
    admin_check(request)
    clear_gemini_key()
    return {"gemini_key_configured": False}


@app.post("/api/settings/test-connection")
def test_connection(request: Request):
    admin_check(request)
    config = load_settings()
    return {"local": "not tested", "gemini": check_gemini_model(config.gemini_model) if gemini_key_configured() else {"available": False, "error": "Gemini API key is not configured"}}


@app.get("/api/audiences/{profile_id}")
def audience(profile_id: str):
    """Return one profile; unknown identifiers safely select Developer."""
    return get_audience_profile(profile_id).model_dump()


@app.post("/api/retry-summaries")
def retry_summaries(request: Request):
    check(request)
    return retry_pending_summaries()


@app.post("/api/approve")
def approve(request: Request, config: Approval):
    global BUSY
    check(request)
    if not config.categories or not set(config.categories).issubset(CATEGORIES):
        raise HTTPException(400, "Choose valid categories")
    with LOCK:
        if BUSY:
            raise HTTPException(409, "Another run is active")
        BUSY = True
    try:
        client = db()
        candidates = client.table("findings").select("id,category").eq("status", "pending").in_("category", config.categories).order("discovered_at", desc=False).limit(config.max_records).execute().data
        if not candidates:
            with LOCK:
                BUSY = False
            return {"message": "No pending findings", "count": 0}
        run = client.table("local_analysis_runs").insert({"categories": config.categories,
            "requested_count": len(candidates), "minimum_score": config.minimum_score}).execute().data[0]
        run_id = run["id"]
        client.table("local_analysis_items").insert([{"run_id": run_id, "finding_id": item["id"]} for item in candidates]).execute()
        def task():
            global BUSY
            try:
                run_approved(run_id)
            finally:
                with LOCK:
                    BUSY = False
        threading.Thread(target=task, daemon=True).start()
        return {"message": "Approved", "run_id": run_id, "count": len(candidates)}
    except Exception:
        with LOCK:
            BUSY = False
        raise


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="application-name" content="RS.ai"><meta name="theme-color" content="#0B1220"><title>RS.ai // Control</title>
<style>
:root{color-scheme:dark;--bg:#0B1220;--panel:#142337;--line:#263B52;--accent:#22D3EE;--text:#F1F5F9;--muted:#94A3B8;--success:#10B981;--warning:#FBBF24;--error:#F87171;--info:#38BDF8}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px Consolas,'Cascadia Code',monospace}button,select,input{font:inherit}button{cursor:pointer;border:1px solid var(--accent);background:#123144;color:var(--accent);padding:9px 13px;border-radius:5px}button:hover{background:#193d52}button:disabled{opacity:.35;cursor:not-allowed}.secondary{border-color:#40566D;background:#17283A;color:var(--text)}.danger{color:var(--warning);border-color:#785b32}header{display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:15px 20px;background:#0D1828}h1{font-size:20px;color:var(--text);margin:0;letter-spacing:0;font-weight:800}.logo-ai{color:var(--accent)}h2{font-size:12px;color:var(--accent);letter-spacing:1px;margin:0 0 14px}small,.muted{color:var(--muted)}.badge{border:1px solid #31566A;border-radius:4px;padding:5px 8px;color:var(--accent);font-size:11px}.badge.warn{border-color:#6a4d26;color:var(--warning)}.wrap{max-width:1440px;margin:auto;padding:16px}.stats{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:10px;margin-bottom:12px}.stat,.panel{background:var(--panel);border:1px solid var(--line);border-radius:7px}.stat{padding:14px}.stat .value{font-size:26px;font-weight:bold;color:var(--accent);margin:5px 0}.stat .label{color:var(--muted);font-size:11px;text-transform:uppercase}.stat.orange .value{color:var(--warning)}.stat.red .value{color:var(--error)}.layout{display:grid;grid-template-columns:minmax(330px,1.15fr) minmax(300px,1fr);gap:12px}.panel{padding:15px;min-width:0}.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.between{justify-content:space-between}.checks{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0}.checks label{cursor:pointer}input[type=checkbox]{accent-color:var(--success)}select{background:var(--bg);color:var(--text);border:1px solid var(--line);padding:8px;border-radius:4px}.fields{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:12px 0 16px}.fields label{display:flex;align-items:center;gap:8px}.progressbar{height:9px;background:#202D3C;border:1px solid var(--line);border-radius:9px;overflow:hidden;margin:14px 0 10px}.progressbar>div{height:100%;background:var(--success);width:0;transition:width .3s}.numbers{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px}.scroll{max-height:260px;overflow:auto}.item{padding:9px 2px;border-bottom:1px solid #26394D;display:flex;gap:12px;align-items:flex-start}.item .type{min-width:84px;color:var(--info);font-size:11px;text-transform:uppercase}.item a{color:var(--text);text-decoration:none;overflow-wrap:anywhere}.item a:hover{color:var(--accent);text-decoration:underline}.mono{white-space:pre-wrap;overflow-wrap:anywhere}#message{min-height:24px;color:var(--warning);margin-top:12px}.compact{padding:7px 0;border-bottom:1px solid #26394D}.footer{color:var(--muted);font-size:11px;margin-top:12px}.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--success);margin-right:7px}.pulse.off{background:var(--warning)}@media(max-width:1200px){.stats{grid-template-columns:repeat(4,1fr)}}@media(max-width:960px){.stats{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr}}@media(max-width:520px){.stats{grid-template-columns:repeat(2,1fr)}header{padding:12px;align-items:flex-start;flex-direction:column}.header-controls{width:100%;justify-content:flex-start}.audience-control{margin-right:auto}.wrap{padding:10px}.stat .value{font-size:21px}}
</style><style>.modal{position:fixed;inset:0;background:#0009;display:grid;place-items:center;padding:12px;z-index:5}.modal[hidden]{display:none}.modal-card{background:var(--panel);border:1px solid var(--accent);border-radius:7px;padding:18px;width:min(900px,100%);max-height:92vh;overflow:auto}fieldset{border:1px solid var(--line);border-radius:5px;padding:12px;display:grid;gap:10px}legend{color:var(--accent)}fieldset label{display:grid;gap:4px;color:var(--muted)}fieldset input,fieldset select{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--line);padding:8px;border-radius:4px}.settings-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}#settingsError{color:var(--error);min-height:22px}@media(max-width:760px){.settings-grid{grid-template-columns:1fr}}</style></head><body>
<header><div><h1>RS<span class="logo-ai">.ai</span></h1></div><div class="row header-controls"><label class="row audience-control" for="audience"><small>Audience:</small><select id="audience" aria-label="Audience"><option value="developer">Developer</option><option value="business">Business</option><option value="educator">Educator</option></select></label><span class="badge" id="cloud">CONNECTING</span><span class="badge warn" id="ai">AI IDLE</span><button class="secondary" id="settingsButton" aria-haspopup="dialog">⚙ SETTINGS</button><button class="secondary" id="refresh">↻ REFRESH</button></div></header>
<div id="settingsModal" class="modal" role="dialog" aria-modal="true" aria-labelledby="settingsTitle" hidden><div class="modal-card"><div class="row between"><h2 id="settingsTitle">SETTINGS</h2><button class="secondary" id="settingsClose" aria-label="Close settings">×</button></div><form id="settingsForm"><div class="settings-grid"><fieldset><legend>AI</legend><label>Provider<select name="provider"><option value="local">Local AI</option><option value="gemini">Gemini API</option></select></label><label>Gemini model<input name="gemini_model"></label><label>Local model<input name="local_model"></label><label>Context size<input name="context_size" type="number" min="256"></label><label>Maximum output tokens<input name="max_output_tokens" type="number" min="1"></label><label>Temperature<input name="temperature" type="number" min="0" max="2" step="0.1"></label><div class="row"><button type="button" class="secondary" id="keyButton">Configure Gemini key</button><span class="muted" id="keyState"></span></div></fieldset><fieldset><legend>CONNECTION</legend><label>Local endpoint<input name="local_endpoint"></label><label>Connection timeout<input name="connection_timeout" type="number" min="1"></label><label>Dashboard port<input name="dashboard_port" type="number" min="1" max="65535"></label><label><input name="check_services" type="checkbox"> Check services on request</label><button type="button" class="secondary" id="testConnection">CHECK AVAILABILITY</button><span class="muted" id="connectionState"></span></fieldset><fieldset><legend>RESEARCH</legend><label>Default audience<select name="audience"><option value="developer">Developer</option><option value="business">Business</option><option value="educator">Educator</option></select></label><label>Default batch size<input name="batch_size" type="number" min="1" max="100"></label><label>Categories<input name="categories" readonly></label><p class="muted">Active relevance policy: publish scores 2–10; exclude 0–1.</p></fieldset></div><div id="settingsError" role="alert"></div><div class="row" style="justify-content:flex-end"><button type="button" class="secondary" id="settingsCancel">CANCEL</button><button>SAVE</button></div></form></div></div>
<main class="wrap"><div class="stats"><div class="stat"><div class="label">Pending*</div><div class="value" id="pending">--</div></div><div class="stat"><div class="label">Published</div><div class="value" id="published">--</div></div><div class="stat"><div class="label">Opportunities*</div><div class="value" id="opportunities">--</div></div><div class="stat"><div class="label">Patents*</div><div class="value" id="patents">--</div></div><div class="stat"><div class="label">News*</div><div class="value" id="news">--</div></div><div class="stat orange"><div class="label">Uncertain</div><div class="value" id="review">--</div></div><div class="stat red"><div class="label">Excluded</div><div class="value" id="rejected">--</div></div><div class="stat red"><div class="label">Failed</div><div class="value" id="failed">--</div></div></div>
<div class="row" style="margin:0 0 12px;gap:12px;color:var(--muted)"><span id="reviewedCount">Classified: --</span><span id="retryableCount">Failed without success: --</span><span id="runCount">Runs: --</span></div><div class="layout"><div class="panel"><h2>01 // INCLUSIVE DISCOVERY QUEUE</h2><div class="muted">Only explicit approval loads the local model. Valid findings publish unless confidently unrelated.</div><div class="checks"><label><input type="checkbox" value="opportunities" checked> Opportunities</label><label><input type="checkbox" value="patents" checked> Patents</label><label><input type="checkbox" value="news" checked> News</label></div><div class="fields"><label>Records <select id="limit"><option>5</option><option>20</option><option selected>50</option><option>100</option></select></label><select id="score" hidden><option selected>0</option></select></div><div class="row"><button id="approve">▶ APPROVE & RUN</button><span class="muted" id="approvalHint">Ready for approval</span></div><div id="message" role="status"></div></div>
<div class="panel"><h2>02 // EXECUTION MONITOR</h2><div class="row between"><strong id="runTitle">NO ACTIVE RUN</strong><span class="badge" id="runState">IDLE</span></div><div class="progressbar"><div id="bar"></div></div><div class="numbers" id="progressText">Waiting for approved run.</div><div class="footer" id="recentRuns">Recent runs: --</div></div>
<div class="panel"><div class="row between"><h2>03 // FINDINGS</h2><small>Pending plus recent classified results</small></div><div class="scroll" id="items">Loading...</div></div>
<div class="panel"><h2>04 // DISCORD DELIVERY & RESULTS</h2><div class="muted">Final run summaries</div><div class="scroll" id="summaries">Loading...</div><div class="row" style="margin-top:12px"><button class="secondary" id="retry">↻ RETRY UNSENT</button></div><div class="footer" id="reviewcounts">Review totals: --</div></div></div><div class="footer">* Pending counts reflect the latest 100 pending records returned by the existing API, not necessarily the entire database. Refreshes every 5 seconds. Localhost only.</div></main>
<script>
const token = '__TOKEN__';
const headers = {'X-Scout-Token': token};
const audienceStorageKey = 'rs.ai.audience';
const validAudiences = new Set(['developer', 'business', 'educator']);
let busy = false;

function selectedAudience() {
  try {
    const stored = localStorage.getItem(audienceStorageKey);
    return validAudiences.has(stored) ? stored : 'developer';
  } catch { return 'developer'; }
}

async function loadAudienceProfile(profileId) {
  const response = await fetch(`/api/audiences/${encodeURIComponent(profileId)}`, {headers, cache: 'no-store'});
  if (!response.ok) throw new Error(`Audience profile HTTP ${response.status}`);
  return response.json();
}

async function configureAudience(profileId, persist = true) {
  const safeId = validAudiences.has(profileId) ? profileId : 'developer';
  const select = requiredElement('audience');
  select.value = safeId;
  if (persist) {
    try { localStorage.setItem(audienceStorageKey, safeId); } catch { /* Keep the in-page selection. */ }
  }
  try {
    const profile = await loadAudienceProfile(safeId);
    select.dataset.profileId = profile.id;
    select.setAttribute('aria-label', `Audience: ${profile.display_name}`);
  } catch (error) {
    select.value = 'developer';
    select.dataset.profileId = 'developer';
    try { localStorage.setItem(audienceStorageKey, 'developer'); } catch { /* Storage is optional. */ }
    console.error('Audience configuration could not be loaded', error);
  }
}

function requiredElement(id) {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Dashboard template is missing #${id}`);
  return node;
}

function optionalElement(id) {
  return document.getElementById(id);
}

function createFindingRow(item, stateText, details = '') {
  const row = document.createElement('div');
  row.className = 'item';
  const state = document.createElement('span');
  state.className = 'type';
  state.textContent = stateText;
  const body = document.createElement('div');
  const link = document.createElement('a');
  link.textContent = item.title || 'Untitled';
  if (/^https?:\/\//.test(item.url || '')) {
    link.href = item.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
  }
  body.append(link);
  if (details) {
    const note = document.createElement('small');
    note.style.display = 'block';
    note.textContent = details;
    body.append(note);
  }
  row.append(state, body);
  return row;
}

function renderStatus(data) {
  const pending = data.pending ?? [];
  const classified = data.classified ?? [];
  const runs = data.runs ?? [];
  const summaries = data.summaries ?? [];
  const counts = data.dashboard_counts ?? {};
  const reviewCounts = data.review_counts ?? {};
  const statusCounts = data.status_counts ?? {};

  busy = Boolean(data.worker_busy);
  requiredElement('ai').textContent = busy ? 'AI RUNNING' : 'AI IDLE';
  requiredElement('ai').className = busy ? 'badge' : 'badge warn';
  requiredElement('approve').disabled = busy;
  requiredElement('approvalHint').textContent = busy ? 'Processing approved batch' : 'No automatic inference';

  const pendingByCategory = {opportunities: 0, patents: 0, news: 0};
  for (const item of pending) {
    if (Object.hasOwn(pendingByCategory, item.category)) pendingByCategory[item.category] += 1;
  }
  requiredElement('pending').textContent = counts.pending ?? pending.length;
  requiredElement('published').textContent = counts.published ?? 0;
  requiredElement('opportunities').textContent = pendingByCategory.opportunities;
  requiredElement('patents').textContent = pendingByCategory.patents;
  requiredElement('news').textContent = pendingByCategory.news;
  requiredElement('review').textContent = counts.uncertain ?? reviewCounts.needs_review ?? 0;
  requiredElement('rejected').textContent = counts.excluded ?? reviewCounts.rejected ?? 0;
  requiredElement('failed').textContent = counts.failed ?? data.retryable_count ?? 0;
  requiredElement('reviewedCount').textContent = `Classified: ${statusCounts.reviewed ?? 0}`;
  requiredElement('retryableCount').textContent = `Failed without success: ${data.retryable_count ?? 0}`;
  requiredElement('runCount').textContent = `Runs: ${runs.length} recent`;
  requiredElement('reviewcounts').textContent = 'Recent classifications: ' +
    (Object.entries(reviewCounts).map(([key, value]) => `${key} ${value}`).join(' · ') || 'none');

  const progress = data.progress;
  if (progress) {
    requiredElement('runTitle').textContent = `RUN #${progress.run_id}`;
    requiredElement('runState').textContent = 'RUNNING';
    const finished = (progress.completed ?? 0) + (progress.failed ?? 0);
    requiredElement('bar').style.width = `${progress.total ? Math.min(100, Math.round(finished / progress.total * 100)) : 0}%`;
    requiredElement('progressText').textContent = `${finished}/${progress.total} finished · ${progress.running ?? 0} active · ${progress.waiting ?? 0} waiting · ${progress.failed ?? 0} failed`;
  } else {
    requiredElement('runTitle').textContent = 'NO ACTIVE RUN';
    requiredElement('runState').textContent = 'IDLE';
    requiredElement('bar').style.width = '0%';
    requiredElement('progressText').textContent = 'No approved job currently processing.';
  }
  requiredElement('recentRuns').textContent = 'Recent runs: ' + runs.map(run => `#${run.id} ${run.status}`).join(' | ');

  const items = requiredElement('items');
  items.replaceChildren();
  for (const item of pending.slice(0, 20)) items.append(createFindingRow(item, item.category));
  for (const item of classified) {
    const details = `Relevance ${item.ai_score ?? '--'}/10 · Importance ${item.importance_score ?? '--'}/10 · ${item.ai_reason || 'No reason recorded'}`;
    items.append(createFindingRow(item, (item.review_status || 'classified').replace('_', ' '), details));
  }
  if (!pending.length && !classified.length) items.textContent = 'No findings returned by the status API.';

  const summaryList = requiredElement('summaries');
  summaryList.replaceChildren();
  for (const summary of summaries) {
    const row = document.createElement('div');
    row.className = 'compact';
    row.textContent = `RUN #${summary.run_id} · ${summary.delivery_status} · attempts ${summary.attempts}${summary.last_error ? ` · ${summary.last_error}` : ''}`;
    summaryList.append(row);
  }
  if (!summaries.length) summaryList.textContent = 'No summaries recorded.';
}

async function refresh() {
  let data;
  try {
    const response = await fetch('/api/status', {headers, cache: 'no-store'});
    if (!response.ok) throw new Error(`Status HTTP ${response.status}`);
    data = await response.json();
  } catch (error) {
    requiredElement('cloud').textContent = 'CLOUD ERROR';
    requiredElement('cloud').className = 'badge warn';
    requiredElement('message').textContent = `Status API failed: ${error.message}`;
    return;
  }

  requiredElement('cloud').textContent = 'CLOUD ONLINE';
  requiredElement('cloud').className = 'badge';
  try {
    renderStatus(data);
    const message = optionalElement('message');
    if (message?.textContent?.startsWith('Dashboard render failed:')) message.textContent = '';
  } catch (error) {
    requiredElement('message').textContent = `Dashboard render failed: ${error.message}`;
    console.error('Dashboard rendering failed', error);
  }
}

requiredElement('refresh').onclick = refresh;
requiredElement('audience').addEventListener('change', event => configureAudience(event.target.value));
requiredElement('retry').onclick = async () => {
  const button = requiredElement('retry');
  button.disabled = true;
  try {
    const response = await fetch('/api/retry-summaries', {method: 'POST', headers});
    if (!response.ok) throw new Error(await response.text());
    requiredElement('message').textContent = `Retry result: ${JSON.stringify(await response.json())}`;
    await refresh();
  } catch (error) {
    requiredElement('message').textContent = `Retry failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
};
requiredElement('approve').onclick = async () => {
  const categories = [...document.querySelectorAll('input[type=checkbox]:checked')].map(node => node.value);
  if (!categories.length) {
    requiredElement('message').textContent = 'Select at least one category.';
    return;
  }
  if (!confirm(`Approve local Qwen inference for ${categories.join(', ')}?`)) return;
  const button = requiredElement('approve');
  button.disabled = true;
  try {
    const response = await fetch('/api/approve', {
      method: 'POST',
      headers: {...headers, 'Content-Type': 'application/json'},
      body: JSON.stringify({categories, max_records: Number(requiredElement('limit').value), minimum_score: 0}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(JSON.stringify(result));
    requiredElement('message').textContent = `Approved run #${result.run_id ?? '?'} · ${result.count ?? 0} findings`;
    await refresh();
  } catch (error) {
    requiredElement('message').textContent = `Approval failed: ${error.message}`;
  } finally {
    if (!busy) button.disabled = false;
  }
};

const settingsModal = optionalElement('settingsModal');
const settingsForm = optionalElement('settingsForm');
function closeSettings() { if (settingsForm?.dataset.dirty === '1' && !confirm('Discard unsaved changes?')) return; settingsModal.hidden = true; }
async function openSettings() { const response = await fetch('/api/settings', {headers}); if (!response.ok) throw new Error('Settings unavailable'); const data = await response.json(); for (const [key, value] of Object.entries(data)) { const field = settingsForm.elements[key]; if (field) field.type === 'checkbox' ? field.checked = value : field.value = Array.isArray(value) ? value.join(',') : value; } optionalElement('keyState').textContent = data.gemini_key_configured ? 'configured' : 'not configured'; settingsForm.dataset.dirty = '0'; settingsModal.hidden = false; settingsForm.elements.provider.focus(); }
optionalElement('settingsButton').onclick = () => openSettings().catch(error => optionalElement('message').textContent = error.message);
optionalElement('settingsClose').onclick = closeSettings; optionalElement('settingsCancel').onclick = closeSettings;
settingsForm?.addEventListener('input', () => settingsForm.dataset.dirty = '1');
settingsForm?.addEventListener('submit', async event => { event.preventDefault(); const data = Object.fromEntries(new FormData(settingsForm)); data.check_services = settingsForm.elements.check_services.checked; data.categories = String(data.categories || '').split(',').map(x => x.trim()).filter(Boolean); for (const key of ['context_size','max_output_tokens','dashboard_port','connection_timeout','batch_size']) data[key] = Number(data[key]); data.temperature = Number(data.temperature); const response = await fetch('/api/settings', {method:'PATCH', headers:{...headers,'Content-Type':'application/json'}, body:JSON.stringify(data)}); const result = await response.json(); if (!response.ok) { optionalElement('settingsError').textContent = result.detail || 'Invalid settings'; return; } optionalElement('settingsError').textContent = result.restart_required ? 'Saved. Restart required for the new dashboard port.' : 'Saved.'; settingsForm.dataset.dirty = '0'; });
optionalElement('keyButton').onclick = async () => { const field = optionalElement('geminiKeyInput'); field.hidden = false; field.focus(); const key = field.value; if (!key) return; const response = await fetch('/api/settings/gemini-key', {method:'PUT', headers:{...headers,'Content-Type':'application/json'}, body:JSON.stringify({key})}); field.value = ''; field.hidden = true; optionalElement('keyState').textContent = response.ok ? 'configured' : 'key update failed'; };
const clearKeyButton = document.createElement('button'); clearKeyButton.type = 'button'; clearKeyButton.className = 'secondary'; clearKeyButton.textContent = 'CLEAR KEY'; optionalElement('keyButton').after(clearKeyButton); clearKeyButton.onclick = async () => { const response = await fetch('/api/settings/gemini-key', {method:'DELETE', headers}); optionalElement('keyState').textContent = response.ok ? 'not configured' : 'clear failed'; };
optionalElement('testConnection').onclick = async () => { const response = await fetch('/api/settings/test-connection', {method:'POST', headers}); optionalElement('connectionState').textContent = response.ok ? 'checked' : 'check failed'; };
document.addEventListener('keydown', event => { if (event.key === 'Escape' && settingsModal && !settingsModal.hidden) closeSettings(); });
settingsModal?.addEventListener('click', event => { if (event.target === settingsModal) closeSettings(); });
configureAudience(selectedAudience(), false);
refresh();
setInterval(refresh, 5000);
</script><input id="geminiKeyInput" type="password" autocomplete="new-password" hidden></body></html>
"""

def main():
    import uvicorn
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("Dashboard must bind to localhost only")
    port = int(os.getenv("DASHBOARD_PORT", "8765"))
    print(f"Open http://127.0.0.1:{port} (AI remains off until approval)")
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
