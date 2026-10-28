"""Create and verify deterministic Diplomacy viewer ZIP archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MANIFEST = "package-manifest.json"
SCHEMA = "diplomacy_archive_manifest_v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def verify_directory(root: Path) -> dict:
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise ValueError(f"missing {MANIFEST}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA or not isinstance(manifest.get("files"), list):
        raise ValueError("invalid archive manifest")
    expected: list[str] = []
    for item in manifest["files"]:
        path_text = item.get("path")
        if not isinstance(path_text, str) or not safe_relative(path_text):
            raise ValueError(f"unsafe manifest path: {path_text!r}")
        path = root.joinpath(*PurePosixPath(path_text).parts)
        if not path.is_file():
            raise ValueError(f"missing manifested file: {path_text}")
        if path.stat().st_size != item.get("bytes"):
            raise ValueError(f"size mismatch: {path_text}")
        if sha256(path) != item.get("sha256"):
            raise ValueError(f"checksum mismatch: {path_text}")
        expected.append(path_text)
    actual = [path for path in relative_files(root) if path != MANIFEST]
    if sorted(expected) != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(f"manifest closure mismatch; missing={missing}, extra={extra}")
    return manifest


def create_archive(package_root: Path, output: Path, force: bool, skip_verification: bool) -> None:
    package_root = package_root.resolve()
    output = output.resolve()
    if not package_root.is_dir():
        raise ValueError(f"package root is not a directory: {package_root}")
    if package_root == output or package_root in output.parents:
        raise ValueError("archive output must be outside the package root")
    if not skip_verification:
        verify_directory(package_root)
    if output.exists() and not force:
        raise ValueError(f"archive already exists; pass --force to replace it: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.stem}-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in relative_files(package_root):
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, package_root.joinpath(*PurePosixPath(relative).parts).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        if output.exists():
            output.unlink()
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def extract_and_verify(archive_path: Path, extract_to: Path) -> dict:
    archive_path = archive_path.resolve()
    extract_to = extract_to.resolve()
    if not archive_path.is_file():
        raise ValueError(f"archive does not exist: {archive_path}")
    if extract_to.exists():
        raise ValueError(f"extract destination already exists: {extract_to}")
    staging = Path(tempfile.mkdtemp(prefix=f".{extract_to.name}-", dir=extract_to.parent))
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("archive contains duplicate paths")
            for item in archive.infolist():
                if not safe_relative(item.filename) or item.is_dir():
                    raise ValueError(f"unsafe archive entry: {item.filename!r}")
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError(f"archive symlink is not allowed: {item.filename}")
            archive.extractall(staging)
        manifest = verify_directory(staging)
        staging.replace(extract_to)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--package-root", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--force", action="store_true")
    create.add_argument("--skip-input-verification", action="store_true", help=argparse.SUPPRESS)
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", required=True)
    verify.add_argument("--extract-to", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            create_archive(Path(args.package_root), Path(args.output), args.force, args.skip_input_verification)
            print(f"created deterministic archive: {Path(args.output).resolve()}")
        else:
            manifest = extract_and_verify(Path(args.archive), Path(args.extract_to))
            print(f"verified clean extraction for {manifest['package_id']}: {Path(args.extract_to).resolve()}")
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
