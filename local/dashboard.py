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

load_dotenv()
logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Research Scout - Local Approval")
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
BUSY = False
CATEGORIES = {"opportunities", "patents", "news"}


def db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def check(request: Request):
    if request.headers.get("X-Scout-Token") != TOKEN:
        raise HTTPException(403, "Invalid dashboard token")


class Approval(BaseModel):
    categories: list[str]
    max_records: int = Field(default=50, ge=1, le=100)
    minimum_score: int = Field(default=7, ge=0, le=10)


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
    return {"pending": findings, "runs": runs, "worker_busy": BUSY, "progress": progress, "review_counts": review_counts, "status_counts": status_counts, "retryable_count": retryable_count, "summaries": summaries}


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


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RESEARCH SCOUT // CONTROL</title>
<style>
:root{color-scheme:dark;--bg:#080b09;--panel:#101612;--line:#25402c;--green:#8bf6a3;--muted:#789784;--orange:#ffb45c;--red:#ff6870;--white:#d7e8d9}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--white);font:13px Consolas,'Cascadia Code',monospace}button,select,input{font:inherit}button{cursor:pointer;border:1px solid #4b8b5d;background:#17341f;color:var(--green);padding:9px 13px;border-radius:5px}button:hover{background:#24552f}button:disabled{opacity:.35;cursor:not-allowed}.secondary{border-color:#485b4d;background:#171e19;color:var(--white)}.danger{color:var(--orange);border-color:#785b32}header{display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:15px 20px;background:#0c120e}h1{font-size:18px;color:var(--green);margin:0;letter-spacing:2px}h2{font-size:12px;color:var(--green);letter-spacing:1px;margin:0 0 14px}small,.muted{color:var(--muted)}.badge{border:1px solid #315a3b;border-radius:4px;padding:5px 8px;color:var(--green);font-size:11px}.badge.warn{border-color:#6a4d26;color:var(--orange)}.wrap{max-width:1440px;margin:auto;padding:16px}.stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-bottom:12px}.stat,.panel{background:var(--panel);border:1px solid var(--line);border-radius:7px}.stat{padding:14px}.stat .value{font-size:26px;font-weight:bold;color:var(--green);margin:5px 0}.stat .label{color:var(--muted);font-size:11px;text-transform:uppercase}.stat.orange .value{color:var(--orange)}.stat.red .value{color:var(--red)}.layout{display:grid;grid-template-columns:minmax(330px,1.15fr) minmax(300px,1fr);gap:12px}.panel{padding:15px;min-width:0}.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.between{justify-content:space-between}.checks{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0}.checks label{cursor:pointer}input[type=checkbox]{accent-color:#70de88}select{background:#0b120d;color:var(--white);border:1px solid var(--line);padding:8px;border-radius:4px}.fields{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:12px 0 16px}.fields label{display:flex;align-items:center;gap:8px}.progressbar{height:9px;background:#202b22;border:1px solid var(--line);border-radius:9px;overflow:hidden;margin:14px 0 10px}.progressbar>div{height:100%;background:var(--green);width:0;transition:width .3s}.numbers{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px}.scroll{max-height:260px;overflow:auto}.item{padding:9px 2px;border-bottom:1px solid #1d3022;display:flex;gap:12px;align-items:flex-start}.item .type{min-width:84px;color:var(--orange);font-size:11px;text-transform:uppercase}.item a{color:var(--white);text-decoration:none;overflow-wrap:anywhere}.item a:hover{color:var(--green);text-decoration:underline}.mono{white-space:pre-wrap;overflow-wrap:anywhere}#message{min-height:24px;color:var(--orange);margin-top:12px}.compact{padding:7px 0;border-bottom:1px solid #1d3022}.footer{color:var(--muted);font-size:11px;margin-top:12px}.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);margin-right:7px}.pulse.off{background:var(--orange)}@media(max-width:960px){.stats{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr}}@media(max-width:520px){.stats{grid-template-columns:repeat(2,1fr)}header{padding:12px}.wrap{padding:10px}.stat .value{font-size:21px}}
</style></head><body>
<header><div><h1>&gt; RESEARCH_SCOUT<span style="color:var(--orange)">_</span></h1><small>LOCAL CONTROL PANEL // QUICK MODE // QWEN3 1.7B</small></div><div class="row"><span class="badge" id="cloud">CONNECTING</span><span class="badge warn" id="ai">AI IDLE</span><button class="secondary" id="refresh">↻ REFRESH</button></div></header>
<main class="wrap"><div class="stats"><div class="stat"><div class="label">Pending total*</div><div class="value" id="pending">--</div></div><div class="stat"><div class="label">Opportunities*</div><div class="value" id="opportunities">--</div></div><div class="stat"><div class="label">Patents*</div><div class="value" id="patents">--</div></div><div class="stat"><div class="label">News*</div><div class="value" id="news">--</div></div><div class="stat orange"><div class="label">Needs review</div><div class="value" id="review">--</div></div><div class="stat red"><div class="label">Rejected</div><div class="value" id="rejected">--</div></div></div>
<div class="row" style="margin:0 0 12px;gap:12px;color:var(--muted)"><span id="reviewedCount">Reviewed: --</span><span id="retryableCount">Failed without success: --</span><span id="runCount">Runs: --</span></div><div class="layout"><div class="panel"><h2>01 // APPROVAL QUEUE</h2><div class="muted">Only explicit approval loads the local model. Nothing runs on page open.</div><div class="checks"><label><input type="checkbox" value="opportunities" checked> Opportunities</label><label><input type="checkbox" value="patents" checked> Patents</label><label><input type="checkbox" value="news" checked> News</label></div><div class="fields"><label>Records <select id="limit"><option>5</option><option>20</option><option selected>50</option><option>100</option></select></label><label>Min score <select id="score"><option>3</option><option>5</option><option selected>7</option><option>9</option></select></label></div><div class="row"><button id="approve">▶ APPROVE & RUN</button><span class="muted" id="approvalHint">Ready for approval</span></div><div id="message" role="status"></div></div>
<div class="panel"><h2>02 // EXECUTION MONITOR</h2><div class="row between"><strong id="runTitle">NO ACTIVE RUN</strong><span class="badge" id="runState">IDLE</span></div><div class="progressbar"><div id="bar"></div></div><div class="numbers" id="progressText">Waiting for approved run.</div><div class="footer" id="recentRuns">Recent runs: --</div></div>
<div class="panel"><div class="row between"><h2>03 // PENDING FINDINGS</h2><small>Most recent 20 of first 100</small></div><div class="scroll" id="items">Loading...</div></div>
<div class="panel"><h2>04 // DISCORD DELIVERY & RESULTS</h2><div class="muted">Final run summaries</div><div class="scroll" id="summaries">Loading...</div><div class="row" style="margin-top:12px"><button class="secondary" id="retry">↻ RETRY UNSENT</button></div><div class="footer" id="reviewcounts">Review totals: --</div></div></div><div class="footer">* Pending counts reflect the latest 100 pending records returned by the existing API, not necessarily the entire database. Refreshes every 5 seconds. Localhost only.</div></main>
<script>
const token='__TOKEN__';const headers={'X-Scout-Token':token};let busy=false;const el=id=>document.getElementById(id);const esc=s=>String(s??'');
async function refresh(){try{const r=await fetch('/api/status',{headers,cache:'no-store'});if(!r.ok)throw Error('Status HTTP '+r.status);const d=await r.json();el('cloud').textContent='CLOUD ONLINE';el('cloud').className='badge';busy=!!d.worker_busy;el('ai').textContent=busy?'AI RUNNING':'AI IDLE';el('ai').className=busy?'badge':'badge warn';el('approve').disabled=busy;el('approvalHint').textContent=busy?'Processing approved batch':'No automatic inference';const c={opportunities:0,patents:0,news:0};for(const x of (d.pending||[])){if(x.category in c)c[x.category]++}el('pending').textContent=(d.pending||[]).length;for(const k of Object.keys(c))el(k).textContent=c[k];el('reviewedCount').textContent='Reviewed: '+((d.status_counts||{}).reviewed||0);el('retryableCount').textContent='Failed without success: '+(d.retryable_count||0);el('runCount').textContent='Runs: '+(d.runs||[]).length+' recent';el('review').textContent=(d.review_counts||{}).needs_review||0;el('rejected').textContent=(d.review_counts||{}).rejected||0;el('reviewcounts').textContent='Recent classifications: '+(Object.entries(d.review_counts||{}).map(([k,v])=>k+' '+v).join(' · ')||'none');const p=d.progress;if(p){el('runTitle').textContent='RUN #'+p.run_id;el('runState').textContent='RUNNING';const n=(p.completed||0)+(p.failed||0);el('bar').style.width=(p.total?Math.min(100,Math.round(n/p.total*100)):0)+'%';el('progressText').textContent=n+'/'+p.total+' finished · '+(p.running||0)+' active · '+(p.waiting||0)+' waiting · '+(p.failed||0)+' failed'}else{el('runTitle').textContent='NO ACTIVE RUN';el('runState').textContent='IDLE';el('bar').style.width='0%';el('progressText').textContent='No approved job currently processing.'}el('recentRuns').textContent='Recent runs: '+(d.runs||[]).map(x=>'#'+x.id+' '+x.status).join(' | ');el('items').replaceChildren();for(const x of (d.pending||[]).slice(0,20)){const row=document.createElement('div');row.className='item';const tag=document.createElement('span');tag.className='type';tag.textContent=x.category;const link=document.createElement('a');link.textContent=x.title||'Untitled';if(/^https?:\/\//.test(x.url||'')){link.href=x.url;link.target='_blank';link.rel='noopener noreferrer'}row.append(tag,link);el('items').append(row)}if(!(d.pending||[]).length)el('items').textContent='No pending findings. To intentionally re-analyze reviewed records, run database/01_requeue_existing.sql first (after stopping the worker).';el('summaries').replaceChildren();for(const s of d.summaries||[]){const row=document.createElement('div');row.className='compact';row.textContent='RUN #'+s.run_id+' · '+s.delivery_status+' · attempts '+s.attempts+(s.last_error?' · '+s.last_error:'');el('summaries').append(row)}if(!(d.summaries||[]).length)el('summaries').textContent='No summaries recorded.'}catch(e){el('cloud').textContent='CLOUD ERROR';el('cloud').className='badge warn';el('message').textContent='Status refresh failed: '+e.message}}
el('refresh').onclick=refresh;el('retry').onclick=async()=>{el('retry').disabled=true;try{const r=await fetch('/api/retry-summaries',{method:'POST',headers});if(!r.ok)throw Error(await r.text());el('message').textContent='Retry result: '+JSON.stringify(await r.json());await refresh()}catch(e){el('message').textContent='Retry failed: '+e.message}finally{el('retry').disabled=false}};
el('approve').onclick=async()=>{const categories=[...document.querySelectorAll('input[type=checkbox]:checked')].map(x=>x.value);if(!categories.length){el('message').textContent='Select at least one category.';return}if(!confirm('Approve local Qwen inference for '+categories.join(', ')+'?'))return;el('approve').disabled=true;try{const r=await fetch('/api/approve',{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({categories,max_records:+el('limit').value,minimum_score:+el('score').value})});const d=await r.json();if(!r.ok)throw Error(JSON.stringify(d));el('message').textContent='Approved run #'+(d.run_id||'?')+' · '+(d.count||0)+' findings';await refresh()}catch(e){el('message').textContent='Approval failed: '+e.message}finally{if(!busy)el('approve').disabled=false}};
refresh();setInterval(refresh,5000);
</script></body></html>
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
