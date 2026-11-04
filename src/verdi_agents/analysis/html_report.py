"""Standalone, read-only society report: python -m verdi_agents.analysis.html_report RUN_DIR.

Chart/UI libraries are MIT licensed; their pinned distributions retain license headers.
Default output embeds cached/downloaded assets. --cdn explicitly requires connectivity.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from urllib.request import urlopen

ASSETS = {
    'plotly': 'https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3.3.0/plotly.min.js',
    'daisyui': 'https://cdn.jsdelivr.net/npm/daisyui@5.5.14/daisyui.css',
}
STREAMS = ('resources', 'probes', 'events', 'model_calls', 'meetings',
           'sanctions', 'constitution_history', 'action_reasons')


def load_run(run_dir: Path) -> dict:
    """Read only named artifacts, projecting public profile fields from saved snapshots; excluding private memory and raw tapes.

    An incomplete final JSONL record in a running run is disclosed and skipped;
    malformed complete records fail loudly instead of silently losing evidence.
    """
    data: dict = {'name': run_dir.name, 'warnings': []}
    for name in STREAMS:
        path = run_dir / f'{name}.jsonl'
        rows = []
        if path.exists():
            with path.open(encoding='utf-8') as stream:
                for number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        if not line.endswith('\n'):
                            data['warnings'].append(f'{path.name}: incomplete last record skipped')
                            break
                        raise ValueError(f'{path.name}:{number}: invalid JSON') from None
                    if not isinstance(row, dict):
                        raise ValueError(f'{path.name}:{number}: expected object')
                    rows.append(row)
        else:
            data['warnings'].append(f'{path.name}: unavailable')
        data[name] = rows
    for name in ('manifest', 'metrics', 'health', 'checkpoint', 'run_meta'):
        path = run_dir / f'{name}.json'
        data[name] = json.loads(path.read_text()) if path.exists() else None
    # Saved state is authoritative: the scenario file may have changed since the run.
    snapshots = run_dir / 'snapshots'
    paths = [p for p in snapshots.glob('*.json') if p.name == 't00.json'
             or (p.stem.startswith('after_tick')
                 and p.stem.removeprefix('after_tick').isdigit())]
    def snapshot_order(path: Path) -> int:
        return -1 if path.name == 't00.json' else int(path.stem.removeprefix('after_tick'))
    paths.sort(key=snapshot_order)
    data['profiles'] = []
    data['snapshot_sources'] = []
    data['initial_is_baseline'] = bool(paths and paths[0].name == 't00.json')
    if paths:
        initial = json.loads(paths[0].read_text())
        final = json.loads(paths[-1].read_text())
        fields = ('agent_id', 'name', 'display_name', 'role', 'description',
                  'persona_summary', 'declared_values', 'resources', 'rights',
                  'social_edges', 'under_sanction', 'idle_ticks', 'public_signals')
        for aid, agent in initial.get('agents', {}).items():
            data['profiles'].append({
                'agent_id': aid,
                'initial': {k: agent[k] for k in fields if k in agent},
                'latest': {k: final.get('agents', {}).get(aid, {}).get(k)
                           for k in fields if k in final.get('agents', {}).get(aid, {})},
            })
        data['snapshot_sources'] = [paths[0].name, paths[-1].name]
    else:
        data['warnings'].append('Agent descriptions unavailable: no saved state snapshots')
    if not any(data[name] for name in STREAMS):
        raise ValueError('No supported run records found')
    return data


def safe_json(data: dict) -> str:
    """Prevent artifact text from closing the HTML script element."""
    return json.dumps(data, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')


def write_report(run_dir: Path, output: Path, *, cdn: bool = False,
                 asset_dir: Path | None = None) -> Path:
    if output.suffix.lower() != '.html':
        raise ValueError('Output must be .html; refusing to overwrite other files')
    if output.exists() and not output.read_text(encoding='utf-8').startswith(
        '<!doctype html>\n<html lang="en" data-theme="light">'
    ):
        raise ValueError('Refusing to overwrite an existing nonreport file')
    data = load_run(run_dir)
    if cdn:
        assets = (f'<style></style><link rel="stylesheet" href="{ASSETS["daisyui"]}">'
                  f'<script src="{ASSETS["plotly"]}"></script>')
        data['warnings'].append('CDN mode: internet required to load chart and UI libraries')
    else:
        cache = asset_dir or Path.home() / '.cache' / 'verdi-agents' / 'html-report'
        cache.mkdir(parents=True, exist_ok=True)
        contents = {}
        for name, url in ASSETS.items():
            path = cache / url.rsplit('/', 1)[-1]
            if not path.exists():
                try:
                    with urlopen(url, timeout=60) as response:
                        content = response.read().decode('utf-8')
                except OSError as exc:
                    raise RuntimeError(
                        f'Cannot download {name}. Provide --asset-dir with pinned assets '
                        'or use --cdn for an online preview.'
                    ) from exc
                path.write_text(content, encoding='utf-8')
            contents[name] = path.read_text(encoding='utf-8')
        assets = (f'<style>{contents["daisyui"]}</style>'
                  f'<script>{contents["plotly"]}</script>')
    document = TEMPLATE.replace('__TITLE__', html.escape(data['name']))
    document = document.replace('__ASSETS__', assets).replace('__DATA__', safe_json(data))
    sources = [run_dir / f'{name}.jsonl' for name in STREAMS]
    sources += [run_dir / f'{name}.json' for name in
                ('manifest', 'metrics', 'health', 'checkpoint', 'run_meta')]
    if output.resolve() in {p.resolve() for p in sources} or output.resolve().is_relative_to(
        (run_dir / 'snapshots').resolve()
    ):
        raise ValueError('Output must be a new file; refusing to overwrite run evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding='utf-8')
    return output


TEMPLATE = '''<!doctype html>
<html lang="en" data-theme="light"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verdi · __TITLE__</title>__ASSETS__
<style>
body{max-width:1480px;margin:auto;padding:32px;font-family:system-ui;background:var(--color-base-200);color:var(--color-base-content)}
header{padding:32px;border-radius:20px;background:var(--color-base-100);border-top:5px solid var(--color-primary)}
.eyebrow{font-size:12px;letter-spacing:3px;font-weight:700;color:var(--color-primary)}
.agent-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:16px}
.card,.chart,.stats{background:var(--color-base-100)}
.card-body{gap:10px}.card-title{margin:0}.badge{margin-right:6px}
.stats{display:flex;flex-wrap:wrap;width:100%;margin:22px 0}.stat{flex:1;min-width:140px}
.stat-value{font-size:28px}.detail{padding:18px;border-radius:12px;background:var(--color-base-100);margin:12px 0}
.agent-avatar{width:48px;height:48px;border-radius:16px;background:var(--color-primary);color:var(--color-primary-content);display:grid;place-items:center;font-size:22px;font-weight:700}
.muted{opacity:.65;font-size:13px}.overflow-x-auto{overflow-x:auto}
td{vertical-align:top}.reason{min-width:280px;max-width:600px;white-space:pre-wrap}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}
[hidden]{display:none!important}
header,section{margin-bottom:24px}h1{font-size:30px;font-weight:700}
h2{font-size:21px;font-weight:600;margin:20px 0 8px}h3{font-size:16px;font-weight:600;margin:12px 0 6px}p{margin:8px 0}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,540px),1fr));gap:18px}
.chart{height:450px;border:1px solid var(--color-base-300);border-radius:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto}
label{display:flex;gap:8px;align-items:center}.input{width:125px}
</style>
<header><div class="eyebrow">VERDI / SOCIETY OBSERVATORY</div><h1>__TITLE__</h1>
<p>Meet the agents. Follow their choices. Inspect the evidence.</p>
<p id="summary"></p><p id="warnings"></p>
<p>Descriptive single-run report. Probe channels measure different constructs; scores do not establish
internal beliefs, causal influence, or recovery. Missing observations remain missing.
Event ticks are reducer transition counts, shown separately from lifecycle ticks.</p><div id="stats" class="stats"></div></header>
<nav><a class="btn" href="#society">Agents</a><a class="btn" href="#trajectories">Trajectories</a>
<a class="btn" href="#activity-section">Actions &amp; reasons</a><a class="btn" href="#institutions">Institutions</a>
<a class="btn" href="#evidence">Raw evidence</a><button id="reset-view" class="btn">Reset view</button></nav>
<section id="society"><div class="controls"><h2>The society</h2>
<button id="back-agents" class="btn" hidden>← Back to all agents</button>
<span id="agent-context" class="badge badge-outline"></span></div><p class="muted" id="profile-source"></p>
<div id="agent-cards" class="agent-grid"></div>
<div class="charts"><div><h2>Social observation network</h2><p class="muted">Arrows show configured observation links, not inferred influence. Large networks show the first 80 participants and 300 arrows; exact links remain in profiles.</p><div id="network" class="chart"></div></div>
<div><h2>Assigned starting values</h2><p class="muted">Saved initial profile; distinct from measured probe scores.</p><div id="profiles-chart" class="chart"></div></div></div></section>
<section id="trajectories">
<h2>Trajectories</h2>
<p class="muted">Zoom/hover/export with Plotly. Each line renders at most 2,000 points using min/max buckets. Probe lines connect measured points; intermediate scores were not measured.</p>
<div class="charts">
<div><h3>Resources · lifecycle tick</h3>
<div class="controls" style="margin-bottom:8px">
<label>Agent <select id="agent" class="select" aria-label="Agent"></select></label>
<label>Resource <select id="resource" class="select" aria-label="Resource"></select></label>
<label>From <input id="from" class="input" type="number" value="0" min="0"></label>
<label>To <input id="to" class="input" type="number" min="0"></label>
<button id="apply" class="btn">Apply</button><span id="window-status" role="status"></span><button id="latest" class="btn">Latest 100</button>
</div>
<div id="resources" class="chart"></div></div>
<div><h3>Value probes · lifecycle tick</h3>
<div class="controls" style="margin-bottom:8px">
<label>Agent <select id="probe-agent" class="select" aria-label="Probe agent"></select></label>
<label>Value <select id="value" class="select" aria-label="Value"></select></label>
<label>Channel <select id="channel" class="select" aria-label="Probe channel"></select></label>
</div>
<div id="probes" class="chart"></div></div>
<div><h3>Governance · lifecycle tick</h3><div id="governance" class="chart"></div></div>
<div><h3>Ledger outcomes · transition tick</h3><div id="outcomes" class="chart"></div></div>
</div>
<details style="margin-top:18px">
<summary style="cursor:pointer;font-weight:600;color:var(--color-primary)">⚙️ Infrastructure metrics (latency, tokens)</summary>
<div class="charts" style="margin-top:12px">
<div><h3>Model latency · lifecycle tick</h3><div id="latency" class="chart"></div></div>
<div><h3>Prompt tokens · lifecycle tick</h3><div id="tokens" class="chart"></div></div>
</div>
</details>
</section>
<section id="activity-section"><h2>Actions &amp; stated reasons</h2>
<p class="muted">Reasons are agent statements, joined by sealed event ID. Ledger transition ticks are separate from lifecycle ticks.</p>
<div class="controls"><input id="activity-search" class="input" placeholder="Search actions" aria-label="Search actions">
<button id="activity-prev" class="btn">Previous</button><button id="activity-next" class="btn">Next</button><span id="activity-page"></span></div>
<div class="overflow-x-auto"><table class="table table-zebra"><thead><tr><th>Event / transition</th><th>Agent</th><th>Decision</th><th>Action / arguments</th><th>Stated reason</th></tr></thead><tbody id="activity"></tbody></table></div></section>
<section id="institutions"><h2>Meetings &amp; constitutional history</h2><p class="muted">Full recorded proposals, votes, abstentions and rule versions; 10 records per page.</p>
<div class="controls"><button id="institution-prev" class="btn">Previous</button><button id="institution-next" class="btn">Next</button><span id="institution-page"></span></div>
<div id="institution-details"></div></section>
<section id="evidence"><h2>Evidence inspector</h2><p>Lifecycle window and agent filters apply to records with those fields.
Events use their independent transition clock. Constitution/reason records have no lifecycle tick.</p>
<div class="controls"><select id="stream" class="select" aria-label="Artifact"></select>
<input id="search" class="input" placeholder="Search evidence" aria-label="Search evidence">
<button id="prev" class="btn">Previous</button><button id="next" class="btn">Next</button>
<span id="page"></span></div><pre id="records"></pre></section>
<details><summary>Provenance / health / metrics</summary><pre id="meta"></pre></details>
<footer><p>Charts: Plotly.js 3.3.0 (MIT). Controls: daisyUI 5.5.14 (MIT).
Generated snapshot; regenerate to include new ticks. Embedded artifact text is research data.</p></footer>
<script id="data" type="application/json">__DATA__</script><script>
const D=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
const unique=xs=>[...new Set(xs)].sort();
function options(id, xs){for(const x of xs){const o=document.createElement('option');o.value=x;const profile=(D.profiles||[]).find(p=>p.agent_id===x);o.textContent=id==='agent'&&profile?`${profile.initial.display_name||profile.initial.name||x} · ${profile.initial.role||x}`:x;$(id).append(o)}}
const agents=unique([...D.probes.map(r=>r.agent_id),...D.resources.flatMap(r=>Object.keys(r.agents||{})),
...D.model_calls.map(r=>r.subject),...(D.profiles||[]).map(r=>r.agent_id)].filter(Boolean));
options('agent',['All',...agents]);
options('probe-agent',['All',...agents]);
options('resource',unique(D.resources.flatMap(r=>[...Object.keys(r.world_resources||{}),
...Object.values(r.agents||{}).flatMap(a=>Object.keys(a))])));
options('value',['All',...unique(D.probes.flatMap(r=>Object.keys(r.value_vector||{})))]);
options('channel',['All','declared','private','public']);
options('stream',['profiles','events','probes','resources','meetings','sanctions','constitution_history','action_reasons','model_calls']);
let maxTick=0;for(const key of ['resources','probes','model_calls','meetings','sanctions'])
for(const r of D[key])if(Number.isFinite(r.tick))maxTick=Math.max(maxTick,r.tick);
$('to').value=maxTick;
$('summary').textContent=`${D.events.length} ledger events · ${D.model_calls.length} model calls · ${D.probes.length} probes · lifecycle tick through ${maxTick}`;
$('warnings').textContent=D.warnings.join(' · ');
$('meta').textContent=JSON.stringify(Object.fromEntries(['manifest','health','metrics','checkpoint','run_meta'].map(k=>[k,D[k]])),null,2);
const subject=r=>r.agent_id||r.actor_id||r.subject||r.subject_id||r.appellant_id;
function windowRows(rows){const a=$('agent').value,lo=Number($('from').value),hi=Number($('to').value);
return rows.filter(r=>(r.tick==null||(r.tick>=lo&&r.tick<=hi))&&
(a==='All'||!subject(r)||subject(r)===a))}
// Min/max buckets preserve extrema and endpoints without a new chart framework.
function bounded(points){if(points.length<=2000)return points;
const out=[points[0]],size=Math.ceil((points.length-2)/999);
for(let i=1;i<points.length-1;i+=size){const b=points.slice(i,Math.min(i+size,points.length-1));
let lo=b[0],hi=b[0];for(const p of b){if(p[1]<lo[1])lo=p;if(p[1]>hi[1])hi=p}
out.push(...(lo[0]<=hi[0]?[lo,hi]:[hi,lo]))}out.push(points.at(-1));return out}
function traces(groups,mode='lines'){return [...groups].map(([name,ps])=>{ps.sort((a,b)=>a[0]-b[0]);const p=bounded(ps);
return {name,type:'scatter',mode,x:p.map(x=>x[0]),y:p.map(x=>x[1]),connectgaps:false}})}
function group(rows,name,value){const g=new Map();for(const r of rows){const y=value(r);if(!Number.isFinite(y))continue;
const n=name(r);if(!g.has(n))g.set(n,[]);g.get(n).push([r.tick,y])}return g}
const chartLegend={orientation:'h',x:0,xanchor:'left',y:1.08,yanchor:'bottom',maxheight:80};
function chart(id,data,y,life=true){data=data.filter(t=>t.x?.length);const layout={margin:{t:110,r:25,b:75,l:70},height:450,
xaxis:{title:{text:life?'Lifecycle tick':'Reducer transition tick'}},yaxis:{title:{text:y}},
legend:chartLegend,hovermode:'closest',paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)'};
if(life)layout.xaxis.range=[Number($('from').value),Number($('to').value)];
if(!data.length)layout.annotations=[{text:'No recorded observations',xref:'paper',yref:'paper',x:.5,y:.5,showarrow:false}];
Plotly.react(id,data,layout,{responsive:true,displaylogo:false});}
let page=0;
function inspect(){let rows=$('stream').value==='events'?D.events:windowRows(D[$('stream').value]);
const q=$('search').value.toLowerCase(),a=$('agent').value;
if($('stream').value==='events'&&a!=='All')rows=rows.filter(r=>r.actor_id===a);
if(q)rows=rows.filter(r=>JSON.stringify(r).toLowerCase().includes(q));
const pages=Math.max(1,Math.ceil(rows.length/25));page=Math.min(page,pages-1);
$('records').textContent=rows.slice(page*25,page*25+25).map(r=>JSON.stringify(r,null,2)).join('\\n\\n')||'No matching records';
$('page').textContent=`Page ${page+1}/${pages} · ${rows.length} records`;
$('prev').disabled=page===0;$('next').disabled=page===pages-1;}
function render(){society();activity();institutions();if(!window.Plotly){$('warnings').textContent+=' · Chart library unavailable';inspect();return}
const lo=Number($('from').value),hi=Number($('to').value);
const a=$('agent').value,res=$('resource').value,g=new Map();
for(const r of windowRows(D.resources)){if(Number.isFinite(r.world_resources?.[res])){
if(!g.has('World pool'))g.set('World pool',[]);g.get('World pool').push([r.tick,r.world_resources[res]])}
for(const [id,balances] of Object.entries(r.agents||{}))if((a==='All'||id===a)&&Number.isFinite(balances[res])){
if(!g.has(id))g.set(id,[]);g.get(id).push([r.tick,balances[res]])}}
chart('resources',traces(g),res||'Balance');
const probeGroups=new Map(),axes=$('value').value==='All'?unique(D.probes.flatMap(r=>Object.keys(r.value_vector||{}))):[$('value').value];
const pa=$('probe-agent').value;
for(const r of D.probes.filter(r=>(r.tick>=lo&&r.tick<=hi)&&(pa==='All'||r.agent_id===pa)&&($('channel').value==='All'||r.channel===$('channel').value)))for(const axis of axes){const score=r.value_vector?.[axis];
if(!Number.isFinite(score))continue;const name=`${r.agent_id} / ${axis} / ${r.channel}`;
if(!probeGroups.has(name))probeGroups.set(name,[]);probeGroups.get(name).push([r.tick,score])}
const probeTraces=traces(probeGroups,'lines+markers');
for(const t of probeTraces){t.line={dash:t.name.endsWith('/ private')?'dash':t.name.endsWith('/ public')?'dot':'solid'};t.marker={size:5}}
chart('probes',probeTraces,$('value').value==='All'?'Value scores':$('value').value||'Score');
const calls=windowRows(D.model_calls);
chart('latency',traces(group(calls,r=>r.purpose,r=>r.duration_ms),'lines+markers'),'Milliseconds');
chart('tokens',traces(group(calls,r=>r.purpose,r=>r.prompt_tokens),'lines+markers'),'Prompt tokens');
const gov=[...windowRows(D.meetings).map(r=>({tick:r.tick,label:r.outcome||'meeting'})),
...windowRows(D.sanctions).map(r=>({tick:r.tick,label:r.kind||r.event||r.record_type||'sanction record'}))].filter(r=>Number.isFinite(r.tick));
const shownGov=gov.length>2000?gov.filter((r,i)=>i%Math.ceil(gov.length/2000)===0):gov;
chart('governance',[{type:'scatter',mode:'markers',x:shownGov.map(r=>r.tick),y:shownGov.map(r=>r.label),marker:{size:12}}],'Recorded outcome');
const events=D.events.filter(r=>a==='All'||r.actor_id===a),counts=new Map();
for(const r of events){const key=r.kind;if(!counts.has(key))counts.set(key,[]);const ps=counts.get(key);ps.push([r.tick,ps.length+1])}
chart('outcomes',traces(counts),'Cumulative recorded events',false);inspect();}

function element(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(cls)e.className=cls;return e}
function clear(id){$(id).replaceChildren();return $(id)}
const profiles=D.profiles||[], profileById=new Map(profiles.map(p=>[p.agent_id,p]));
const label=id=>{const p=profileById.get(id)?.initial;return p?.display_name||p?.name||id};
const reasons=new Map(D.action_reasons.map(r=>[r.event_id,r.reason]));
function balances(r){return Object.entries(r||{}).map(([k,v])=>`${k}: ${v}`).join(' · ')||'Not recorded'}
function society(){
$('profile-source').textContent=D.snapshot_sources?.length?`Profiles from saved snapshots ${D.snapshot_sources.join(' → ')}. IDs are shown where names were not recorded.`:'Agent profiles were not recorded.';
const stats=clear('stats');for(const [title,value,desc] of [
['Agents',agents.length,'Recorded participants'],['Lifecycle ticks',maxTick+1,'Observed through t'+maxTick],
['Actions',D.events.filter(r=>r.kind.startsWith('action_')).length,'Accepted and rejected attempts'],
['Meetings',D.meetings.length,'Recorded proceedings'],['Replay',D.checkpoint?.replay_matches===true?'Matches':D.checkpoint?.replay_matches===false?'Mismatch':'Unknown','Recorded checkpoint result']]){
const box=element('div',null,'stat');box.append(element('div',title,'stat-title'),element('div',value,'stat-value'),element('div',desc,'stat-desc'));stats.append(box)}
const container=clear('agent-cards'),selected=$('agent').value;
$('back-agents').hidden=selected==='All';
$('agent-context').textContent=selected==='All'?'All agents':`Viewing ${label(selected)}`;
for(const p of profiles.filter(p=>selected==='All'||p.agent_id===selected).slice(0,24)){
const initial=p.initial,latest=p.latest,card=element('div',null,'card card-border'),body=element('div',null,'card-body');
body.append(element('div',label(p.agent_id).slice(0,2).toUpperCase(),'agent-avatar'),element('h3',label(p.agent_id),'card-title'));
body.append(element('p',`${p.agent_id} · ${initial.role||'Role not recorded'}`,'muted'),element('p',initial.description||initial.persona_summary||'Description not recorded'));
const badges=element('div');badges.append(element('span',latest.under_sanction===true?'Under sanction':latest.under_sanction===false?'Latest: unrestricted':'Sanction status not recorded','badge badge-outline'));
body.append(badges,element('p',(D.initial_is_baseline?'Starting resources · ':'First checkpoint resources · ')+balances(initial.resources)),element('p','Latest checkpoint · '+balances(latest.resources)),
element('p','Observes · '+(initial.social_edges||[]).map(label).join(', ')),
element('p','Latest rights · '+Object.entries(latest.rights||{}).map(([k,v])=>`${k}: ${v?'granted':'restricted'}`).join(' · ')));
const details=element('details'),summary=element('summary','Starting values & latest public signals');details.append(summary,element('pre',JSON.stringify({assigned_values:initial.declared_values,public_signals:latest.public_signals},null,2)));body.append(details);
const button=element('button','Explore this agent','btn');button.onclick=()=>{selectAgent(p.agent_id)};body.append(button);card.append(body);container.append(card)}
if(profiles.length>24&&selected==='All')container.append(element('p','Showing first 24 profiles; select an agent to inspect any participant.'));
if(!window.Plotly)return;
const visible=profiles.filter(p=>selected==='All'||p.agent_id===selected).slice(0,24),axes=unique(visible.flatMap(p=>Object.keys(p.initial.declared_values||{})));
Plotly.react('profiles-chart',visible.map(p=>({type:'scatterpolar',name:label(p.agent_id),r:axes.map(k=>p.initial.declared_values?.[k]??null),theta:axes,fill:'toself',connectgaps:false})),{height:450,margin:{t:110,b:60,l:70,r:70},polar:{radialaxis:{range:[0,1]}},legend:chartLegend},{responsive:true,displaylogo:false});
const nodes=profiles.slice(0,80),positions=new Map(nodes.map((p,i)=>[p.agent_id,[Math.cos(2*Math.PI*i/nodes.length),Math.sin(2*Math.PI*i/nodes.length)]])),x=[],y=[],arrows=[];
for(const p of nodes)for(const neighbor of p.initial.social_edges||[]){const a=positions.get(p.agent_id),b=positions.get(neighbor);if(!b)continue;
x.push(a[0],b[0],null);y.push(a[1],b[1],null);arrows.push({x:b[0]*.88+a[0]*.12,y:b[1]*.88+a[1]*.12,ax:a[0]*.88+b[0]*.12,ay:a[1]*.88+b[1]*.12,xref:'x',yref:'y',axref:'x',ayref:'y',showarrow:true,arrowhead:2,arrowwidth:1})}
Plotly.react('network',[{type:'scatter',mode:'lines',x,y,line:{color:'#c6cbd4',width:1},hoverinfo:'skip'},
{type:'scatter',mode:'markers+text',x:nodes.map(p=>positions.get(p.agent_id)[0]),y:nodes.map(p=>positions.get(p.agent_id)[1]),text:nodes.map(p=>`${label(p.agent_id)} · ${p.initial.role||''}`),textposition:'top center',marker:{size:22,color:nodes.map(p=>p.agent_id===selected?'#d67c32':'#5279a9')},hoverinfo:'text'}],
{height:450,margin:{t:45,b:40,l:65,r:65},showlegend:false,annotations:arrows.slice(0,300),xaxis:{visible:false,range:[-1.5,1.5]},yaxis:{visible:false,range:[-1.4,1.4]}},{responsive:true,displaylogo:false});
}
let actionPage=0,institutionPage=0;
function activity(){const a=$('agent').value,q=$('activity-search').value.toLowerCase();
const rows=D.events.filter(r=>(a==='All'||r.actor_id===a)&&(!q||JSON.stringify(r).toLowerCase().includes(q)||String(reasons.get(r.event_id)||'').toLowerCase().includes(q)));
const pages=Math.max(1,Math.ceil(rows.length/20));actionPage=Math.max(0,Math.min(actionPage,pages-1));const body=clear('activity');
for(const r of rows.slice(actionPage*20,actionPage*20+20)){const tr=element('tr');
for(const text of [`#${r.event_id} / t${r.tick}`,label(r.actor_id)||'Institution',r.decision||r.kind,`${r.payload?.action_kind||r.kind}\n${JSON.stringify(r.payload?.arguments||r.payload)}`,reasons.get(r.event_id)||'No reason recorded'])tr.append(element('td',text,'reason'));
body.append(tr)}$('activity-page').textContent=`${rows.length} records · page ${actionPage+1}/${pages}`;$('activity-prev').disabled=actionPage===0;$('activity-next').disabled=actionPage===pages-1;
}
function constitutionGroups(){const groups=new Map();
const canonical=value=>JSON.stringify(value,(key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
for(const r of D.constitution_history){const version=r.version||r.constitution?.version||'unknown',key=version+' / '+canonical(r.constitution||r);
if(!groups.has(key))groups.set(key,{title:`Constitution · ${version}`,record:r,records:[]});groups.get(key).records.push(r)}return [...groups.values()]}
function institutions(){const rows=[...windowRows(D.meetings).map(r=>({title:`Meeting at lifecycle tick ${r.tick} · ${r.outcome}`,record:r})),
...constitutionGroups(),
...windowRows(D.sanctions).map(r=>({title:`${r.record_type||'Governance'} · lifecycle tick ${r.tick} · ${label(subject(r))||''}`,record:r}))];
const pages=Math.max(1,Math.ceil(rows.length/10));institutionPage=Math.max(0,Math.min(institutionPage,pages-1));const container=clear('institution-details');
for(const r of rows.slice(institutionPage*10,institutionPage*10+10)){const d=element('div',null,'detail');d.append(element('h3',r.title,'card-title'));
const c=r.record.constitution;
if(c){
if(r.records.length>1)d.append(element('p',`Recorded ${r.records.length} times with identical constitution content.`,'muted'));
const list=element('dl');for(const [key,title] of [['effective_from_tick','Effective from tick'],['quorum','Quorum'],['amendment_threshold','Amendment threshold'],['sanction_policy','Sanction policy'],['appeal_policy','Appeal policy']]){
if(c[key]==null)continue;list.append(element('dt',title,'muted'),element('dd',typeof c[key]==='object'?Object.entries(c[key]).map(([k,v])=>`${k.replaceAll('_',' ')}: ${v}`).join(' · '):c[key]));}d.append(list);
d.append(element('h4','Constitutional rules'));
const rules=c.rules||{};for(const [key,value] of Object.entries(rules)){d.append(element('p',Array.isArray(rules)?String(value):`${key.replaceAll('_',' ')}: ${typeof value==='string'?value.replaceAll('_',' '):JSON.stringify(value)}`));}
if(!Object.keys(rules).length)d.append(element('p','No rule text recorded.'));
}else if(r.record.outcome){d.append(element('p','Outcome · '+r.record.outcome),element('p',`Proposals: ${(r.record.proposals||[]).length} · Vote records: ${(r.record.votes||[]).length} · Abstentions: ${r.record.abstentions??'not recorded'}`));
for(const proposal of r.record.proposals||[]){d.append(element('p',proposal.text||proposal.description||JSON.stringify(proposal)));}
}else{for(const [key,value] of Object.entries(r.record))if(typeof value!=='object')d.append(element('p',`${key.replaceAll('_',' ')}: ${value}`));}
const raw=element('details');raw.append(element('summary','Raw record'),element('pre',JSON.stringify(r.records?.length>1?r.records:r.record,null,2)));d.append(raw);container.append(d)}
if(!rows.length)container.append(element('p','No recorded governance proceedings.'));
$('institution-page').textContent=`${rows.length} records · page ${institutionPage+1}/${pages}`;$('institution-prev').disabled=institutionPage===0;$('institution-next').disabled=institutionPage===pages-1;
}
$('activity-search').oninput=()=>{actionPage=0;activity()};$('activity-prev').onclick=()=>{actionPage--;activity()};$('activity-next').onclick=()=>{actionPage++;activity()};
$('institution-prev').onclick=()=>{institutionPage--;institutions()};$('institution-next').onclick=()=>{institutionPage++;institutions()};
function selectAgent(id){$('agent').value=id;page=0;actionPage=0;institutionPage=0;render();$('society').scrollIntoView?.({block:'start'})}
$('back-agents').onclick=()=>selectAgent('All');
$('reset-view').onclick=()=>{$('value').value='All';$('channel').value='All';$('from').value=0;$('to').value=maxTick;
$('activity-search').value='';$('search').value='';$('stream').value='events';$('window-status').textContent='Showing the full recorded run';selectAgent('All')};

$('apply').onclick=()=>{const lo=Number($('from').value),hi=Number($('to').value);
if($('from').value===''||$('to').value===''||!Number.isInteger(lo)||!Number.isInteger(hi)||lo<0||hi<lo){$('window-status').textContent='Enter whole ticks ≥ 0, with From ≤ To.';return}
$('window-status').textContent=`Showing lifecycle ticks ${lo}–${hi}`;page=0;actionPage=0;institutionPage=0;render()};
$('latest').onclick=()=>{$('from').value=Math.max(0,maxTick-99);$('to').value=maxTick;$('apply').onclick()};
for(const id of ['agent','probe-agent','resource','value','channel'])$(id).onchange=()=>{page=0;actionPage=0;institutionPage=0;render()};
for(const id of ['stream','search'])$(id).oninput=()=>{page=0;inspect()};
$('prev').onclick=()=>{page--;inspect()};$('next').onclick=()=>{page++;inspect()};render();
</script></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--output', '-o', type=Path)
    parser.add_argument('--cdn', action='store_true', help='Online preview instead of offline embedding')
    parser.add_argument('--asset-dir', type=Path, help='Cache containing plotly.min.js and daisyui.css')
    args = parser.parse_args()
    output = args.output or args.run_dir / 'report.html'
    print(write_report(args.run_dir, output, cdn=args.cdn, asset_dir=args.asset_dir))


if __name__ == '__main__':
    main()
