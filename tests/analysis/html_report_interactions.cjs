// Execute the generated report's actual JS against a real DOM; stub chart painting only.
// NODE_PATH=/path/to/node_modules node tests/analysis/html_report_interactions.cjs REPORT.html
const {JSDOM}=require('jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
// CSS rendering is outside this DOM interaction test (jsdom cannot parse modern daisyUI CSS).
const dom=new JSDOM(html.replace(/<style[^>]*>[\s\S]*?<\/style>/g,''),{runScripts:'outside-only'}),w=dom.window,d=w.document,plots=new Map();
w.Plotly={react(id,data,layout){plots.set(id,{data,layout});return Promise.resolve()}};
const scripts=[...d.querySelectorAll('script')];
w.eval(scripts.at(-1).textContent);
const $=id=>d.getElementById(id),change=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new w.Event('change'))},input=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new w.Event('input'))};
const D=JSON.parse($('data').textContent),n=D.profiles.length;
assert(n>1,'Use a multi-agent run for this interaction test');
assert.equal($('agent-cards').querySelectorAll('.card').length,Math.min(n,24));
assert.equal($('back-agents').hidden,true);
$('agent-cards').querySelector('button').click();
assert.equal($('agent-cards').querySelectorAll('.card').length,1);
assert.equal($('back-agents').hidden,false);
assert.match($('agent-context').textContent,/Viewing/);
const chosen=$('agent').value;
assert([...$('activity').querySelectorAll('tr')].every(tr=>tr.children[1].textContent.includes(chosen)));
$('back-agents').click();
assert.equal($('agent').value,'All');assert.equal($('agent-cards').querySelectorAll('.card').length,Math.min(n,24));
assert.equal($('back-agents').hidden,true);
// Repeated enter/exit, both card button and selector, must restore overview.
for(let i=0;i<3;i++){change('agent',D.profiles.at(-1).agent_id);assert.equal($('agent-cards').querySelectorAll('.card').length,1);$('back-agents').click();assert.equal($('agent').value,'All')}
assert.equal($('value').value,'All');
assert(plots.get('probes').data.length>0);
assert(plots.get('probes').data.every(t=>t.mode==='lines+markers'));
const allCount=plots.get('probes').data.length;
change('channel','declared');assert(plots.get('probes').data.every(t=>t.name.endsWith('/ declared')));assert(plots.get('probes').data.length<allCount);
const axis=[...$('value').options].find(o=>o.value!=='All').value;
change('value',axis);assert(plots.get('probes').data.every(t=>t.name.includes(` / ${axis} / `)));
change('value','All');change('channel','All');assert.equal(plots.get('probes').data.length,allCount);
$('from').value='7';$('to').value='5';$('apply').click();assert.match($('window-status').textContent,/Enter whole ticks/);
$('from').value='';$('apply').click();assert.match($('window-status').textContent,/Enter whole ticks/);
$('from').value='0';$('to').value='0';$('apply').click();assert(plots.get('probes').data.every(t=>t.x.every(x=>x===0)));
$('latest').click();assert.match($('window-status').textContent,/Showing lifecycle ticks/);
// Tick ranges with no data produce a visible empty-state annotation.
$('from').value='100000';$('to').value='100001';$('apply').click();assert.equal(plots.get('probes').data.length,0);assert.equal(plots.get('probes').layout.annotations[0].text,'No recorded observations');
$('from').value='0';$('to').value='10000';$('apply').click();
// Search + paging, including zero hits and disabled controls.
input('activity-search','no-such-action-9x71');assert.equal($('activity').children.length,0);assert.equal($('activity-next').disabled,true);
input('activity-search','');assert.equal($('activity').children.length,Math.min(20,D.events.length));
if(D.events.length>20){const before=$('activity').firstElementChild.textContent;$('activity-next').click();assert.notEqual($('activity').firstElementChild.textContent,before);$('activity-prev').click();assert.equal($('activity').firstElementChild.textContent,before)}
input('stream','events');input('search','no-such-evidence-9x71');assert.equal($('records').textContent,'No matching records');assert.equal($('next').disabled,true);
input('search','');if(D.events.length>25){const before=$('records').textContent;$('next').click();assert.notEqual($('records').textContent,before);$('prev').click();assert.equal($('records').textContent,before)}
change('agent',D.profiles[0].agent_id);change('channel','private');input('search','no-such-evidence');
$('reset-view').click();assert.equal($('agent').value,'All');assert.equal($('value').value,'All');assert.equal($('channel').value,'All');assert.equal($('search').value,'');assert.equal($('activity-search').value,'');assert.equal($('agent-cards').querySelectorAll('.card').length,Math.min(n,24));
assert.match($('institution-details').textContent,/Constitutional rules/);
assert.match($('institution-details').textContent,/Quorum/);
const versions=[...new Set(D.constitution_history.map(r=>r.version||r.constitution?.version))];
for(const version of versions){const records=D.constitution_history.filter(r=>(r.version||r.constitution?.version)===version);
if(records.length>1&&records.every(r=>JSON.stringify(r.constitution)===JSON.stringify(records[0].constitution))){
assert.equal([...$('institution-details').querySelectorAll('h3')].filter(e=>e.textContent===`Constitution · ${version}`).length,1);
assert.match($('institution-details').textContent,new RegExp(`Recorded ${records.length} times`));
}}
for(const id of ['resources','probes','latency','tokens','governance','outcomes','profiles-chart']){
const layout=plots.get(id).layout;assert.equal(layout.legend.yanchor,'bottom');assert(layout.legend.y>1);assert(layout.margin.t>=110);assert(layout.margin.b>=60);assert.equal(layout.legend.maxheight,80);
}
assert([...$('institution-details').querySelectorAll('details')].every(e=>!e.open),'Raw records should be collapsed');
assert.equal($('agent-cards').querySelectorAll('.card')[0].textContent.includes(D.profiles[0].initial.persona_summary),true);
console.log('PASS: real DOM explore/back cycles, agent selector, All values, line modes, channels, tick validation, empty states, search, pagination, descriptions and readable, deduplicated constitution; legend placement above plots');
dom.window.close();
