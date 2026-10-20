# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""What code is actually on disk, and what code is actually running.

Born 2026-09-14 for the Phase 1A deployment gate. Two failures it exists to stop:

  * `deploy_live.sh` shipped `live_trail.py` and `output_owner.py` but not
    `recovery.py`, `batch.py` or `cell_plan.py`. A deploy that omits a module the
    entry point imports is not a deploy, and nothing noticed.
  * The active candidate `cellplan-d9c35972788f/` was edited in place three times
    after it was created, so its version-shaped name stopped meaning anything.

`closure()` answers "what does this entry point need"; `manifest()`/`digest()`
answer "what exactly is here"; `running()` answers "what did this process load".
A deploy is complete when closure() is a subset of the manifest, and honest when
the running digest matches the verified package digest.

No imports of the modules it inspects: it reads source text, so it can be run
against a package it must not execute.
"""
import ast
import hashlib
import json
import os
import sys
from pathlib import Path

SCHEMA = 1


def _sibling_imports(path):
    """Module names imported by `path` that are plain siblings of it.

    Static, so an import guarded by a try/except or hidden in a function still
    counts. Over-inclusion is the safe direction for a completeness check.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"), str(path))
    except SyntaxError as exc:
        raise ValueError(f"{path}: will not parse: {exc}") from exc
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def closure(entries, root):
    """Every sibling .py under `root` transitively reachable from `entries`.

    Returns names relative to root, entries included. Raises if an entry is missing.
    """
    root = Path(root).resolve()
    pending, seen = [], set()
    for entry in entries:
        p = (root / entry).resolve() if not Path(entry).is_absolute() else Path(entry).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"entry point not found: {p}")
        pending.append(p)
    while pending:
        path = pending.pop()
        rel = path.relative_to(root).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        for name in _sibling_imports(path):
            sibling = root / (name + ".py")
            if sibling.is_file():
                pending.append(sibling.resolve())
    return sorted(seen)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root, names):
    """{relative name: sha256} for every name, which must all exist."""
    root = Path(root)
    out = {}
    for name in sorted(names):
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"missing from package: {name}")
        out[name] = sha256_file(path)
    return out


def digest(entries):
    """One digest over the whole manifest. Order-independent, content-only."""
    h = hashlib.sha256()
    h.update(f"package-id v{SCHEMA}\n".encode())
    for name in sorted(entries):
        h.update(f"{name} {entries[name]}\n".encode())
    return h.hexdigest()


def running(entry=None):
    """Identity of the code THIS process loaded, not of the files on disk now.

    Hashes the entry script plus every already-imported module whose file sits in
    the entry's directory. A module edited after import still hashes to its
    on-disk bytes, so this is 'the package as it stands', not 'the bytes executed'
    -- say so rather than over-claiming. Returns (digest, manifest) or (None, {}).
    """
    try:
        entry = Path(entry or sys.argv[0]).resolve()
        root = entry.parent
        names = {entry.name} if entry.is_file() else set()
        for module in list(sys.modules.values()):
            f = getattr(module, "__file__", None)
            if not f:
                continue
            p = Path(f).resolve()
            if p.parent == root and p.suffix == ".py" and p.is_file():
                names.add(p.name)
        if not names:
            return None, {}
        m = manifest(root, names)
        return digest(m), m
    except (OSError, ValueError):
        return None, {}


def write_manifest(path, name, entries, extra=None):
    payload = dict(schema=SCHEMA, package=name, digest=digest(entries),
                   files=entries, **(extra or {}))
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return payload


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="package identity for the live tooling")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--entry", action="append", default=[], help="entry point (repeatable)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    names = closure(a.entry or ["live_trail.py"], a.root)
    m = manifest(a.root, names)
    if a.json:
        print(json.dumps(dict(digest=digest(m), files=m), indent=2, sort_keys=True))
    else:
        print(f"digest {digest(m)[:12]}  ({len(m)} files)")
        for name in sorted(m):
            print(f"  {m[name][:12]}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
