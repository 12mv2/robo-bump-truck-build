#!/usr/bin/env python3
"""gate_lib.py - the one leak-gate implementation shared by scripts/check.py and the three hooks in
scripts/hooks (redesigned 2026-10-10).

Threat model: an accidental leak by a cooperating author, not an adversary. The gate DECODES
NOTHING. It reads plain text, normalised once, and refuses what it cannot read:
  - every blob, every commit object in full raw form (author and committer name and email, every
    header, the whole message), every path, every ref name, every tag object (nested tags too);
  - a text containing a base64-like run over 200 characters (8+ distinct characters), a `data:` URI, or bytes that are not
    UTF-8 is refused as opaque content;
  - a binary (a NUL byte) passes only when its exact path and SHA-256 are listed in
    scripts/binary-manifest.txt;
  - an image or video passes only if it is exactly the JPEG / PNG / MP4 structure that
    scripts/strip-image-metadata.sh emits (scripts/media_walk.py, a pure-python container walk);
  - no path is exempt from any rule.

SENSE, DO NOT ACT: nothing here edits, deletes or commits. The denylist stays OUTSIDE this repo;
hits are reported by denylist line number, never by the private string.

Denylist syntax (one entry per line; a comment is a line starting with '# ', hash and space):
  plain text   substring match on normalised text (whitespace runs collapse to one space, in the
               text and in the entry, so write multi-word entries with single spaces)
  re:<regex>   regex, matched case-insensitively against normalised text
Extra synthetic entries for tests: env GATE_EXTRA_STRINGS (comma or newline separated,
same syntax)."""
import hashlib
import os
import pathlib
import re
import stat
import subprocess
import unicodedata
from collections import namedtuple

HERE = pathlib.Path(__file__).resolve().parent


def find_main_root(here=HERE):
    """The main checkout, however this file was reached (main checkout or any linked worktree): the parent
    of `git rev-parse --git-common-dir`, asked from the directory this file lives in. So the binary manifest
    and the hooks are the main checkout's committed ones from every worktree. Falls back to this file's own
    checkout when git cannot answer (scripts copied outside a repository) or the common dir is not a .git."""
    here = pathlib.Path(here)
    own = here.parent
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")}
    try:
        out = subprocess.run(["git", "-C", str(here), "rev-parse", "--git-common-dir"], capture_output=True,
                             text=True, check=True, env=env).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return own
    if not out:
        return own
    common = pathlib.Path(out)
    common = (common if common.is_absolute() else here / common).resolve()
    return common.parent if common.name == ".git" else own


MAIN_ROOT = find_main_root()
# The denylist is already one absolute path under HOME, identical from every worktree.
DENY_FILE = pathlib.Path.home() / "Projects/clients/robo-bump-truck-build/private-strings.txt"
EXTRA_ENV = "GATE_EXTRA_STRINGS"
# Manifest of record = the main checkout's. Hooks likewise (core.hooksPath is absolute and shared).
MANIFEST_FILE = MAIN_ROOT / "scripts" / "binary-manifest.txt"
HOOKS_DIR = MAIN_ROOT / "scripts" / "hooks"

# PATH rules: a tracked path containing any of these (case-insensitive) is refused. Plain entries are
# substrings; 're:' entries are regexes. These words are path rules only, not denylist strings. (Every
# path is also scanned against the denylist, so a denylist word in a path is refused as well.)
PATH_RULES = ("orchestrator/", "tier0/", "worktrees/", "SECRETARY", r"re:\bhandoff\b",
              "docs/handoffs", "PROCESS-DIARY", "PROCESS-PATTERNS", "SESSION-LOG", "DIARY.md",
              "STATUS.md", "STANDING-ORDERS", "credentials", "imessage", "agreement")

# Expected SHA-256 of each tracked hook. Editing a hook means updating its hash here, in the same commit.
HOOK_SHA256 = {
    "pre-commit": "9b02422450a48e35a56e52ed4b086c7baeaea02c5bbd4b1b2b2494d8977c8487",
    "commit-msg": "1154b7f2237f72b2cf8e46cc5a00691d98dbb6f2e5973ddfcf5af1059cce012c",
    "pre-push": "97c7bfffd6b7b7056a082a0e6e1ae877fb691e94fc2746117e7dd5010937cbfa",
}

BIG_FILE_MB = 20
OPAQUE = "opaque content, the gate cannot read it"

Entry = namedtuple("Entry", "label kind pattern")


class GateError(Exception):
    pass


# ---------------------------------------------------------------- normalisation (once)

class _CfTable(dict):
    """str.translate table that drops Unicode format characters (Cf: zero-width space and joiners,
    BOM, word joiner, U+00AD soft hyphen, ...). Each code point is classified once and cached."""

    def __missing__(self, cp):
        res = None if unicodedata.category(chr(cp)) == "Cf" else chr(cp)
        self[cp] = res
        return res


_CF = _CfTable()


_WS = re.compile(r"\s+")


def normalise(text):
    """Strip Cf characters, NFKC, casefold, then collapse every whitespace run to one space (so a
    line reflow, a tab or a double space cannot split a multi-word entry)."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text.translate(_CF)).casefold())


# ---------------------------------------------------------------- denylist

def parse_entries(lines, origin, stats=None):
    """Denylist lines -> entries. A comment is ONLY a line starting with '# ' (hash, space): an
    entry such as '#channel' is an entry. A plain entry that normalises to nothing cannot match
    anything; it is dropped and counted in stats['dropped'] (never silently)."""
    out = []
    stats = {} if stats is None else stats
    for k in ("comments", "blank", "dropped"):
        stats.setdefault(k, 0)
    for i, raw in enumerate(lines, 1):
        if not raw.strip():
            stats["blank"] += 1
            continue
        if raw.startswith("# "):
            stats["comments"] += 1
            continue
        line = raw.strip()
        label = "%s line %d" % (origin, i)
        if line.startswith("re:"):
            pat = line[3:].strip()
            if not pat:
                raise GateError("empty regex at " + label)
            try:
                out.append(Entry(label, "re", re.compile(pat, re.IGNORECASE)))
            except re.error as exc:
                raise GateError("bad regex at %s: %s" % (label, exc))
        else:
            n = normalise(line).strip()
            if n:
                out.append(Entry(label, "sub", n))
            else:
                stats["dropped"] += 1
    return out


def load_denylist(path=DENY_FILE, environ=None, stats=None):
    environ = os.environ if environ is None else environ
    path = pathlib.Path(path)
    if not path.is_file():
        raise GateError("denylist missing or empty: " + str(path))
    entries = parse_entries(path.read_text().splitlines(), "denylist", stats)
    if not entries:
        raise GateError("denylist missing or empty: " + str(path))
    extra = environ.get(EXTRA_ENV, "")
    if extra:
        entries += parse_entries(re.split(r"[,\n]", extra), EXTRA_ENV, stats)
    return entries


def _first_hit(norm, deny):
    for e in deny:
        if (e.pattern in norm) if e.kind == "sub" else e.pattern.search(norm):
            return e.label
    return None


def scan_text(text, deny):
    """The label of the first denylist entry found in the normalised text, else None."""
    return _first_hit(normalise(text), deny)


# ---------------------------------------------------------------- the one text rule

_B64_LIKE = re.compile(r"[a-z0-9+/_-]{201,}")   # applied to casefolded text
_B64_MIN_DISTINCT = 8   # a rule line, banner or table separator has 1-3 distinct characters
_DATA_URI = re.compile(r"\bdata:(?:[a-z0-9.+-]+/[a-z0-9.+-]+)?(?:;[^,\s]*)?,")


def text_problems(text, deny, where):
    """Opaque-content refusals, then the denylist, for one already-decoded text."""
    norm = normalise(text)
    probs = []
    if any(len(set(m.group())) >= _B64_MIN_DISTINCT for m in _B64_LIKE.finditer(norm)):
        probs.append("%s (base64-like run over 200 characters) in %s" % (OPAQUE, where))
    if _DATA_URI.search(norm):
        probs.append("%s (data: URI) in %s" % (OPAQUE, where))
    hit = _first_hit(norm, deny)
    if hit:
        probs.append("private string (%s) in %s" % (hit, where))
    return probs


def bytes_problems(data, deny, where):
    """text_problems for raw bytes; bytes that are not UTF-8 are refused, never decoded loosely."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["%s (not UTF-8) in %s" % (OPAQUE, where)]
    return text_problems(text, deny, where)


# Trailer policy: no model/agent co-author trailer in any commit message. Pattern is built from
# parts so this file never contains the literal phrase (the denylist refuses it).
TRAILER_RE = re.compile(r"co[\s_-]*" + "authored" + r"[\s_-]*by", re.IGNORECASE)


def trailer_problem(text):
    if TRAILER_RE.search(normalise(text)):
        return "commit message carries a co-author trailer (not allowed in this repo)"
    return None


def message_problems(data, deny, where="commit message"):
    """One commit message (or raw commit object), read exactly as given: comment lines, git's
    editor template and anything below a scissors line are all scanned."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["%s (not UTF-8) in %s" % (OPAQUE, where)]
    probs = text_problems(text, deny, where)
    tp = trailer_problem(text)
    if tp:
        probs.append("%s (%s)" % (tp, where))
    return probs


# ---------------------------------------------------------------- paths, blobs, binaries, sizes

def _path_rules():
    out = []
    for rule in PATH_RULES:
        if rule.startswith("re:"):
            out.append((rule, re.compile(rule[3:], re.IGNORECASE)))
        else:
            out.append((rule, normalise(rule)))
    return out


_PATH_RULES = _path_rules()

# Private-name stems: refused when the NORMALISED stem of any path component (casefolded, extension
# stripped, the separators - _ . and space removed) STARTS WITH one of these. Catches session_log.md,
# HANDOFFS.md, Handoff-2026-10-10.md, DIARY.txt, process_diary.md, Standing-Orders.md, an orchestrator/ dir.
PRIVATE_STEM_PREFIXES = ("handoff", "sessionlog", "diary", "processdiary", "orchestrator", "standingorders", "status")
_STEM_SEPARATORS = re.compile(r"[-_. ]")


def private_stem_hit(path):
    """The prefix a path component's normalised stem starts with, else None."""
    comps = normalise(path).split("/")
    for i, comp in enumerate(comps):
        base, ext = os.path.splitext(comp)
        stem = _STEM_SEPARATORS.sub("", base)
        for pre in PRIVATE_STEM_PREFIXES:
            if stem.startswith(pre):
                # "status" only on a final component ending .md/.txt (status.json run logs ship as sample data)
                if pre == "status" and not (i == len(comps) - 1 and ext in (".md", ".txt")):
                    continue
                return pre
    return None


WITHHELD = "<name withheld: it carries a private string>"


def shown(name, deny):
    """A file or ref name as it may be printed: the name itself, unless it carries a denylist hit."""
    return WITHHELD if _first_hit(normalise(name), deny) else name


def scan_path(path, deny):
    probs = []
    npath = normalise(path)
    for rule, pat in _PATH_RULES:
        if (pat in npath) if isinstance(pat, str) else pat.search(npath):
            probs.append("private path (rule %r): %s" % (rule, shown(path, deny)))
            break
    else:
        pre = private_stem_hit(path)
        if pre:
            probs.append("private path (name starts with %r): %s" % (pre, shown(path, deny)))
    probs += text_problems(path, deny, "file name %r" % shown(path, deny))
    return probs


def load_manifest(path=MANIFEST_FILE):
    """{(path, sha256)} from the binary manifest: lines '<sha256>  <path>', '#' comments.
    A missing manifest is an error (fail closed); an empty one allows no binary."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise GateError("binary manifest missing: " + str(path))
    out = set()
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-f]{64})\s+(\S.*)$", line)
        if not m:
            raise GateError("bad binary manifest line %d" % i)
        out.add((m.group(2), m.group(1)))
    return frozenset(out)


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".tif", ".tiff", ".webp", ".gif", ".bmp",
              ".dng", ".jxl")
VIDEO_EXTS = (".mov", ".mp4", ".m4v")


def _load_media_walk():
    """media_walk.py sits next to this file; load it by path so it does not depend on sys.path (hooks, check.py, tests)."""
    import importlib.util
    import sys
    mod = sys.modules.get("media_walk")
    if mod is None or getattr(mod, "__file__", None) != str(HERE / "media_walk.py"):
        spec = importlib.util.spec_from_file_location("media_walk", HERE / "media_walk.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["media_walk"] = mod
        spec.loader.exec_module(mod)
    return mod


def is_image(path, data):
    """An image or video by extension or by magic bytes (a manifest-listed file renamed to .bin is still checked)."""
    if str(path).lower().endswith(IMAGE_EXTS + VIDEO_EXTS):
        return True
    head = data[:16]
    return (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:4] in (b"II*\0", b"MM\0*", b"II+\0", b"MM\0+")
            or (head[:4] == b"RIFF" and data[8:12] == b"WEBP") or head[:6] in (b"GIF87a", b"GIF89a")
            or (head[:2] == b"BM" and data[6:10] == b"\0\0\0\0") or head[:2] == b"\xff\x0a"
            or head[:12] == b"\0\0\0\x0cJXL \r\n\x87\n"
            or (data[4:8] == b"ftyp" and len(data) >= 12))      # every ISO-BMFF brand: HEIF/AVIF, MP4, M4V, MOV


def image_problems(path, data, deny):
    """Problems for one image or video (B50c, pixels-only). The file is admitted only if it is exactly the structure
    scripts/strip-image-metadata.sh emits for its format (JPEG, PNG or MP4): scripts/media_walk.py walks every chunk,
    marker, box and NAL unit type, the order, the fixed fields and the end of file, and refuses any extra chunk,
    trailer, slack, unknown box, ICC profile, audio track or other format (GIF, TIFF, WebP, HEIC, AVIF, ...). Nothing is
    decoded for meaning; no exiftool, so no tag names or values are involved. The message names the structural reason,
    never file content. Fails closed: anything that does not parse is refused."""
    name = shown(path, deny)
    reasons = _load_media_walk().media_problems(path, data)
    if not reasons:
        return []
    return ["image or video is not the structure scripts/strip-image-metadata.sh emits (%s); publish a copy made with "
            "scripts/strip-image-metadata.sh IN OUT: %s" % (reasons[0], name)]


def check_blob(path, data, deny, manifest=None):
    """Problems for one file's content. Text is always scanned. Content the gate cannot read (a NUL
    byte, or not UTF-8) passes only on an exact (path, SHA-256) manifest entry."""
    manifest = load_manifest() if manifest is None else manifest
    unreadable = b"\0" in data
    if not unreadable:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            unreadable = True
    img = image_problems(path, data, deny) if is_image(path, data) else []
    if unreadable and (path, hashlib.sha256(data).hexdigest()) in manifest:
        return img
    if b"\0" in data:
        return img + ["binary file not in the SHA-256 manifest (scripts/binary-manifest.txt): " + shown(path, deny)]
    return img + bytes_problems(data, deny, shown(path, deny))


def size_problem(path, size, limit_mb=None):
    limit_mb = BIG_FILE_MB if limit_mb is None else limit_mb
    if size > limit_mb * 2 ** 20:
        return "file over %s MB (%.1f MB): %s" % (limit_mb, size / 2 ** 20, path)
    return None


# ---------------------------------------------------------------- hooks

def hook_problems(hooks_dir=HOOKS_DIR, expected=None):
    """Each hook exists, is executable and is byte-for-byte the expected file (SHA-256)."""
    expected = HOOK_SHA256 if expected is None else expected
    probs = []
    for name, want in sorted(expected.items()):
        hook = pathlib.Path(hooks_dir) / name
        if not hook.is_file():
            probs.append("scripts/hooks/%s missing" % name)
        elif not os.access(hook, os.X_OK) or not hook.stat().st_mode & stat.S_IXUSR:
            probs.append("scripts/hooks/%s is not executable (git silently skips it)" % name)
        elif hashlib.sha256(hook.read_bytes()).hexdigest() != want:
            probs.append("scripts/hooks/%s does not match its expected SHA-256 (gate_lib.HOOK_SHA256)" % name)
    return probs


# ---------------------------------------------------------------- git helpers

def git_env():
    """Environment for every git read: replace refs (refs/replace/*) are NOT honoured, because pack
    transfer ignores them - the gate must read the objects that are actually pushed."""
    return dict(os.environ, GIT_NO_REPLACE_OBJECTS="1")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True,
                          env=git_env()).stdout


def _tree_problems(repo, treeish, deny, seen, manifest, limit_mb, tag):
    probs = []
    for entry in git(repo, "ls-tree", "-r", "-z", "-l", treeish).split(b"\0"):
        if not entry:
            continue
        meta_b, rawpath = entry.split(b"\t", 1)
        _mode, kind, sha, size = meta_b.decode().split()
        try:
            path = rawpath.decode("utf-8")
        except UnicodeDecodeError:
            probs.append("%s (not UTF-8) in a file name%s" % (OPAQUE, tag))
            continue
        if path not in seen["paths"]:
            seen["paths"].add(path)
            probs += [p + tag for p in scan_path(path, deny)]
        if kind != "blob":
            continue
        bigp = size_problem(path, int(size), limit_mb)
        if bigp:
            probs.append(bigp + tag)
            continue
        if (sha, path) in seen["blobs"]:
            continue
        seen["blobs"].add((sha, path))
        probs += [p + tag for p in check_blob(path, git(repo, "cat-file", "blob", sha), deny, manifest)]
    return probs


def audit_commit(repo, commit, deny, seen, limit_mb=None, manifest=None):
    """The raw commit object (identities, every header, the full message), every path, every blob."""
    manifest = load_manifest() if manifest is None else manifest
    tag = " (commit %s)" % commit[:8]
    probs = message_problems(git(repo, "cat-file", "commit", commit), deny, "commit %s" % commit[:8])
    probs += _tree_problems(repo, commit, deny, seen, manifest, limit_mb, tag)
    return probs


def audit_ref(repo, ref_name, sha, deny, seen=None, manifest=None):
    """The ref name; for a tag object, its full raw content and every nested tag under it. A tag that
    ends on a tree or blob has that content audited too (commits are audited by the caller)."""
    probs = text_problems(ref_name, deny, "ref name %r" % shown(ref_name, deny))
    ref_name = shown(ref_name, deny)
    if set(sha) == {"0"}:
        return probs
    seen = {"paths": set(), "blobs": set()} if seen is None else seen
    obj, depth = sha, 0
    while git(repo, "cat-file", "-t", obj).strip() == b"tag":
        depth += 1
        if depth > 32:
            return probs + ["tag chain under %s deeper than 32" % ref_name]
        raw = git(repo, "cat-file", "tag", obj)
        probs += message_problems(raw, deny, "tag object %s (%s)" % (obj[:8], ref_name))
        first = raw.split(b"\n", 1)[0].split()   # a tag object starts 'object <sha>'
        if len(first) != 2 or first[0] != b"object":
            return probs + ["unreadable tag object %s (%s)" % (obj[:8], ref_name)]
        obj = first[1].decode()
    kind = git(repo, "cat-file", "-t", obj).strip()
    if kind == b"tree":
        manifest = load_manifest() if manifest is None else manifest
        probs += _tree_problems(repo, obj, deny, seen, manifest, None, " (tree under %s)" % ref_name)
    elif kind == b"blob":
        manifest = load_manifest() if manifest is None else manifest
        probs += check_blob("<blob under %s>" % ref_name, git(repo, "cat-file", "blob", obj), deny, manifest)
    return probs


def audit_commits(repo, commits, deny, limit_mb=None, manifest=None):
    manifest = load_manifest() if manifest is None else manifest
    seen = {"paths": set(), "blobs": set()}
    probs = []
    for c in commits:
        probs += audit_commit(repo, c, deny, seen, limit_mb, manifest)
    return probs


def audit_push(repo, lines, deny, manifest=None):
    """What the pre-push hook checks, from its stdin lines (local_ref local_sha remote_ref remote_sha):
    both ref names, tag objects, and every commit in the pushed range."""
    manifest = load_manifest() if manifest is None else manifest
    probs, seen = [], {"paths": set(), "blobs": set()}
    for line in lines:
        if not line.strip():
            continue
        local_ref, local_sha, remote_ref, remote_sha = line.split()
        if set(local_sha) == {"0"}:
            continue  # branch delete
        probs += audit_ref(repo, local_ref, local_sha, deny, seen, manifest)
        if remote_ref != local_ref:
            probs += audit_ref(repo, remote_ref, "0" * 40, deny)
        rng = local_sha if set(remote_sha) == {"0"} else remote_sha + ".." + local_sha
        try:
            commits = git(repo, "rev-list", rng).decode().split()
        except subprocess.CalledProcessError:
            commits = git(repo, "rev-list", local_sha).decode().split()
        for c in commits:
            probs += audit_commit(repo, c, deny, seen, None, manifest)
    return probs


def audit_files(repo, files, deny, limit_mb=None, manifest=None):
    """Working-tree files (tracked + pending): path rules, blob rules, size."""
    manifest = load_manifest() if manifest is None else manifest
    probs = []
    for f in files:
        probs += scan_path(f, deny)
        fp = pathlib.Path(repo) / f
        if fp.is_symlink():
            probs += text_problems(os.readlink(fp), deny, "symlink target of " + shown(f, deny))
            continue
        if not fp.is_file():
            continue
        sp = size_problem(f, fp.stat().st_size, limit_mb)
        if sp:
            probs.append(sp)
            continue
        probs += check_blob(f, fp.read_bytes(), deny, manifest)
    return probs


def audit_staged(repo, deny, limit_mb=None, manifest=None):
    """The index, not the working tree: each staged blob is read by object id from
    `diff --cached --raw` (never by path); type changes (T) included, so a symlink's target is
    scanned; gitlinks (160000) have no blob."""
    manifest = load_manifest() if manifest is None else manifest
    probs = []
    parts = git(repo, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames",
                "--diff-filter=ACMRT").split(b"\0")
    for meta, rawpath in zip(parts[0::2], parts[1::2]):
        _om, mode, _osha, sha, _st = meta.lstrip(b":").decode().split()
        try:
            f = rawpath.decode("utf-8")
        except UnicodeDecodeError:
            probs.append("%s (not UTF-8) in a staged file name" % OPAQUE)
            continue
        probs += scan_path(f, deny)
        if mode == "160000":
            continue
        data = git(repo, "cat-file", "blob", sha)
        sp = size_problem(f, len(data), limit_mb)
        if sp:
            probs.append(sp)
            continue
        probs += check_blob(f, data, deny, manifest)
    return probs


def oversize(repo, files, limit_mb=None):
    out = []
    for f in files:
        fp = pathlib.Path(repo) / f
        if fp.is_file():
            p = size_problem(f, fp.stat().st_size, limit_mb)
            if p:
                out.append(p)
    return out
