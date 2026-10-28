import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "export_publication_candidate.py"
SPEC = importlib.util.spec_from_file_location("publication_export", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(exporter)


def _minimal_sources(tmp_path: Path, monkeypatch):
    source = tmp_path / "framework-source"
    viewer = tmp_path / "viewer-source"
    (source / "src").mkdir(parents=True)
    (viewer / "src").mkdir(parents=True)
    for name in ("blueprints", "tests"):
        (source / name).mkdir()
    license_path = source / "tests" / "fixtures" / "datc" / "upstream" / "LICENSE"
    license_path.parent.mkdir(parents=True)
    license_path.write_text("fixture license", encoding="utf-8")
    for name in ("contracts", "scripts", "tests", "docs"):
        (viewer / name).mkdir(parents=True, exist_ok=True)
    (viewer / "public" / "maps").mkdir(parents=True, exist_ok=True)
    (source / "README.md").write_text("source readme", encoding="utf-8")
    (source / "src" / "allowed.py").write_text("value = 1\n", encoding="utf-8")
    (source / "src" / "ignored.txt").write_text("omit", encoding="utf-8")
    (viewer / "package.json").write_text("{}", encoding="utf-8")
    (viewer / "src" / "app.ts").write_text("export {};\n", encoding="utf-8")
    (viewer / "public" / "maps" / "standard_clean.svg").write_text("<svg/>", encoding="utf-8")
    (viewer / "public" / "maps" / "standard_clean.LICENSE.md").write_text("asset notice", encoding="utf-8")
    monkeypatch.setattr(exporter, "FRAMEWORK_FILES", ("README.md",))
    monkeypatch.setattr(exporter, "ROOT_FILES", ())
    monkeypatch.setattr(exporter, "FRAMEWORK_TREES", {"src": {".py"}})
    monkeypatch.setattr(exporter, "VIEWER_FILES", ("package.json",))
    monkeypatch.setattr(exporter, "VIEWER_TREES", {"src": {".ts"}, "docs": {".md"}})
    monkeypatch.setattr(exporter, "VIEWER_DOCS", set())
    return source, viewer


def test_export_uses_allowlist_and_relative_hash_manifest(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    destination = tmp_path / "candidate"
    manifest = exporter.export_candidate(source, viewer, destination)
    assert (destination / "framework" / "src" / "allowed.py").is_file()
    assert not (destination / "framework" / "src" / "ignored.txt").exists()
    assert (destination / "viewer" / "public" / "maps" / "standard_clean.LICENSE.md").is_file()
    parsed = json.loads((destination / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    assert parsed == manifest
    assert all(not Path(row["path"]).is_absolute() for row in parsed["files"])
    for row in parsed["files"]:
        data = (destination / row["path"]).read_bytes()
        assert row["bytes"] == len(data)
        assert row["sha256"] == hashlib.sha256(data).hexdigest()
    assert "private" not in (destination / "SOURCE_MANIFEST.json").read_text(encoding="utf-8").lower()


def test_export_writes_root_monorepo_ignore_and_keeps_authored_sources(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    destination = tmp_path / "candidate"
    exporter.export_candidate(source, viewer, destination)
    rules = (destination / ".gitignore").read_text(encoding="utf-8")
    assert "**/node_modules/" in rules
    assert "**/public/reports/" in rules
    assert "framework/agents/*/persistent_memory.md" in rules
    assert "**/.aws/" in rules
    assert "**/.env" in rules
    assert "!**/.env.example" in rules
    assert "framework/config/" not in rules
    assert "framework/blueprints/" not in rules
    manifest = json.loads((destination / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    assert ".gitignore" in {row["path"] for row in manifest["files"]}
    assert (destination / "framework" / "src" / "allowed.py").is_file()


def test_markdown_link_validation_checks_exported_local_targets(tmp_path):
    docs = tmp_path / "candidate"
    docs.mkdir()
    (docs / "README.md").write_text("[guide](docs/guide.md)\n", encoding="utf-8")
    (docs / "docs").mkdir()
    (docs / "docs" / "guide.md").write_text("[source](../README.md)\n", encoding="utf-8")
    exporter._validate_markdown_links(docs)
    (docs / "README.md").write_text("[missing](docs/missing.md)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="target is missing"):
        exporter._validate_markdown_links(docs)


def test_export_allowlist_includes_provider_scope_references():
    assert "docs/PER_PLAYER_2X2_OFFLINE_SMOKE.md" in exporter.FRAMEWORK_FILES
    assert "docs/RELIABILITY_PROVIDER_COST.md" in exporter.FRAMEWORK_FILES
    assert "docs/NATIVE_CODEX_PROVIDER.md" in exporter.FRAMEWORK_FILES


def test_export_refuses_existing_destination(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        exporter.export_candidate(source, viewer, destination)


def test_export_refuses_destination_inside_selected_source(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="overlaps selected source"):
        exporter.export_candidate(source, viewer, source / "src" / "candidate")


def test_export_refuses_overlapping_source_roots(tmp_path, monkeypatch):
    source, _ = _minimal_sources(tmp_path, monkeypatch)
    nested_viewer = source / "other"
    nested_viewer.mkdir()
    with pytest.raises(ValueError, match="roots overlap"):
        exporter.validate_destination(source, nested_viewer, tmp_path / "candidate")


def test_import_viewer_copies_allowlisted_source_only_to_local_package(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    manifest = exporter.import_viewer_source(source, viewer, source / "viewer")
    package = source / "viewer"
    assert (package / "package.json").read_text(encoding="utf-8") == "{}"
    assert (package / "src" / "app.ts").is_file()
    assert (package / "README.md").is_file()
    assert (package / "tsconfig.source.json").is_file()
    assert not (package / "public" / "reports").exists()
    assert not (package / "fixtures").exists()
    for row in manifest["files"]:
        data = (package / row["path"]).read_bytes()
        assert row["sha256"] == hashlib.sha256(data).hexdigest()
    assert manifest["source_project"] == viewer.name


def test_import_viewer_rejects_other_destinations_and_overwrite(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="framework's viewer/"):
        exporter.import_viewer_source(source, viewer, tmp_path / "elsewhere")
    destination = source / "viewer"
    destination.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        exporter.import_viewer_source(source, viewer, destination)


def test_local_viewer_nesting_is_allowed_but_other_nested_roots_are_rejected(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    local_viewer = source / "viewer"
    (local_viewer / "src").mkdir(parents=True)
    (local_viewer / "public" / "maps").mkdir(parents=True)
    source_root, viewer_root, _ = exporter.validate_destination(source, local_viewer, tmp_path / "candidate")
    assert viewer_root == local_viewer.resolve()
    another_nested = source / "other"
    another_nested.mkdir()
    with pytest.raises(ValueError, match="roots overlap"):
        exporter.validate_destination(source, another_nested, tmp_path / "candidate")


def test_default_viewer_path_prefers_local_package_and_import_uses_sibling(tmp_path):
    source = tmp_path / "framework"
    source.mkdir()
    local = source / "viewer"
    sibling = tmp_path / "llm-diplomacy-viewer"
    local.mkdir()
    sibling.mkdir()
    assert exporter.default_viewer_root(source) == local
    assert exporter.default_viewer_root(source, importing=True) == sibling


def test_selected_tree_refuses_symlink_escape(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("sensitive = True", encoding="utf-8")
    link = root / "src" / "escape.py"
    link.write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(exporter, "_is_link", lambda path: Path(path) == link)
    with pytest.raises(ValueError, match="symlink or junction"):
        exporter._collect_tree(root, "src", {".py"})


def test_selected_tree_detects_real_symlink_escape_when_supported(tmp_path):
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("sensitive = True", encoding="utf-8")
    link = root / "src" / "escape.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create symlinks.")
    with pytest.raises(ValueError, match="symlink or junction"):
        exporter._collect_tree(root, "src", {".py"})


def test_is_link_detects_windows_reparse_point_without_is_junction(monkeypatch, tmp_path):
    path = tmp_path / "legacy-junction"
    path.write_text("placeholder", encoding="utf-8")
    monkeypatch.delattr(Path, "is_junction", raising=False)
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_file_attributes=0x400))
    assert exporter._is_link(path)


def test_pages_workflow_is_explicitly_exported_at_monorepo_root(tmp_path, monkeypatch):
    source, viewer = _minimal_sources(tmp_path, monkeypatch)
    workflow = '.github/workflows/viewer-pages-preview.yml'
    path = source / workflow
    path.parent.mkdir(parents=True)
    path.write_text('name: artifact-only\npermissions:\n  contents: read\n', encoding='utf-8')
    (path.parent / 'unselected.yml').write_text('name: omit\n', encoding='utf-8')
    monkeypatch.setattr(exporter, 'ROOT_FILES', (workflow,))
    destination = tmp_path / 'candidate'
    manifest = exporter.export_candidate(source, viewer, destination)
    assert (destination / workflow).read_bytes() == path.read_bytes()
    assert not (destination / '.github/workflows/unselected.yml').exists()
    assert manifest['root_source_allowlist'] == [workflow]
