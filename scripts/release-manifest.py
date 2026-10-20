#!/usr/bin/env python3
"""release-manifest.py - check a staging dir of scrubbed sample data and write the manifest a
release upload would take. stdlib only. Nothing here uploads, edits or deletes anything.

usage: release-manifest.py STAGING_DIR --descriptions SIDECAR --out MANIFEST [--denylist FILE] [--sha-manifest FILE]

STAGING_DIR   the output of scripts/scrub-run.py (plus any image/video you added).
SIDECAR       a UTF-8 text file OUTSIDE STAGING_DIR, one line per shipped file:
                  <path relative to STAGING_DIR, '/' separators><TAB><one-line description>
              Blank lines and lines starting with '# ' are ignored. Every file needs exactly one
              line; a line naming a file that is not there, a duplicate, a '..' or absolute path,
              or an empty description is refused. Descriptions are scanned like text.
MANIFEST      written OUTSIDE STAGING_DIR, only when there are no refusals. JSON:
                  {"files": [{"path", "bytes", "sha256", "description"}, ...], "total_bytes": N}
              sorted by path. An existing MANIFEST is replaced via a temp file and rename.
--denylist    default: the gate's denylist (scripts/gate_lib.py DENY_FILE).
--sha-manifest default: scripts/binary-manifest.txt (gate_lib.load_manifest: '<sha256>  <path>' lines). A .npz is
              opaque content and is admitted only when its staging-relative path AND sha256 are listed there.

Refused (all listed, exit 1; nothing written):
  - a file with no sidecar description (over BIG_FILE_MB it is reported as its own reason)
  - a path or file name hitting the gate's path rules or denylist
  - an image or video that is not exactly the JPEG / PNG / MP4 structure scripts/strip-image-metadata.sh emits
    (gate_lib.image_problems -> scripts/media_walk.py: any extra chunk, marker, box or NAL type, trailer, ICC,
    audio, other format, unreadable)
  - a symlink (any; one resolving outside STAGING_DIR is reported as outside), or any file whose
    real path is not under STAGING_DIR, or a non-regular file
  - a text file failing the gate's text scan (denylist, base64-like run, data: URI, ...)
  - a file that is neither text, image/video, nor .npz (listed)
  - a .npz whose path+sha256 is not in the SHA manifest; a .npz that is not a zip of plain numeric .npy members;
    any unreadable directory or file
Text means valid UTF-8 with no NUL byte, or UTF-16 with a BOM; every such file is text-scanned whatever
its extension or magic bytes (an image-named text file is scanned as text AND checked as an image).
Images/videos: the gate's container walk (gate_lib.image_problems; one rule, no copy here; nothing is decoded for meaning).
.npz (any case): only members named [A-Za-z0-9_.-]+.npy holding a plain {descr, fortran_order, shape}
header with a numeric/bool/datetime dtype; member names, zip/member comments and each member's raw header
text are scanned (denylist and plain-text scan); nothing else in a member is decoded.
Each file is opened once; its size, hash and scan all come from that one read. The manifest is the
whole truth: the upload MUST take exactly the manifest's list of files (never the directory, which may
change afterwards) and the uploader should verify each sha256.
Exit: 0 ok, 1 refusals, 2 usage or denylist error. Private strings are never printed: denylist hits
are named by denylist line number and a hitting file name is shown as withheld."""
import argparse
import contextlib
import ast
import hashlib
import io
import json
import os
import re
import stat
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_lib as g  # noqa: E402


def read_sidecar(path, deny):
    """-> ({relpath: description}, [refusal strings])."""
    entries, refusals = {}, []
    text = Path(path).read_text(encoding="utf-8")
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.startswith("# "):
            continue
        rel, sep, desc = raw.partition("\t")
        rel, desc = rel.strip(), desc.strip()
        parts = rel.split("/")
        if not sep or not rel or not g.normalise(desc).strip():
            refusals.append("sidecar line %d: needs '<path><TAB><description>'" % n)
        elif rel.startswith("/") or ".." in parts or "" in parts or "." in parts or "\\" in rel:
            refusals.append("sidecar line %d: path must be relative, with no '..', '.', empty or backslash part" % n)
        elif rel in entries:
            refusals.append("sidecar line %d: duplicate entry for %s" % (n, g.shown(rel, deny)))
        else:
            probs = g.text_problems(desc, deny, "sidecar line %d" % n)
            if probs:
                refusals += probs
            else:
                entries[rel] = desc
    return entries, refusals


def walk(root, errors):
    """-> sorted [relpath of every non-directory entry]; a symlinked directory is listed as one entry.
    A directory that cannot be listed is appended to errors (a refusal), never skipped."""
    files = []

    def onerror(exc):
        errors.append("unreadable directory (%s): %s" % (type(exc).__name__, os.path.relpath(exc.filename or root, root)))

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=onerror):
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                dirnames.remove(d)
                files.append(os.path.relpath(full, root))   # judged below as a symlink
        files += [os.path.relpath(os.path.join(dirpath, f), root) for f in filenames]
    return sorted(f.replace(os.sep, "/") for f in files)


def decode_text(data):
    """The decoded text of a NUL-free UTF-8 file, or of a UTF-16 file with a BOM; else None."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


NPY_MEMBER = re.compile(r"[A-Za-z0-9_.-]+\.npy")
NPY_DESCR = re.compile(r"[<>|=][biufcMm]\d+(\[[a-zA-Z]+\])?")


def npz_problems(data, deny, name):
    """Open the zip: only plain numeric/bool/datetime .npy members, names and comments scanned."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return ["not a readable zip despite .npz name: " + name]
    probs, seen = [], set()
    comments = [("zip comment", zf.comment)] + [("member comment", i.comment) for i in zf.infolist()]
    for what, raw in comments:
        if raw:
            probs += g.bytes_problems(raw, deny, "%s in %s" % (what, name))
    for info in zf.infolist():
        mname = info.filename
        probs += g.text_problems(mname, deny, "a member name in %s" % name)
        if not NPY_MEMBER.fullmatch(mname):
            probs.append("npz member name not allowed (only plain *.npy): %s in %s" % (g.shown(mname, deny), name))
            continue
        if mname in seen:
            probs.append("npz has a duplicate member name in " + name)
        seen.add(mname)
        if info.flag_bits & 1:
            probs.append("npz member is encrypted in " + name)
            continue
        try:
            with zf.open(info) as fh:
                head = fh.read(10)
                if head[:6] != b"\x93NUMPY" or len(head) < 10 or head[6] not in (1, 2, 3):
                    raise ValueError("bad magic")
                if head[6] == 1:
                    hlen = struct.unpack("<H", head[8:10])[0]
                    hdr = fh.read(hlen)
                else:
                    hdr = head[8:10] + fh.read(2)
                    hlen = struct.unpack("<I", hdr[:4])[0]
                    hdr = hdr[4:] + fh.read(max(hlen - 2, 0))
                probs += g.bytes_problems(hdr, deny, "an npy header in %s" % name)
                meta = ast.literal_eval(hdr.decode("latin-1").strip())
            ok = (isinstance(meta, dict) and set(meta) == {"descr", "fortran_order", "shape"}
                  and isinstance(meta["descr"], str) and NPY_DESCR.fullmatch(meta["descr"]))
        except Exception:
            ok = False
        if not ok:
            probs.append("npz member is not a plain numeric/bool/datetime .npy array (object, string or unreadable): %s in %s"
                         % (mname, name))
    return probs


def read_once(full):
    """(size, data) from ONE open: no symlink follow, regular files only. Raises OSError/ValueError."""
    fd = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        return st.st_size, fh.read()


def check_file(root, rel, descs, deny, manifest):
    """-> (record or None, [refusals]) for one staged file. Size and hash come from one read."""
    name = g.shown(rel, deny)
    full = os.path.join(root, *rel.split("/"))
    probs = g.scan_path(rel, deny)
    if any(ord(c) < 32 or ord(c) == 127 for c in rel):
        probs.append("control character in file name: " + name)
    if os.path.islink(full):
        real = os.path.realpath(full)
        if real != root and not real.startswith(root + os.sep):
            return None, probs + ["symlink resolves outside the staging dir: " + name]
        return None, probs + ["symlink (not shipped): " + name]
    if not os.path.realpath(full).startswith(root + os.sep):
        return None, probs + ["file is not under the staging dir: " + name]
    try:
        size, data = read_once(full)
    except ValueError:
        return None, probs + ["not a regular file: " + name]
    except OSError as exc:
        return None, probs + ["unreadable file (%s): %s" % (type(exc).__name__, name)]
    desc = descs.get(rel)
    if desc is None:
        over = g.size_problem(rel, size)
        probs.append("%s: %s" % ("over %d MB and no description entry" % g.BIG_FILE_MB if over else "no description entry", name))
    text = decode_text(data)
    img = g.is_image(rel, data)
    npz = rel.lower().endswith(".npz")
    if text is not None:
        probs += g.text_problems(text, deny, name)
    if img:
        probs += g.image_problems(rel, data, deny)
    if npz:
        if (rel, hashlib.sha256(data).hexdigest()) not in manifest:
            probs.append(".npz is opaque content and its path+sha256 is not in the SHA manifest: " + name)
        probs += npz_problems(data, deny, name)
    if text is None and not img and not npz:
        probs.append("not text, image/video or .npz (type not shippable): " + name)
    if probs:
        return None, probs
    return {"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "description": desc}, []


def build(staging, sidecar, deny, manifest=None):
    """-> (records, refusals). Pure: writes nothing. manifest = gate_lib.load_manifest() set by default."""
    manifest = g.load_manifest() if manifest is None else manifest
    root = os.path.realpath(staging)
    refusals = []
    if os.path.realpath(sidecar).startswith(root + os.sep):
        refusals.append("the sidecar is inside the staging dir; move it outside")
    descs, r = read_sidecar(sidecar, deny)
    refusals += r
    files = walk(root, refusals)
    if not files:
        refusals.append("staging dir holds no files")
    records = []
    for rel in files:
        rec, probs = check_file(root, rel, descs, deny, manifest)
        refusals += probs
        if rec:
            records.append(rec)
    for rel in sorted(set(descs) - set(files)):
        refusals.append("sidecar entry for a file that is not in the staging dir: " + g.shown(rel, deny))
    return records, refusals


def main(argv=None):
    ap = argparse.ArgumentParser(description="check a staging dir and write a release manifest")
    ap.add_argument("staging")
    ap.add_argument("--descriptions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--denylist", default=str(g.DENY_FILE))
    ap.add_argument("--sha-manifest", default=str(g.MANIFEST_FILE))
    a = ap.parse_args(argv)
    root = os.path.realpath(a.staging)
    if not os.path.isdir(root):
        print("not a directory: " + a.staging, file=sys.stderr)
        return 2
    out = os.path.realpath(a.out)
    if os.path.isdir(out):
        print("--out is an existing directory: " + a.out, file=sys.stderr)
        return 2
    if out == root or out.startswith(root + os.sep):
        print("--out must be outside the staging dir", file=sys.stderr)
        return 2
    try:
        deny = g.load_denylist(a.denylist)
        records, refusals = build(a.staging, a.descriptions, deny, g.load_manifest(a.sha_manifest))
    except (g.GateError, OSError, UnicodeDecodeError) as exc:
        print("error: %s" % type(exc).__name__ + (": %s" % exc if isinstance(exc, g.GateError) else ""), file=sys.stderr)
        return 2
    total = sum(r["bytes"] for r in records)
    if refusals:
        for r in refusals:
            print("REFUSED: " + r)
        print("files: %d  total bytes: %d  refusals: %d  (no manifest written)" % (len(records), total, len(refusals)))
        return 1
    if not os.path.isdir(os.path.dirname(out)):
        print("--out directory does not exist: " + os.path.dirname(a.out), file=sys.stderr)
        return 2
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".manifest-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"files": records, "total_bytes": total}, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    print("files: %d  total bytes: %d  refusals: 0  manifest: %s" % (len(records), total, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
