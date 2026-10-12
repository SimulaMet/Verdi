"""Standalone, read-only society report: python -m verdi.analysis.html_report RUN_DIR.

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
    """Read only named artifacts, excluding private snapshots and raw prompt tapes.

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
        cache = asset_dir or Path.home() / '.cache' / 'verdi' / 'html-report'
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
body{max-width:1400px;margin:auto;padding:28px;font-family:system-ui}
header,section{margin-bottom:24px}h1{font-size:30px;font-weight:700}
h2{font-size:21px;font-weight:600;margin:20px 0 8px}p{margin:8px 0}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:14px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,540px),1fr));gap:18px}
.chart{height:370px;border:1px solid var(--color-base-300);border-radius:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto}
label{display:flex;gap:8px;align-items:center}.input{width:125px}
</style>
<header><h1>Verdi / __TITLE__</h1><p>Observable society trajectories · recorded evidence</p>
<p id="summary"></p><p id="warnings"></p>
<p>Descriptive single-run report. Probe channels measure different constructs; scores do not establish
internal beliefs, causal influence, or recovery. Missing observations remain missing.
Event ticks are reducer transition counts, shown separately from lifecycle ticks.</p></header>
<section class="controls">
<label>Agent <select id="agent" class="select" aria-label="Agent"></select></label>
<label>Resource <select id="resource" class="select" aria-label="Resource"></select></label>
<label>Value <select id="value" class="select" aria-label="Value"></select></label>
<label>From tick <input id="from" class="input" type="number" value="0" min="0"></label>
<label>To tick <input id="to" class="input" type="number" min="0"></label>
<button id="apply" class="btn">Apply window</button><button id="latest" class="btn">Latest 100 ticks</button>
</section>
<p>Zoom/hover/export with Plotly. Each line renders at most 2,000 points using min/max buckets;
zoom with the tick inputs to recover finer detail. Governance markers are sampled to at most
2,000. Exact records remain in the inspector.</p>
<section class="charts"><div><h2>Resources · lifecycle tick</h2><div id="resources" class="chart"></div></div>
<div><h2>Value probes · lifecycle tick</h2><div id="probes" class="chart"></div></div>
<div><h2>Model latency · lifecycle tick</h2><div id="latency" class="chart"></div></div>
<div><h2>Prompt tokens · lifecycle tick</h2><div id="tokens" class="chart"></div></div>
<div><h2>Governance · lifecycle tick</h2><div id="governance" class="chart"></div></div>
<div><h2>Ledger outcomes · transition tick</h2><div id="outcomes" class="chart"></div></div></section>
<section><h2>Evidence inspector</h2><p>Lifecycle window and agent filters apply to records with those fields.
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
function options(id, xs){for(const x of xs){const o=document.createElement('option');o.value=x;o.textContent=x;$(id).append(o)}}
const agents=unique([...D.probes.map(r=>r.agent_id),...D.resources.flatMap(r=>Object.keys(r.agents||{})),
...D.model_calls.map(r=>r.subject)].filter(Boolean));
options('agent',['All',...agents]);
options('resource',unique(D.resources.flatMap(r=>[...Object.keys(r.world_resources||{}),
...Object.values(r.agents||{}).flatMap(a=>Object.keys(a))])));
options('value',unique(D.probes.flatMap(r=>Object.keys(r.value_vector||{}))));
options('stream',['events','probes','resources','meetings','sanctions','constitution_history','action_reasons','model_calls']);
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
function chart(id,data,y,life=true){const layout={margin:{t:20,r:20,b:65,l:65},height:370,
xaxis:{title:{text:life?'Lifecycle tick':'Reducer transition tick'}},yaxis:{title:{text:y}},
legend:{orientation:'h'},hovermode:'closest'};
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
function render(){if(!window.Plotly){$('warnings').textContent+=' · Chart library unavailable';inspect();return}
const a=$('agent').value,res=$('resource').value,g=new Map();
for(const r of windowRows(D.resources)){if(Number.isFinite(r.world_resources?.[res])){
if(!g.has('World pool'))g.set('World pool',[]);g.get('World pool').push([r.tick,r.world_resources[res]])}
for(const [id,balances] of Object.entries(r.agents||{}))if((a==='All'||id===a)&&Number.isFinite(balances[res])){
if(!g.has(id))g.set(id,[]);g.get(id).push([r.tick,balances[res]])}}
chart('resources',traces(g),res||'Balance');
chart('probes',traces(group(windowRows(D.probes),r=>`${r.agent_id} / ${r.channel}`,
r=>r.value_vector?.[$('value').value]),'markers'),$('value').value||'Score');
const calls=windowRows(D.model_calls);
chart('latency',traces(group(calls,r=>r.purpose,r=>r.duration_ms),'markers'),'Milliseconds');
chart('tokens',traces(group(calls,r=>r.purpose,r=>r.prompt_tokens),'markers'),'Prompt tokens');
const gov=[...windowRows(D.meetings).map(r=>({tick:r.tick,label:r.outcome||'meeting'})),
...windowRows(D.sanctions).map(r=>({tick:r.tick,label:r.kind||r.event||r.record_type||'sanction record'}))].filter(r=>Number.isFinite(r.tick));
const shownGov=gov.length>2000?gov.filter((r,i)=>i%Math.ceil(gov.length/2000)===0):gov;
chart('governance',[{type:'scatter',mode:'markers',x:shownGov.map(r=>r.tick),y:shownGov.map(r=>r.label),marker:{size:12}}],'Recorded outcome');
const events=D.events.filter(r=>a==='All'||r.actor_id===a),counts=new Map();
for(const r of events){const key=r.kind;if(!counts.has(key))counts.set(key,[]);const ps=counts.get(key);ps.push([r.tick,ps.length+1])}
chart('outcomes',traces(counts),'Cumulative recorded events',false);inspect();}
$('apply').onclick=()=>{if(Number($('from').value)>Number($('to').value))return;page=0;render()};
$('latest').onclick=()=>{$('from').value=Math.max(0,maxTick-99);$('to').value=maxTick;page=0;render()};
for(const id of ['agent','resource','value'])$(id).onchange=()=>{page=0;render()};
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
