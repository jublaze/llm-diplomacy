import importlib.util
import json
import os
from pathlib import Path
import pytest
from llm_diplomacy.runner.recovery import canonical_hash

root = Path(os.environ.get('BUILD_RULE_CANDIDATE_ROOT', Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('terminal_handoff_test', root/'scripts/prepare_terra_second_game.py')
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


@pytest.mark.parametrize('changed', ['metrics/budget/budget_events.jsonl',
    'recovery/terminal-currency-source-checkpoint.json', 'recovery/terminal-currency-source-budget.json'])
def test_receipt_reverification_rejects_changed_terminal_evidence(tmp_path, monkeypatch, changed):
    source=tmp_path/'source';destination=tmp_path/'dest';source.mkdir();destination.mkdir()
    policy=tmp_path/'policy.json';template=tmp_path/'template.yaml';policy.write_text('{}');template.write_text('{}')
    files=['metrics/budget/budget_events.jsonl','recovery/terminal-currency-source-checkpoint.json',
           'recovery/terminal-currency-source-budget.json']
    for name in files:
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('unchanged')
    (destination/'launch.yaml').write_text('unchanged')
    monkeypatch.setattr(handoff,'runtime_code_sha',lambda:'code')
    entry=handoff.digest(root/'scripts/run_population_experiment.py')
    wrapper=handoff.digest(root/'scripts/run_sealed_population.py')
    body={'policy_sha256':handoff.digest(policy),'template_sha256':handoff.digest(template),
          'runtime_code_sha256':'code','runtime_entrypoint_sha256':entry,'runtime_wrapper_sha256':wrapper,
          'source_root':str(source),'source_files':[{'path':p,'sha256':handoff.digest(source/p)} for p in files],
          'destination_files':[{'path':'launch.yaml','sha256':handoff.digest(destination/'launch.yaml')}]}
    (destination/'handoff_receipt.json').write_text(json.dumps({**body,'sha256':canonical_hash(body)}))
    args=(destination,policy,template,source,'code',entry,wrapper)
    handoff.verify_receipt(*args)
    (source/changed).write_text('tampered')
    with pytest.raises(ValueError,match='source changed'): handoff.verify_receipt(*args)
