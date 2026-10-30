from pathlib import Path
import pytest
from test_build_rule_activation import activated, cross_boundary


def stage_case(tmp_path):
    recovery, _ = activated(tmp_path)
    recovery.prepare_working_tree()
    cross_boundary(recovery)
    stage = recovery.working_root / 'terminal'
    stage.mkdir()
    (stage / 'result.txt').write_text('complete')
    return recovery, stage


def test_activated_pending_manifest_can_be_resumed(tmp_path, monkeypatch):
    recovery, stage = stage_case(tmp_path)
    original = recovery._apply_commit
    def interrupt(*args, **kwargs):
        raise OSError('after durable manifest')
    monkeypatch.setattr(recovery, '_apply_commit', interrupt)
    with pytest.raises(OSError, match='durable manifest'):
        recovery.commit_tree(staged_root=stage, canonical_root=recovery.root,
            cursor={'game_index': 1, 'unit': 'game_completed'}, state={'games_completed': 1}, applied=['game_001'])
    monkeypatch.setattr(recovery, '_apply_commit', original)
    recovery.prepare()
    recovery.finish_pending_commit(canonical_root=recovery.root)
    recovery.prepare()
    assert (recovery.root / 'result.txt').read_text() == 'complete'


def test_legacy_unlink_gap_requires_exact_canonical_files(tmp_path, monkeypatch):
    recovery, stage = stage_case(tmp_path)
    original = recovery._append
    def interrupt(row):
        if row['kind'] == 'commit_completed':
            raise OSError('before completion row')
        return original(row)
    monkeypatch.setattr(recovery, '_append', interrupt)
    with pytest.raises(OSError, match='completion row'):
        recovery.commit_tree(staged_root=stage, canonical_root=recovery.root,
            cursor={'game_index': 1, 'unit': 'game_completed'}, state={'games_completed': 1}, applied=['game_001'])
    monkeypatch.setattr(recovery, '_append', original)
    (recovery.path / 'commit_manifest.json').unlink()
    (recovery.root / 'result.txt').write_text('tampered')
    with pytest.raises(ValueError, match='canonical files'):
        recovery.prepare()
    (recovery.root / 'result.txt').write_text('complete')
    recovery.prepare()
    assert len([row for row in recovery._journal_rows() if row.get('kind') == 'commit_completed']) == 1
    (stage / 'result.txt').write_text('later')
    recovery.commit_tree(staged_root=stage, canonical_root=recovery.root,
        cursor={'game_index': 1, 'unit': 'population_reports_completed'}, state={'games_completed': 1}, applied=['game_001'])
    recovery.prepare()

def test_completion_row_before_unlink_is_idempotent(tmp_path, monkeypatch):
    recovery, stage = stage_case(tmp_path)
    original = Path.unlink
    def interrupt(path, *args, **kwargs):
        if path == recovery.path / 'commit_manifest.json':
            raise OSError('unlink interrupted')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', interrupt)
    with pytest.raises(OSError, match='unlink interrupted'):
        recovery.commit_tree(staged_root=stage, canonical_root=recovery.root,
            cursor={'game_index': 1, 'unit': 'game_completed'}, state={'games_completed': 1}, applied=['game_001'])
    monkeypatch.setattr(Path, 'unlink', original)
    recovery.prepare()
    recovery.finish_pending_commit(canonical_root=recovery.root)
    recovery.prepare()
    assert len([row for row in recovery._journal_rows() if row.get('kind') == 'commit_completed']) == 1
