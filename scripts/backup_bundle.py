"""Validate maintenance snapshots and replace volume contents through staging.

Uses only the Python standard library; also runs via `python -c` inside Docker.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile

ROOTS = {"data": "data", "tests": "app/static/tests", "uploads": "app/static/uploads"}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def checked_members(archive):
    members = archive.getmembers()
    roots = set()
    entries = {}
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in ROOTS:
            raise ValueError(f"Unsafe archive path: {member.name}")
        if not member.isfile() and not member.isdir():
            raise ValueError(f"Links and special files are not supported: {member.name}")
        if path in entries:
            raise ValueError(f"Duplicate archive path: {member.name}")
        entries[path] = member.isdir()
        roots.add(path.parts[0])
        if len(path.parts) == 1 and not member.isdir():
            raise ValueError(f"Archive root must be a directory: {member.name}")
    if not {"data", "tests"}.issubset(roots):
        raise ValueError("Archive must contain data and tests directories")
    for path in entries:
        if any(parent in entries and not entries[parent] for parent in path.parents):
            raise ValueError(f"File used as a directory: {path}")
    # Read every file to catch truncated gzip/data before replacing anything.
    for member in members:
        if member.isfile():
            with archive.extractfile(member) as source:
                while source.read(1024 * 1024):
                    pass
    return members, roots


def verify(directory):
    directory = Path(directory)
    dump = directory / "database.dump"
    files = directory / "files.tar.gz"
    with dump.open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise ValueError("Not a PostgreSQL custom-format dump")
    manifest_path = directory / "manifest.json"
    manifest = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("format_version") != 1:
            raise ValueError("Unsupported backup manifest version")
        for name in ("database.dump", "files.tar.gz"):
            if manifest.get("sha256", {}).get(name) != digest(directory / name):
                raise ValueError(f"Checksum mismatch: {name}")
    else:
        print("Warning: legacy backup has no checksums/version manifest.", file=sys.stderr)
    with tarfile.open(files, "r:gz") as archive:
        _, roots = checked_members(archive)
    if manifest and not set(manifest.get("required_roots", [])).issubset(roots):
        raise ValueError("Backup is missing required file directories")
    if "uploads" not in roots:
        print("Warning: legacy backup has no static/uploads; that volume will be preserved.", file=sys.stderr)


def create_manifest(args):
    directory = Path(args.directory)
    manifest = {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "git_commit": args.commit, "app_image_id": args.image,
                "required_roots": list(ROOTS),
                "sha256": {name: digest(directory / name) for name in ("database.dump", "files.tar.gz")}}
    temporary = directory / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(directory / "manifest.json")


def restore_stream(stream, target_root, uploads_only=False):
    """Stage all files first, then swap children of mounted volume roots.

    Old children remain available until all swaps succeed. If a filesystem
    operation fails, put the previous children back; the caller keeps app stopped.
    """
    stages, old_dirs, installed, modified = {}, {}, {}, []
    with tempfile.TemporaryFile() as packed:
        shutil.copyfileobj(stream, packed)
        packed.seek(0)
        with tarfile.open(fileobj=packed, mode="r:gz") as archive:
            members, roots = checked_members(archive)
            selected = roots & {"uploads"} if uploads_only else roots
            if uploads_only and "uploads" not in roots:
                raise ValueError("Update snapshot does not contain uploads")
            try:
                for name in sorted(selected):
                    destination = Path(target_root) / ROOTS[name]
                    destination.mkdir(parents=True, exist_ok=True)
                    stages[name] = Path(tempfile.mkdtemp(prefix=".schooltest-stage-", dir=destination))
                for member in members:
                    parts = PurePosixPath(member.name).parts
                    if parts[0] not in selected or len(parts) == 1:
                        continue
                    output = stages[parts[0]].joinpath(*parts[1:])
                    if member.isdir():
                        output.mkdir(parents=True, exist_ok=True)
                    else:
                        output.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source, output.open("wb") as dest:
                            shutil.copyfileobj(source, dest)
                for name in sorted(selected):
                    destination = Path(target_root) / ROOTS[name]
                    previous = Path(tempfile.mkdtemp(prefix=".schooltest-old-", dir=destination))
                    old_dirs[name] = previous
                    modified.append(name)
                    installed[name] = []
                    for child in list(destination.iterdir()):
                        if child not in (stages[name], previous):
                            child.replace(previous / child.name)
                    for child in list(stages[name].iterdir()):
                        child.replace(destination / child.name)
                        installed[name].append(child.name)
            except Exception:
                for name in reversed(modified):
                    destination = Path(target_root) / ROOTS[name]
                    previous = old_dirs[name]
                    for child_name in installed[name]:
                        (destination / child_name).replace(stages[name] / child_name)
                    for child in list(previous.iterdir()):
                        child.replace(destination / child.name)
                raise
            finally:
                for stage in stages.values():
                    shutil.rmtree(stage)
                for previous in old_dirs.values():
                    if not any(previous.iterdir()):
                        previous.rmdir()
            for previous in old_dirs.values():
                shutil.rmtree(previous)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("directory")
    create.add_argument("--commit", required=True)
    create.add_argument("--image", required=True)
    check = commands.add_parser("verify")
    check.add_argument("directory")
    for command in ("restore-files", "restore-uploads"):
        restore = commands.add_parser(command)
        restore.add_argument("--target-root", default="/app")
    args = parser.parse_args()
    if args.command == "create":
        create_manifest(args)
    elif args.command == "verify":
        verify(args.directory)
    else:
        restore_stream(sys.stdin.buffer, args.target_root, args.command == "restore-uploads")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, tarfile.TarError) as error:
        print(f"Backup operation failed: {error}", file=sys.stderr)
        sys.exit(1)
