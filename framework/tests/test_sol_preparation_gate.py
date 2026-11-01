import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

spec = importlib.util.spec_from_file_location('sol_preparation', Path(__file__).parents[1]/'scripts/prepare_sol_two_games.py')
prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, 'ROOT', tmp_path)
    monkeypatch.setattr(prep, 'source_files', lambda: {'test_source': 'fixed'})
    monkeypatch.setattr(prep.subprocess, 'run', lambda *args, **kwargs: pytest.fail('live dispatch forbidden in test'))
    return prep.prepare('sol-gated')


def test_preparation_is_dry_and_sealed(prepared):
    config = yaml.safe_load((prepared/'prepared.yaml').read_text(encoding='utf8'))
    record = json.loads((prepared/'preparation.json').read_text(encoding='utf8'))
    assert config['dry_run'] is True and config['games']['count'] == 1
    assert config['games']['country_assignment'] == 'fixed'
    second = yaml.safe_load((prepared/'second-template.yaml').read_text(encoding='utf8'))
    assert second['games']['country_assignment'] == 'seeded_shuffle'
    assert second['games']['seed'] == 20261028 and second['agent_state_mode'] == 'reuse'
    assert record['state'] == 'PREPARED_NOT_AUTHORIZED'
    assert record['config_sha256'] == prep.digest(prepared/'prepared.yaml')
    assert not (prepared/'launch.yaml').exists()


@pytest.mark.parametrize('live,ref,usd', [(False,'owner',350), (True,'',350), (True,'owner',175), (True,'owner',float('nan'))])
def test_missing_or_different_approval_cannot_launch(prepared, live, ref, usd):
    with pytest.raises(ValueError):
        prep.launch(prepared, owner_reference=ref, approved_usd=usd, approve_live=live)
    assert not (prepared/'launch.yaml').exists()


def test_changed_preparation_cannot_launch(prepared):
    p=prepared/'prepared.yaml'; p.write_text(p.read_text(encoding='utf8')+'\n',encoding='utf8')
    with pytest.raises(ValueError, match='changed'):
        prep.launch(prepared, owner_reference='owner', approved_usd=350, approve_live=True)


def test_launch_consumption_and_resume_config_binding(prepared, monkeypatch):
    commands=[]
    monkeypatch.setattr(prep.subprocess, 'run', lambda command, **kw: commands.append(command) or SimpleNamespace(returncode=0))
    assert prep.launch(prepared, owner_reference='owner', approved_usd=350, approve_live=True) == 0
    assert len(commands)==1 and '--resume' not in commands[0]
    with pytest.raises(ValueError, match='consumed'):
        prep.launch(prepared, owner_reference='owner', approved_usd=350, approve_live=True)
    assert prep.launch(prepared, owner_reference='owner', approved_usd=350, approve_live=True, resume=True)==0
    assert '--resume' in commands[-1]
    p=prepared/'launch.yaml';p.write_text(p.read_text(encoding='utf8')+'\n',encoding='utf8')
    with pytest.raises(ValueError, match='changed'):
        prep.launch(prepared, owner_reference='owner', approved_usd=350, approve_live=True, resume=True)
