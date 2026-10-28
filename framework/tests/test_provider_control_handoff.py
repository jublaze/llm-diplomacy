import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_diplomacy.agents.llm_provider import canonical_provider_config
from llm_diplomacy.population import PopulationExperimentRunner
from llm_diplomacy.runner.recovery import canonical_hash


def test_pricing_controls_require_receipt_and_preserve_copied_provenance(tmp_path):
    source=tmp_path/'experiments/old';root=tmp_path/'experiments/new'
    old=source/'agents/a';new=root/'agents/a';old.mkdir(parents=True);new.mkdir(parents=True)
    files={}
    for name in ('manifest.json','soul.md','public_profile.md','persistent_memory.md'):
        content=('unchanged '+name).encode();(old/name).write_bytes(content);(new/name).write_bytes(content)
        files[name]=hashlib.sha256(content).hexdigest()
    previous=canonical_provider_config({'model_profile':'openai_gpt_6_luna','reasoning_effort':'high'})
    current={**previous,'service_tier':'default','prompt_cache_mode':'explicit'}
    receipt={'kind':'standard_no_cache_provider_control_handoff_v1','source_root':str(source),
        'players':{'a':{'from_provider_sha256':canonical_hash(previous),
                        'to_provider_sha256':canonical_hash(current),'files':files}}}
    receipt['sha256']=canonical_hash(receipt)
    path=root/'handoff.json';path.write_text(json.dumps(receipt))
    runner=object.__new__(PopulationExperimentRunner)
    runner.config=SimpleNamespace(raw={'memory_handoff_receipt':str(path)},experiment_id='new')
    runner.base_dir=tmp_path;runner._active_recovery=None
    before={p.name:p.read_bytes() for p in new.iterdir()}
    runner._verify_provider_control_handoff(new,'a',previous,current)
    assert all((new/name).read_bytes()==value for name,value in before.items())
    with pytest.raises(ValueError,match='only Standard/no-cache'):
        runner._verify_provider_control_handoff(new,'a',previous,{**current,'model':'different'})
    receipt['sha256']='changed';path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='integrity'):
        runner._verify_provider_control_handoff(new,'a',previous,current)
    receipt['sha256']=canonical_hash({k:v for k,v in receipt.items() if k!='sha256'});path.write_text(json.dumps(receipt))
    (new/'persistent_memory.md').write_text('changed')
    with pytest.raises(ValueError,match='copied file hash'):
        runner._verify_provider_control_handoff(new,'a',previous,current)


def test_pricing_controls_do_not_allow_unreceipted_provider_reuse(tmp_path):
    runner=object.__new__(PopulationExperimentRunner);runner.config=SimpleNamespace(raw={})
    with pytest.raises(ValueError,match='provider configuration mismatch'):
        runner._verify_provider_control_handoff(tmp_path,'a',{}, {'service_tier':'default','prompt_cache_mode':'explicit'})
