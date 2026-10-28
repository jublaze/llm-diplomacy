from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path


FRAMEWORK_FILES = (
    "README.md",
    ".gitignore",
    "pyproject.toml",
    "strategic_imperatives.md",
    "diplomacy_primer.md",
    "victory_campaign_doctrine.md",
    "maps/standard.json",
    "docs/AGENT_FACTORY.md",
    "docs/AGENT_SPEC.md",
    "docs/ARTIFACT_COMPATIBILITY.md",
    "docs/CORE_EVENT_CONTRACTS.md",
    "docs/DATC_COVERAGE.md",
    "docs/DIPLOMACY_RULE_SPEC.md",
    "docs/EXPERIMENT_CHECKPOINTS.md",
    "docs/FRAMEWORK_AUDIT_PROMPT.md",
    "docs/OBSERVABILITY_AND_EVIDENCE_STANDARDS.md",
    "docs/NATIVE_CODEX_PROVIDER.md",
    "docs/ORCHESTRATION_OWNERSHIP.md",
    "docs/PER_PLAYER_PROVIDER_SCOPE.md",
    "docs/PROVIDER_RECOVERY.md",
    "docs/PER_PLAYER_2X2_OFFLINE_SMOKE.md",
    "docs/RELIABILITY_PROVIDER_COST.md",
    "docs/REPORT_BUNDLE_EXPORTER.md",
    "docs/REPORT_BUNDLE_PROJECTOR.md",
    "docs/SYSTEM_DESIGN.md",
    "docs/VALIDATION.md",
    "docs/VIEWER_EXPERIMENTS.md",
    "docs/PUBLICATION_READINESS.md",
    "docs/PAGES_PREPARATION.md",
)
ROOT_FILES = (".github/workflows/viewer-pages-preview.yml",)
FRAMEWORK_TREES = {
    "blueprints": {".yaml"},
    "config/examples": {".yaml", ".json"},
    "config/model_profiles": {".yaml"},
    "experiments/configs": {".yaml", ".json"},
    "scripts": {".py", ".ps1"},
    "src": {".py", ".json"},
    "tests": {".py", ".json", ".md", ".txt"},
}
VIEWER_FILES = (
    "package.json",
    "package-lock.json",
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "eslint.config.js",
    "playwright.config.ts",
    "playwright.public.config.ts",
)
VIEWER_TREES = {
    "contracts": {".json"},
    "src": {".ts", ".tsx", ".css"},
    "scripts": {".ts", ".tsx", ".js", ".py"},
    "tests": {".ts", ".tsx", ".json"},
    "docs": {".md"},
}
VIEWER_DOCS = {
    "AUDIENCE_PROJECTIONS.md",
    "LOCAL_PUBLIC_RELEASE.md",
    "NARRATIVE_PIPELINE.md",
    "PORTABLE_ARCHIVE.md",
    "PUBLIC_BUILD_REHEARSAL.md",
    "REPORT_CATALOG.md",
}
SKIP_DIRS = {
    ".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".codex",
    ".agents", ".aws", "__pycache__", "node_modules", "dist", "dist-public",
    "build", "coverage", "experiments", "agent_state", "logs", "run_logs",
    "public-build-input", "public-build-audits", "test-results", ".tmp",
}


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction and is_junction():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_no_link_components(path: Path, stop: Path) -> None:
    path = Path(os.path.abspath(path))
    stop = Path(os.path.abspath(stop))
    try:
        relative = path.relative_to(stop)
    except ValueError as exc:
        raise ValueError("path escapes its declared root") from exc
    current = stop
    if _is_link(current):
        raise ValueError("linked root is not allowed")
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ValueError("symlink or junction is not allowed: " + part)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_destination(source_root: Path, viewer_root: Path, destination: Path) -> tuple[Path, Path, Path]:
    source_root = Path(os.path.abspath(source_root))
    viewer_root = Path(os.path.abspath(viewer_root))
    for root in (source_root, viewer_root):
        if not root.is_dir():
            raise FileNotFoundError("declared source root is missing")
        _assert_no_link_components(root, Path(root.anchor))
    source_root = source_root.resolve(strict=True)
    viewer_root = viewer_root.resolve(strict=True)
    destination = Path(os.path.abspath(destination))
    if destination.exists():
        raise FileExistsError("candidate destination already exists")
    local_viewer_root = viewer_root == source_root / "viewer"
    if source_root == viewer_root or _inside(source_root, viewer_root) or (_inside(viewer_root, source_root) and not local_viewer_root):
        raise ValueError("framework and viewer source roots overlap")
    selected_roots = [source_root / name for name in FRAMEWORK_TREES]
    selected_roots.extend(viewer_root / name for name in VIEWER_TREES if name != "docs")
    selected_roots.extend([viewer_root / "public" / "maps"])
    for selected in selected_roots:
        selected = selected.resolve(strict=True)
        if _inside(destination, selected) or _inside(selected, destination):
            raise ValueError("destination overlaps selected source")
    parent = destination.parent.resolve(strict=True)
    _assert_no_link_components(destination.parent, parent.anchor and Path(parent.anchor) or parent)
    return source_root, viewer_root, destination


def _collect_tree(root: Path, relative: str, extensions: set[str]) -> list[tuple[str, Path]]:
    start = root / relative
    if not start.is_dir():
        raise FileNotFoundError("required source directory is missing: " + relative)
    _assert_no_link_components(start, root)
    found: list[tuple[str, Path]] = []
    def fail_walk(error: OSError) -> None:
        raise error

    for current, dirnames, filenames in os.walk(start, topdown=True, onerror=fail_walk, followlinks=False):
        current_path = Path(current)
        keep: list[str] = []
        for name in sorted(dirnames):
            child = current_path / name
            if _is_link(child):
                raise ValueError("symlink or junction is not allowed: " + child.relative_to(root).as_posix())
            if name not in SKIP_DIRS:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            source = current_path / name
            if _is_link(source):
                raise ValueError("symlink or junction is not allowed: " + source.relative_to(root).as_posix())
            if source.suffix.lower() in extensions:
                found.append((source.relative_to(root).as_posix(), source))
    return found


def _add_file(root: Path, relative: str, package: str, collected: list[tuple[str, Path]]) -> None:
    source = root / Path(relative)
    _assert_no_link_components(source, root)
    if not source.is_file():
        raise FileNotFoundError("required source file is missing: " + relative)
    collected.append((package + "/" + relative.replace("\\", "/"), source))


def _viewer_source_files(viewer_root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for relative in VIEWER_FILES:
        _assert_no_link_components(viewer_root / relative, viewer_root)
        if not (viewer_root / relative).is_file():
            raise FileNotFoundError("required viewer source file is missing: " + relative)
        files.append((relative, viewer_root / relative))
    for relative, extensions in VIEWER_TREES.items():
        if relative == "docs":
            for name in sorted(VIEWER_DOCS):
                _assert_no_link_components(viewer_root / relative / name, viewer_root)
                if not (viewer_root / relative / name).is_file():
                    raise FileNotFoundError("required viewer documentation is missing: " + name)
                files.append((relative + "/" + name, viewer_root / relative / name))
            continue
        files.extend(_collect_tree(viewer_root, relative, extensions))
    for relative in ("public/maps/standard_clean.svg", "public/maps/standard_clean.LICENSE.md"):
        _assert_no_link_components(viewer_root / relative, viewer_root)
        if not (viewer_root / relative).is_file():
            raise FileNotFoundError("required licensed viewer map file is missing: " + relative)
        files.append((relative, viewer_root / relative))
    return files


def import_viewer_source(source_root: Path, viewer_root: Path, destination: Path) -> dict[str, object]:
    source_root = Path(os.path.abspath(source_root))
    viewer_root = Path(os.path.abspath(viewer_root))
    destination = Path(os.path.abspath(destination))
    if destination != source_root / "viewer":
        raise ValueError("viewer import destination must be the framework's viewer/ directory")
    if destination.exists():
        raise FileExistsError("viewer import destination already exists")
    source_root, viewer_root, destination = validate_destination(source_root, viewer_root, destination)
    sources = _viewer_source_files(viewer_root)
    relative_paths = [relative for relative, _ in sources]
    if len(relative_paths) != len(set(relative_paths)):
        raise ValueError("viewer source allowlist maps duplicate paths")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for relative, source in sources:
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (destination / "README.md").write_text(_viewer_readme(), encoding="utf-8")
        (destination / "tsconfig.source.json").write_text(_viewer_source_tsconfig(), encoding="utf-8")
        manifest_files = []
        for relative in [*relative_paths, "README.md", "tsconfig.source.json"]:
            digest, size = _manifest_hash(destination / Path(relative))
            manifest_files.append({"path": relative, "sha256": digest, "bytes": size})
        manifest_files.sort(key=lambda item: str(item["path"]))
        manifest = {
            "schema_version": 1,
            "package": "viewer",
            "source_project": viewer_root.name,
            "allowlist": {
                "files": sorted(VIEWER_FILES),
                "trees": [{"path": path, "extensions": sorted(extensions)} for path, extensions in sorted(VIEWER_TREES.items())],
                "selected_docs": sorted(VIEWER_DOCS),
                "licensed_map_files": ["public/maps/standard_clean.svg", "public/maps/standard_clean.LICENSE.md"],
            },
            "excluded": ["repository history", "dependencies", "builds", "generated report catalogs", "private historical report fixtures", "runtime data"],
            "files": manifest_files,
        }
        _validate_markdown_links(destination)
        return manifest
    except Exception:
        shutil.rmtree(destination)
        raise


def _manifest_hash(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _candidate_readme() -> str:
    return """# Source-only publication candidate\n\nThis is a source snapshot of the Diplomacy experiment framework and its compatible report viewer. It excludes repository history, runtime memories, generated experiments, viewer report fixtures, dependencies, and build outputs. Read `framework/README.md` for the framework quickstart and `viewer/README.md` for viewer validation.\n\nThe offline DATC gate covers 172 entries: 155 match expectations, nine are declared project-policy divergences, one is an adapter limitation (ordinary 6.G.11 adjacent-convoy intent inference), and seven are historical alternatives. These results are not an unqualified standard-rules conformance claim; see `framework/docs/DATC_COVERAGE.md`. The project-wide license has not been selected, so this candidate is not a release. The standard map adaptation includes its own attribution and CC BY-SA 3.0 notice.\n"""


def _candidate_gitignore() -> str:
    return """# Monorepo package runtime and generated data\n**/__pycache__/\n**/*.py[cod]\n**/.pytest_cache/\n**/.mypy_cache/\n**/.ruff_cache/\n**/.venv/\n**/venv/\n**/.tox/\n**/*.egg-info/\n\n# Viewer dependencies, builds, and local validation output\n**/node_modules/\n**/dist/\n**/dist-public/\n**/build/\n**/coverage/\n**/.vite/\n**/test-results/\n**/playwright-report/\n**/public/reports/\n**/public/catalog/\n**/public-build-input/\n**/public-build-input-*/\n**/public-build-demo/\n**/public-build-audits/\n**/reports/\n**/catalog/\n**/audits/\n\n# Framework run state and private agent material\nframework/agent_state/\nframework/agents_*/\nframework/logs/\nframework/run_logs/\nframework/run-output/\nframework/agents/*/persistent_memory.md\nframework/agents/*/runtime/\nframework/agents/*/logs/\nframework/agents/*/memory/*.jsonl\nframework/experiments/*\n!framework/experiments/configs/\n!framework/experiments/configs/*.yaml\n!framework/experiments/configs/*.json\n\n# Local credentials and environment files; retain example templates\n**/.aws/\n**/.env\n**/.env.*\n!**/.env.example\n**/standalone.env\n**/*.local.env\n**/*.key\n**/*.pem\n**/*.p12\n**/*.pfx\n**/secrets.*\n\n# Logs and temporary files\n**/*.log\n**/*.tmp\n.DS_Store\nThumbs.db\n"""


def _viewer_readme() -> str:
    return """# Report viewer package

This package is paired with the framework report-bundle schema. It intentionally excludes generated reports and historical/private fixtures. Supported Node releases are 22.12 or later within 22.x, 24.x, and 26 or later. Install the locked dependencies without running npm lifecycle hooks, then run the portable checks:

```powershell
npm ci --ignore-scripts
npm run typecheck
npm test
npm run build
```

Portable tests use explicit synthetic reports to check schema and semantic validation, catalog loading, and year navigation through 1912. Historical fixture regressions remain available through `npm run test:legacy` after approved fixtures, their generated catalog, and the optional `docs/reproducibility/v1/` materials have been supplied. Those materials describe fixture-linked workflows and are omitted from this source package because their sample report data is excluded. They are not part of clean-checkout checks. Use only approved, redistributable report bundles for viewer QA.
"""
def _viewer_source_tsconfig() -> str:
    return json.dumps({
        "compilerOptions": {
            "target": "ES2022", "useDefineForClassFields": True,
            "lib": ["ES2022", "DOM", "DOM.Iterable"], "allowJs": False,
            "skipLibCheck": True, "esModuleInterop": True,
            "allowSyntheticDefaultImports": True, "strict": True,
            "module": "ESNext", "moduleResolution": "Bundler",
            "resolveJsonModule": True, "isolatedModules": True,
            "noEmit": True, "jsx": "react-jsx",
            "allowImportingTsExtensions": True,
            "types": ["node", "vite/client"],
        },
        "include": ["src", "vite.config.ts"],
    }, indent=2) + "\n"


def _framework_readme(source_root: Path) -> str:
    text = (source_root / "README.md").read_text(encoding="utf-8")
    current = "- The report viewer lives in the sibling `llm-diplomacy-viewer` checkout and is not part of this repository's normal working tree. The publication exporter can prepare a source-only monorepo candidate for review; see [publication readiness](docs/PUBLICATION_READINESS.md)."
    candidate = "- In this source-only candidate, the adjacent `../viewer/` directory contains the compatible viewer package. In the original framework checkout, the viewer remains a separate sibling repository. See [offline viewer study plumbing](docs/VIEWER_EXPERIMENTS.md) for bounded fake-provider demonstrations."
    text = text.replace(current, candidate)
    text = text.replace("[package README](viewer/README.md)", "[package README](../viewer/README.md)")
    return text.replace("- [Publication readiness](docs/PUBLICATION_READINESS.md)", "- [Publication readiness](docs/PUBLICATION_READINESS.md)\n- [Offline viewer experiments](docs/VIEWER_EXPERIMENTS.md)")


def _validate_markdown_links(root: Path) -> None:
    import re
    from urllib.parse import unquote

    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    external_prefixes = ("http:", "https:", "mailto:", "data:", "tel:")
    for document in root.rglob("*.md"):
        text = document.read_text(encoding="utf-8", errors="replace")
        for match in link_pattern.finditer(text):
            target = match.group(1).strip().split()[0].strip("<>")
            if not target or target.startswith(("#", *external_prefixes)):
                continue
            path_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not path_part:
                continue
            resolved = (document.parent / path_part).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError as exc:
                raise ValueError("candidate Markdown link escapes package: " + document.relative_to(root).as_posix()) from exc
            if not resolved.exists():
                raise ValueError(
                    "candidate Markdown link target is missing: "
                    + document.relative_to(root).as_posix()
                    + " -> "
                    + target
                )


def export_candidate(source_root: Path, viewer_root: Path, destination: Path) -> dict[str, object]:
    source_root, viewer_root, destination = validate_destination(source_root, viewer_root, destination)
    files: list[tuple[str, Path]] = []
    for relative in ROOT_FILES:
        selected: list[tuple[str, Path]] = []
        _add_file(source_root, relative, "root", selected)
        files.extend((relative, source) for _, source in selected)
    for relative in FRAMEWORK_FILES:
        _add_file(source_root, relative, "framework", files)
    for relative, extensions in FRAMEWORK_TREES.items():
        for source_rel, source in _collect_tree(source_root, relative, extensions):
            files.append(("framework/" + source_rel, source))
    _add_file(source_root, "tests/fixtures/datc/upstream/LICENSE", "framework", files)
    files.extend(("viewer/" + relative, source) for relative, source in _viewer_source_files(viewer_root))

    paths = [name for name, _ in files]
    if len(paths) != len(set(paths)):
        raise ValueError("allowlist maps more than one source to a destination path")
    destination.mkdir(parents=True, exist_ok=False)
    manifest_files: list[dict[str, object]] = []
    try:
        (destination / "README.md").write_bytes(_candidate_readme().encode("utf-8"))
        root_gitignore = _candidate_gitignore().encode("utf-8")
        (destination / ".gitignore").write_bytes(root_gitignore)
        (destination / "viewer" / "README.md").parent.mkdir(parents=True, exist_ok=True)
        (destination / "viewer" / "README.md").write_bytes(_viewer_readme().encode("utf-8"))
        source_tsconfig = _viewer_source_tsconfig().encode("utf-8")
        (destination / "viewer" / "tsconfig.source.json").write_bytes(source_tsconfig)
        manifest_files.extend([
            {"path": "README.md", "sha256": hashlib.sha256(_candidate_readme().encode()).hexdigest(), "bytes": len(_candidate_readme().encode())},
            {"path": ".gitignore", "sha256": hashlib.sha256(root_gitignore).hexdigest(), "bytes": len(root_gitignore)},
            {"path": "viewer/README.md", "sha256": hashlib.sha256(_viewer_readme().encode()).hexdigest(), "bytes": len(_viewer_readme().encode())},
            {"path": "viewer/tsconfig.source.json", "sha256": hashlib.sha256(source_tsconfig).hexdigest(), "bytes": len(source_tsconfig)},
        ])
        for relative, source in sorted(files):
            target = destination / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "framework/README.md":
                target.write_text(_framework_readme(source_root), encoding="utf-8")
            else:
                shutil.copyfile(source, target)
            digest, size = _manifest_hash(target)
            manifest_files.append({"path": relative, "sha256": digest, "bytes": size})
        _validate_markdown_links(destination)
        manifest_files.sort(key=lambda item: str(item["path"]))
        manifest = {
            "schema_version": 1,
            "candidate_kind": "source_only_framework_viewer_monorepo",
            "root_source_allowlist": list(ROOT_FILES),
            "framework_source_allowlist": {
                "files": sorted(list(FRAMEWORK_FILES) + ["tests/fixtures/datc/upstream/LICENSE"]),
                "trees": [{"path": path, "extensions": sorted(extensions)} for path, extensions in sorted(FRAMEWORK_TREES.items())],
            },
            "viewer_source_allowlist": {
                "files": sorted(list(VIEWER_FILES) + ["public/maps/standard_clean.svg", "public/maps/standard_clean.LICENSE.md"]),
                "trees": [{"path": path, "extensions": sorted(extensions)} for path, extensions in sorted(VIEWER_TREES.items())],
                "selected_docs": sorted(VIEWER_DOCS),
            },
            "transformations": {
                "framework/README.md": "Updates checkout wording for adjacent viewer package in monorepo candidate.",
                ".gitignore": "Adds root monorepo exclusions for dependencies, generated reports/builds, runtime state, and local credentials while retaining authored source/configs.",
                "viewer/README.md": "Documents fixture-free source checks.",
                "viewer/tsconfig.source.json": "Generated config checks app source without tests that require excluded report fixtures.",
            },
            "excluded": [".git and history", "runtime memories and agent state", "experiment/report fixtures", "public/reports", "node_modules", "dist/build products", "logs", "secrets and credentials"],
            "project_license_status": "No project-wide license was provided; none was added.",
            "files": manifest_files,
        }
        (destination / "SOURCE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest
    except Exception:
        shutil.rmtree(destination)
        raise


def default_viewer_root(source_root: Path, *, importing: bool = False) -> Path:
    source_root = Path(source_root)
    candidates = (
        (source_root.parent / "llm-diplomacy-viewer", source_root.parent / "viewer")
        if importing else
        (source_root / "viewer", source_root.parent / "viewer", source_root.parent / "llm-diplomacy-viewer")
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export an explicit source-only framework/viewer candidate.")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--viewer-root", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--import-viewer", action="store_true", help="copy allowlisted viewer source into source-root/viewer")
    args = parser.parse_args(argv)
    source_root = args.source_root
    viewer_root = args.viewer_root or default_viewer_root(source_root, importing=args.import_viewer)
    if args.import_viewer:
        manifest = import_viewer_source(source_root, viewer_root, args.destination)
        print(json.dumps({"destination": str(args.destination), "file_count": len(manifest["files"]), "manifest": manifest}, sort_keys=True))
        return 0
    manifest = export_candidate(source_root, viewer_root, args.destination)
    print(json.dumps({"candidate": str(args.destination), "file_count": len(manifest["files"]), "manifest": "SOURCE_MANIFEST.json"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
