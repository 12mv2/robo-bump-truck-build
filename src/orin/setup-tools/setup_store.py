# SPDX-License-Identifier: MIT — © 2026 Rooney Industries LLC
"""Durable, content-addressed setup snapshots (stdlib only; no hardware access).

Snapshots describe saved bytes, not evidence that a physical setup was reproduced.
Missing dependencies stay in the immutable manifest. Only an explicit keep outcome
may advance a scenario's known-good pointer.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

SCHEMA_VERSION = 1
_CHUNK = 1024 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{64}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SENSITIVE = {".ssh", ".gnupg", ".aws", ".azure", "credentials", "secrets", ".env"}
_mutex = threading.RLock()
_local = threading.local()


class StoreError(ValueError):
    """Invalid manifest, unsafe path, or failed integrity verification."""


def _json_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StoreError("Snapshot values must be finite JSON data") from exc


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _safe_name(value, kind="identifier"):
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value) or value in {".", ".."}:
        raise StoreError(f"Unsafe {kind}: {value!r}")
    return value


def _no_symlinks(path):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                raise StoreError(f"Symlink path is not allowed: {current}")
        except FileNotFoundError:
            # Remaining components cannot exist beneath a missing parent.
            break


def _absolute_path(value, *, security=True, check_symlinks=True):
    if not isinstance(value, (str, os.PathLike)):
        raise StoreError("File path must be an absolute path")
    raw = os.fspath(value)
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts or "\x00" in raw or path == Path("/"):
        raise StoreError(f"Unsafe file path: {raw!r}")
    if security and (any(p.lower() in _SENSITIVE for p in path.parts)
                     or path.parts[1:2] in [("dev",), ("proc",), ("sys",)]):
        raise StoreError(f"Security path cannot be captured/restored: {path}")
    if check_symlinks:
        _no_symlinks(path)
    return path


def _store_root(store):
    path = Path(os.path.abspath(os.fspath(store)))
    if ".." in Path(os.fspath(store)).parts or path == Path("/"):
        raise StoreError("Unsafe store path")
    _no_symlinks(path)
    return path


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_store(store):
    root = _store_root(store)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for relative in ("blobs/sha256", "snapshots", "refs", "receipts", ".tmp"):
        child = root / relative
        _no_symlinks(child)
        child.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Persist newly created directory entries as well as files published later.
    _fsync_dir(root / "blobs")
    _fsync_dir(root)
    _fsync_dir(root.parent)
    return root


@contextlib.contextmanager
def store_lock(store):
    """Exclusive interprocess lock, reentrant in the calling thread.

    Restore may hold this lock while calling capture_snapshot for its rollback.
    """
    with _mutex:
        root = _ensure_store(store)
        key = str(root)
        held = getattr(_local, "held", None)
        if held is None:
            held = _local.held = {}
        if key in held:
            yield root
            return
        lock_path = root / ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StoreError("Store lock must be a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            held[key] = fd
            try:
                yield root
            finally:
                del held[key]
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_json(path, payload, *, immutable=True):
    """Publish only a completely written and fsynced JSON file."""
    _no_symlinks(path)
    data = _json_bytes(payload) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444 if immutable else 0o600)
            os.fsync(stream.fileno())
        if immutable:
            os.link(temporary, path)  # Fails rather than overwriting an existing record.
        else:
            os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_json(path):
    _no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StoreError(f"Expected regular JSON file: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None
            return json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StoreError(f"Invalid stored JSON: {path}") from exc
    finally:
        if fd is not None:
            os.close(fd)


def blob_path(store, sha256):
    if not isinstance(sha256, str) or not _HASH_RE.fullmatch(sha256):
        raise StoreError("Invalid blob hash")
    result = _store_root(store) / "blobs" / "sha256" / sha256[:2] / sha256
    _no_symlinks(result)
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise StoreError(f"Expected regular file: {path}")
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _save_blob(root, source):
    _no_symlinks(source)
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    temporary = None
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise StoreError(f"Capture only supports regular files: {source}")
        fd, temporary = tempfile.mkstemp(prefix="blob-", dir=root / ".tmp")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as out, os.fdopen(source_fd, "rb") as inp:
            source_fd = None
            for chunk in iter(lambda: inp.read(_CHUNK), b""):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(inp.fileno())
            current = source.lstat()
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if signature(before) != signature(after) or signature(after) != signature(current):
                raise StoreError(f"File changed during capture: {source}")
            out.flush()
            os.fchmod(out.fileno(), 0o444)
            os.fsync(out.fileno())
        sha = digest.hexdigest()
        destination = blob_path(root, sha)
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        _fsync_dir(destination.parent.parent)
        if destination.exists():
            if _hash_file(destination) != (sha, size):
                raise StoreError(f"Existing blob failed verification: {sha}")
        else:
            os.link(temporary, destination)
            _fsync_dir(destination.parent)
        return {"sha256": sha, "size": size, "mode": stat.S_IMODE(before.st_mode),
                "blob": str(destination.relative_to(root))}
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if temporary is not None:
            os.unlink(temporary)


def _digest_record(record):
    body = {key: value for key, value in record.items() if key not in {"id", "snapshot_digest"}}
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def write_receipt(store, kind, payload):
    """Write an immutable fsynced operation receipt; return its Path."""
    _safe_name(kind, "receipt kind")
    if not isinstance(payload, dict):
        raise StoreError("Receipt payload must be an object")
    with store_lock(store) as root:
        now = _now()
        stamp = dt.datetime.fromisoformat(now).strftime("%Y%m%dT%H%M%S.%fZ")
        path = root / "receipts" / f"{kind}-{stamp}-{uuid.uuid4().hex}.json"
        _atomic_json(path, {"schema_version": SCHEMA_VERSION, "kind": kind,
                            "created_at": now, "payload": payload})
        return path


def capture_snapshot(store, files, metadata, observations=None):
    """Save actual immutable bytes; missing required files make capture incomplete.

    Each file is {path, role, required=True, target_path=path}. Metadata and
    observations are preserved as JSON, including exact command argv and gaps.
    """
    if not isinstance(files, list) or not isinstance(metadata, dict):
        raise StoreError("files must be a list and metadata must be an object")
    if observations is not None and not isinstance(observations, dict):
        raise StoreError("observations must be an object")
    # Copy JSON data so later caller mutation cannot alter the returned record.
    metadata = json.loads(_json_bytes(metadata))
    observations = json.loads(_json_bytes(observations or {}))
    with store_lock(store) as root:
        manifest, targets = [], set()
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("role"), str) or not item["role"].strip():
                raise StoreError("Each file requires a nonempty role")
            source = _absolute_path(item.get("path"))
            target = _absolute_path(item.get("target_path", source))
            for path in (source, target):
                if path == root or root in path.parents:
                    raise StoreError("Snapshot sources/targets cannot point into their store")
            if str(target) in targets:
                raise StoreError(f"Duplicate snapshot target: {target}")
            targets.add(str(target))
            required = item.get("required", True)
            if not isinstance(required, bool):
                raise StoreError("required must be boolean")
            entry = {"path": str(source), "target_path": str(target), "role": item["role"],
                     "required": required, "status": "present"}
            try:
                entry.update(_save_blob(root, source))
            except FileNotFoundError:
                entry["status"] = "missing"
            manifest.append(entry)
        missing = [e["target_path"] for e in manifest if e["required"] and e["status"] == "missing"]
        record = {"schema_version": SCHEMA_VERSION, "created_at": _now(),
                  "complete": not missing, "file_capture_complete": not missing,
                  "completeness_scope": "required_file_bytes", "missing_required": missing,
                  "files": manifest, "metadata": metadata, "observations": observations}
        digest = _digest_record(record)
        stamp = dt.datetime.fromisoformat(record["created_at"]).strftime("%Y%m%dT%H%M%S.%fZ")
        record.update(id=f"{stamp}-{digest}", snapshot_digest=digest)
        _atomic_json(root / "snapshots" / f"{record['id']}.json", record)
        write_receipt(root, "capture", {"snapshot_id": record["id"], "snapshot_digest": digest,
                                        "file_capture_complete": not missing,
                                        "missing_required": missing})
        return record


def load_snapshot(store, snapshot_id):
    """Load an immutable manifest and validate its schema and content digest."""
    if not isinstance(snapshot_id, str) or not _ID_RE.fullmatch(snapshot_id):
        raise StoreError("Invalid snapshot identifier")
    root = _store_root(store)
    record = _read_json(root / "snapshots" / f"{snapshot_id}.json")
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise StoreError("Unsupported snapshot schema")
    if record.get("id") != snapshot_id or record.get("snapshot_digest") != _digest_record(record):
        raise StoreError("Snapshot digest mismatch")
    if not snapshot_id.endswith("-" + record["snapshot_digest"]):
        raise StoreError("Snapshot identity mismatch")
    if not isinstance(record.get("metadata"), dict) or not isinstance(record.get("observations"), dict):
        raise StoreError("Invalid snapshot metadata/observations")
    try:
        created = dt.datetime.fromisoformat(record["created_at"])
        if created.utcoffset() != dt.timedelta(0):
            raise ValueError("timestamp must be UTC")
        expected_id = created.strftime("%Y%m%dT%H%M%S.%fZ") + "-" + record["snapshot_digest"]
        if expected_id != snapshot_id:
            raise ValueError("timestamp identity mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        raise StoreError("Invalid snapshot timestamp") from exc
    if not isinstance(record.get("files"), list):
        raise StoreError("Invalid snapshot file manifest")
    targets, missing = set(), []
    for entry in record["files"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("role"), str) or not entry["role"].strip():
            raise StoreError("Invalid file role")
        # A captured source or destination can disappear or change into a symlink
        # later; manifest loading must not depend on the current source filesystem.
        for key in ("path", "target_path"):
            value = entry.get(key)
            manifest_path = _absolute_path(value, check_symlinks=False)
            if manifest_path == root or root in manifest_path.parents:
                raise StoreError("Manifest path points into store")
        if entry["target_path"] in targets:
            raise StoreError("Duplicate manifest target")
        targets.add(entry["target_path"])
        if not isinstance(entry.get("required"), bool):
            raise StoreError("Invalid required flag")
        if entry.get("status") == "missing":
            if any(key in entry for key in ("sha256", "size", "mode", "blob")):
                raise StoreError("Missing dependency cannot claim saved bytes")
            if entry["required"]:
                missing.append(entry["target_path"])
        elif entry.get("status") == "present":
            sha = entry.get("sha256")
            path = blob_path(root, sha)
            if entry.get("blob") != str(path.relative_to(root)):
                raise StoreError("Invalid blob path")
            if type(entry.get("size")) is not int or entry["size"] < 0:
                raise StoreError("Invalid file size")
            if type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o7777:
                raise StoreError("Invalid file mode")
        else:
            raise StoreError("Invalid file capture status")
    complete = not missing
    if (record.get("missing_required") != missing or type(record.get("complete")) is not bool
            or record["complete"] != complete or type(record.get("file_capture_complete")) is not bool
            or record["file_capture_complete"] != complete
            or record.get("completeness_scope") != "required_file_bytes"):
        raise StoreError("Invalid file completeness claim")
    return record


def verify_snapshot(store, snapshot_id):
    """Validate manifest and every stored blob, streaming large model files."""
    record = load_snapshot(store, snapshot_id)
    checked = set()
    for entry in record["files"]:
        if entry["status"] != "present":
            continue
        identity = (entry["sha256"], entry["size"])
        if identity not in checked:
            try:
                actual = _hash_file(blob_path(store, entry["sha256"]))
            except FileNotFoundError as exc:
                raise StoreError(f"Missing stored blob: {entry['sha256']}") from exc
            if actual != identity:
                raise StoreError(f"Stored blob digest/size mismatch: {entry['sha256']}")
            checked.add(identity)
    return record


def list_refs(store, scenario=None):
    """Return {scenario: reference-document}; absent scenarios return an empty map."""
    root = _store_root(store)
    if scenario is not None:
        paths = [root / "refs" / f"{_safe_name(scenario, 'scenario')}.json"]
    else:
        _no_symlinks(root / "refs")
        paths = sorted((root / "refs").glob("*.json"))
    result = {}
    for path in paths:
        if not path.exists():
            continue
        document = _read_json(path)
        _safe_name(path.stem, "scenario")
        if (not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION
                or document.get("scenario") != path.stem):
            raise StoreError("Invalid scenario reference document")
        for kind in ("known_good", "last_tested"):
            ref = document.get(kind)
            if ref is not None:
                if (not isinstance(ref, dict) or not isinstance(ref.get("snapshot_id"), str)
                        or not _ID_RE.fullmatch(ref["snapshot_id"])):
                    raise StoreError("Invalid scenario snapshot reference")
                outcome = ref.get("outcome")
                if kind == "known_good" and (not isinstance(outcome, dict) or outcome.get("action") != "keep"
                        or any(not isinstance(outcome.get(k), str) or not outcome[k].strip()
                               for k in ("note", "evidence_run"))):
                    raise StoreError("Invalid known-good keep outcome")
        result[path.stem] = document
    return result


def update_ref(store, scenario, snapshot_id, kind, outcome=None):
    """Advance one pointer without conflating last-tested and explicitly kept."""
    _safe_name(scenario, "scenario")
    if kind not in {"known_good", "last_tested"}:
        raise StoreError("Reference kind must be known_good or last_tested")
    if outcome is not None and not isinstance(outcome, dict):
        raise StoreError("Outcome must be an object")
    if kind == "known_good" and (not outcome or outcome.get("action") != "keep"
            or any(not isinstance(outcome.get(k), str) or not outcome[k].strip()
                   for k in ("note", "evidence_run"))):
        raise StoreError("Known-good promotion requires explicit keep, outcome note and evidence_run")
    with store_lock(store) as root:
        record = verify_snapshot(root, snapshot_id)
        if kind == "known_good" and not record["file_capture_complete"]:
            raise StoreError("Incomplete file capture cannot be promoted to known_good")
        document = list_refs(root, scenario).get(scenario, {
            "schema_version": SCHEMA_VERSION, "scenario": scenario,
            "known_good": None, "last_tested": None})
        ref = {"snapshot_id": snapshot_id, "updated_at": _now(),
               "outcome": json.loads(_json_bytes(outcome))}
        receipt = write_receipt(root, "ref-update", {"phase": "intent", "scenario": scenario, "kind": kind,
                                "before": document[kind], "after": ref})
        ref["receipt"] = str(receipt.relative_to(root))
        document[kind] = ref
        _atomic_json(root / "refs" / f"{scenario}.json", document, immutable=False)
        return ref
