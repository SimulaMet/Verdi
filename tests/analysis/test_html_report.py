import json

import pytest

from verdi.analysis.html_report import load_run, safe_json, write_report


def test_partial_tail_and_missing_streams(tmp_path):
    (tmp_path / 'events.jsonl').write_text('{"tick": 1}\n{"tick":')
    data = load_run(tmp_path)
    assert data['events'] == [{'tick': 1}]
    assert any('incomplete' in w for w in data['warnings'])
    assert data['health'] is None
    (tmp_path / 'events.jsonl').write_text('broken\n')
    with pytest.raises(ValueError, match='events.jsonl:1'):
        load_run(tmp_path)


def test_script_injection_escape():
    value = {'reason': '</script><script>alert(1)</script>'}
    serialized = safe_json(value)
    assert '</script>' not in serialized
    assert json.loads(serialized) == value


def test_long_run_offline_generation_and_regeneration(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    rows = [{'tick': tick, 'agents': {'a': {'food': tick}}, 'world_resources': {}}
            for tick in range(10001)]
    (run / 'resources.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'plotly.min.js').write_text('/* local plotly */')
    (assets / 'daisyui.css').write_text('/* local daisyui */')
    output = run / 'report.html'
    write_report(run, output, asset_dir=assets)
    write_report(run, output, asset_dir=assets)
    text = output.read_text()
    assert 'src="https:' not in text and 'href="https:' not in text
    assert 'local plotly' in text
    assert '"tick": 10000' in text
    before = (run / 'resources.jsonl').read_text()
    with pytest.raises(ValueError, match='overwrite'):
        write_report(run, run / 'resources.jsonl', cdn=True)
    assert (run / 'resources.jsonl').read_text() == before


def test_output_cannot_overwrite_other_evidence(tmp_path):
    (tmp_path / 'events.jsonl').write_text('{"tick": 1}\n')
    private = tmp_path / 'prompts.jsonl'
    private.write_text('private evidence')
    with pytest.raises(ValueError, match='overwrite'):
        write_report(tmp_path, private, cdn=True)
    existing = tmp_path / 'notes.html'
    existing.write_text('existing evidence')
    with pytest.raises(ValueError, match='overwrite'):
        write_report(tmp_path, existing, cdn=True)
    assert private.read_text() == 'private evidence'
    assert existing.read_text() == 'existing evidence'


def test_javascript_long_window_filters_and_point_budget(tmp_path):
    import shutil
    import subprocess

    from verdi.analysis.html_report import TEMPLATE

    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for report behavior check')
    data = {name: [] for name in ('resources', 'probes', 'events', 'model_calls', 'meetings',
                                 'sanctions', 'constitution_history', 'action_reasons')}
    data.update(warnings=[], manifest=None, health=None, metrics=None,
                checkpoint=None, run_meta=None)
    data['resources'] = [{'tick': i, 'agents': {'a': {'food': i}}, 'world_resources': {}}
                         for i in range(10001)]
    data['profiles'] = [{'agent_id': 'a', 'initial': {'role': 'farmer',
                         'persona_summary': 'Grows food', 'declared_values': {'fairness': .7}},
                         'latest': {'resources': {'food': 4}}}]
    data['probes'] = [{'tick': tick, 'agent_id': 'a', 'channel': channel,
                      'value_vector': {'fairness': .7, 'cooperation': .8}}
                      for tick in (0, 5) for channel in ('declared', 'private', 'public')]
    data['constitution_history'] = [{'version': 'society-v2-base', 'constitution':
        {'quorum': .5, 'amendment_threshold': .67,
         'rules': {'quorum_rule': 'simple_majority_of_eligible'}}}]
    data['sanctions'] = [dict(tick=5, subject_id='a', record_type='sanction'),
                         dict(tick=6, appellant_id='b', record_type='appeal')]
    script = TEMPLATE.rsplit('<script>', 1)[1].split('</script>', 1)[0]
    harness = '''
const elements = new Map(), plots = new Map(), created = [];
global.document={getElementById(id){if(!elements.has(id))elements.set(id,{
 value:id==='from'?'0':'', textContent:'', append(...os){for(const o of os)if(o.value&&!this.value)this.value=o.value},replaceChildren(){}
});return elements.get(id)},createElement(){const e={append(){},replaceChildren(){}};created.push(e);return e}};
document.getElementById('data').textContent=JSON.stringify(DATA);
global.window={Plotly:{}};
global.Plotly={react(id,data,layout){plots.set(id,{data,layout})}};
'''.replace('DATA', json.dumps(data))
    assertions = '''
const assert=require('node:assert/strict');
assert(plots.get('resources').data[0].x.length<=2000);
assert.equal($('value').value,'All');
assert(created.some(e=>e.textContent==='Constitutional rules'));
assert(created.some(e=>e.textContent==='quorum rule: simple majority of eligible'));
assert.equal(plots.get('probes').data.length,6);
assert(plots.get('probes').data.every(t=>t.mode==='lines+markers'));
$('channel').value='private';render();assert.equal(plots.get('probes').data.length,2);
$('value').value='fairness';render();assert.equal(plots.get('probes').data.length,1);
assert.equal(plots.get('profiles-chart').data[0].r[0],.7);
assert.equal(plots.get('resources').data[0].x.at(-1),10000);
$('agent').value='a';$('from').value=0;$('to').value=10;render();
assert.equal(plots.get('resources').data[0].x.length,11);
assert.equal(windowRows(D.sanctions).length,1);
assert.equal(plots.get('governance').data[0].y[0],'sanction');
$('latest').onclick();assert.equal(Number($('from').value),9901);
assert.equal(plots.get('resources').data[0].x.length,100);
'''
    path = tmp_path / 'behavior.cjs'
    path.write_text(harness + script + assertions)
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True)


def test_saved_profiles_authoritative_and_private_memory_excluded(tmp_path):
    (tmp_path / 'events.jsonl').write_text('{"tick":1}\n')
    snapshots = tmp_path / 'snapshots'
    snapshots.mkdir()
    initial = {'agents': {'m01': {'agent_id': 'm01', 'name': 'Mira', 'role': 'farmer',
                                 'persona_summary': '<script>persona</script>',
                                 'declared_values': {'fairness': 0.7},
                                 'resources': {'food': 10}, 'social_edges': ['m02'],
                                 'private_memory': 'never embed this'}}}
    (snapshots / 't00.json').write_text(json.dumps(initial))
    (snapshots / 'after_tick1000.json').write_text(json.dumps(
        {'agents': {'m01': {'resources': {'food': 3}, 'under_sanction': True}}}))
    data = load_run(tmp_path)
    assert data['profiles'][0]['initial']['name'] == 'Mira'
    assert data['profiles'][0]['latest']['resources'] == {'food': 3}
    assert 'never embed this' not in safe_json(data)
    assert data['snapshot_sources'] == ['t00.json', 'after_tick1000.json']


def test_snapshot_auxiliary_names_ignored(tmp_path):
    (tmp_path / 'events.jsonl').write_text('{"tick":1}\n')
    snapshots = tmp_path / 'snapshots'
    snapshots.mkdir()
    (snapshots / 'after_tick1000.backup.json').write_text('not a checkpoint')
    assert load_run(tmp_path)['profiles'] == []
