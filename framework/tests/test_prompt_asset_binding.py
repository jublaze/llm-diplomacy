import hashlib
import json

import pytest

from llm_diplomacy.core import prompt_assets


def test_checkpoint_asset_binding_preserves_original_paths_and_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt_assets, "_BOUND_PROMPT_ASSETS", None)
    names = ["strategic_imperatives", "victory_campaign_doctrine", "diplomacy_primer"]
    assets = {}
    for name in names:
        path = (tmp_path / (name + ".md")).resolve()
        path.write_text("original " + name, encoding="utf-8")
        assets[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    body = {"version": 1, "halt_required": True, "identity": {"prompt_assets": assets}}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({**body, "sha256": hashlib.sha256(canonical).hexdigest()}), encoding="utf-8")
    file_hash = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="checkpoint bytes"):
        prompt_assets.bind_checkpoint_prompt_assets(checkpoint, expected_file_sha256="0" * 64)
    assert prompt_assets._BOUND_PROMPT_ASSETS is None
    prompt_assets.bind_checkpoint_prompt_assets(checkpoint, expected_file_sha256=file_hash)
    assert prompt_assets.canonical_strategic_imperatives_path() == tmp_path / "strategic_imperatives.md"
    assert prompt_assets.canonical_victory_campaign_doctrine_path() == tmp_path / "victory_campaign_doctrine.md"
    assert prompt_assets.canonical_diplomacy_primer_path() == tmp_path / "diplomacy_primer.md"
    prompt_assets.bind_checkpoint_prompt_assets(checkpoint, expected_file_sha256=file_hash)
    (tmp_path / "diplomacy_primer.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes changed"):
        prompt_assets.bind_checkpoint_prompt_assets(checkpoint, expected_file_sha256=file_hash)
