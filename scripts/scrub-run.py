#!/usr/bin/env python3
"""scrub-run.py - copy the text of a robot run into a staging directory with paths, hosts, users
and IP addresses scrubbed. stdlib only; never modifies the source; never touches images/video.

usage: scrub-run.py SRC_RUN_DIR STAGING_DIR [--host NAME]... [--user NAME]...

Copied:   frames.jsonl and *.json (scrubbed, record by record); *.npz byte-for-byte, only when ALL hold:
          empty zip comment and empty per-member comments; only zip64 extra fields; members laid out
          back to back with nothing before, between or after them; every member name matches
          ^[A-Za-z0-9_.-]+\\.npy$ and is unchanged by the scrub; every .npy header is a plain
          {descr, fortran_order, shape} dict (no comment, no extra keys) with a numeric/bool/datetime dtype.
Skipped:  everything else (images, video, unrecognised files, symlinks, unparsable JSON, ...),
          each listed with the reason. A file whose NAME (or any directory name above it) would be
          changed by the scrub is refused and listed; it is never renamed. Exit 1.
Every file is all-or-nothing: the whole output is built, re-parsed and checked in memory first, then
written through a temp file and renamed. A file that fails (lone surrogate, nesting too deep, a number
that is not strict JSON, ...) is skipped with a reason; staging never holds a partial or 0-byte file.
Rewrites (every string gets EVERY step below, in this order, with no early exit; numbers are never touched):
  nested  a string that parses as a JSON object/array is scrubbed recursively and re-serialised
          (its inner spacing/number text may change; numbers are checked bit-for-bit); else text rules.
  path    /home/<u>/.., /Users/<u>/.., /mnt/<d>/.., /media/<u>/<d>/.., /root/.., /tmp/<d>/..,
          C:\\Users\\<u>\\.., <mounted-volume prefix>/<d>/.. -> relative to the run root, else the basename.
          Run root = SRC itself, or the first path component equal to SRC's directory name (the run
          may have been recorded on another machine). The drive/user segment is scrubbed even when it
          contains spaces (up to the next path separator). A path inside a longer string ends at
          whitespace or a quote; a string that STARTS with such a path is treated as one path.
  host    user@HOST (not WxH@fps / known-pixel-format@rate), host=/hostname: values (also quoted, "hostname": "x"), *.local names, values
          under host-like keys (strings, and every string nested in lists/dicts), any --host NAME -> "host"
  user    USER@host, user=/username: values, values under user-like keys (all nested strings; under owner only the modes legacy/simple survive), any
          --user NAME, and any user name seen in a /home, /Users, /media or C:\\Users path anywhere in
          the run -> "user"
  ip      IPv6 (any valid address, also ::ffff:a.b.c.d and %zone) -> "::"; IPv4 a.b.c.d (octets <= 255) -> "0.0.0.0"
Host/user names are matched as whole tokens: letters and digits bound them, '_' and '-' do not.
Numbers: ints, floats (bit-for-bit), bools and nulls are compared before/after on the RE-PARSED output.
The decimal TEXT of a number may change (1E2 -> 100.0, 1.00000000000000000001 -> 1.0); the value does not.
Input with NaN/Infinity tokens, or a number that overflows to inf (1e400), is refused: output must be
strict JSON. U+2028, U+2029 and U+0085 are escaped in the output so splitlines() never splits a record.
NOT detected: names that appear nowhere in a path, key, user@host or --host/--user (and are shorter than
3 characters or generic when only auto-detected); MAC addresses; FQDN suffixes; ~user; the contents of
zip64 extra fields and of numeric array payloads. The gate (check.py --text) is the backstop, and the
staging dir is yours to review.
Exit: 0 ok, 1 some file skipped for error, 2 refused/usage."""
import argparse
import ast
import ipaddress
import json
import math
import os
import re
import struct
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

HOST_KEYS = {"host", "hostname", "host_name", "node", "node_name", "nodename", "machine", "machine_name"}
USER_KEYS = {"user", "username", "user_name", "login", "owner", "whoami"}
# "owner" is also the controller's --owner mode (live_trail.py: choices=("legacy", "simple")). Only these exact
# values survive under an owner key; anything else (a real owner name, nested or not) -> "user" (B62).
OWNER_MODES = frozenset({"legacy", "simple"})
# left part of a "WxH@fps" / "format@rate" capture-format value; never a bare word (alice@10 is a login)
FORMAT_LEFT = frozenset({"yuyv422", "mjpg", "mjpeg", "nv12", "h264", "h265", "rgb24", "bgr24", "gray"})
GENERIC_USERS = {"user", "root", "shared", "guest", "public", "home", "host"}
CATEGORIES = ("path", "host", "user", "ip")

_VOL = "/Vol" + "umes"   # split so this file does not carry the literal prefix
# one path segment (a drive or user name). Embedded flavour: may contain spaces when a '/' or '\' follows
# and the segment does not end in a space; otherwise a run without whitespace. Whole flavour: anything.
_SEG = r"(?:[^/\\\s\"'](?:[^/\\\"'\n]*[^/\\\s\"'])?(?=[/\\])|[^/\\\s\"']+)"
_WSEG = r"[^/\\]+"


def _prefixes(seg):
    return ["/home/" + seg, "/Users/" + seg, _VOL + "/" + seg, "/mnt/" + seg,
            "/media/" + seg + "(?:/" + seg + ")?", "/tmp/" + seg,
            r"/root(?=[/\\]|[\s\"',;)\]}>]|\Z)", r"[A-Za-z]:[\\/]Users[\\/]" + seg]


_PATH_START = "(?:" + "|".join(_prefixes(_SEG)) + ")"
_WHOLE_START = "(?:" + "|".join(_prefixes(_WSEG)) + ")"
_PATH_TAIL = r"(?:[/\\][^\s\"',;)\]}>]*)?"
PATH_RE = re.compile(r"(?<![\w.~-])(" + _PATH_START + ")(" + _PATH_TAIL + ")")
WHOLE_PATH_RE = re.compile(r"(" + _WHOLE_START + r")((?:[/\\].*)?)\Z", re.S)
HOME_USER_RE = re.compile(r"(?<![\w.~-])(?:/home|/Users|/media|[A-Za-z]:[\\/]Users)[/\\](" + _SEG + ")")
_B = r"(?<![^\W_])"   # token start: '_' counts as a separator
USERHOST_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9_][A-Za-z0-9_.-]*)@([A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9])")
_KV = r"(\s*[\"']?\s*[=:]\s*[\"']?)(?=[A-Za-z0-9._-]*[A-Za-z])([A-Za-z0-9][A-Za-z0-9._-]*)"
HOSTKV_RE = re.compile(r"(?i)" + _B + r"(host(?:_?name)?)" + _KV)
NUMERIC_HOST_RE = re.compile(r"\d+(?:\.\d+)?|\d+x\d+")
RESOLUTION_RE = re.compile(r"\d+x\d+")
USERKV_RE = re.compile(r"(?i)" + _B + r"(user(?:_?name)?)" + _KV)
LOCAL_RE = re.compile(r"(?i)(?<![A-Za-z0-9._-])[a-z0-9_][a-z0-9_-]*(?:\.[a-z0-9_-]+)*\.local(?![A-Za-z0-9_-])")
IP_RE = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d]|\.\d)")
IP6_RE = re.compile(r"(?<![0-9A-Za-z:.])([0-9A-Fa-f:.]{2,})(%[0-9A-Za-z._-]+)?(?![0-9A-Za-z:])")
_SEPS = {"\u2028": "\\u2028", "\u2029": "\\u2029", "\x85": "\\u0085"}


class Refused(Exception):
    pass


class FileSkip(Exception):
    pass


def _forced(key):
    k = key.lower()
    return "host" if k in HOST_KEYS else "user" if k in USER_KEYS else None


class Scrubber:
    def __init__(self, root, hosts=(), users=(), roots=(), auto_users=()):
        self.root = root                      # resolved Path of the source run
        self.roots = sorted({str(root), *map(str, roots)}, key=len, reverse=True)  # literal spellings of it
        self.hosts = [h for h in hosts if h]
        self.users = {u for u in users if u}                  # explicit: always scrubbed
        self.auto_users = {u for u in auto_users if u}        # seen in paths: length/generic filter
        self.counts = Counter()
        self.root_re = self.host_re = self.user_re = None

    # -- helpers ------------------------------------------------------------------------------
    @staticmethod
    def _word_re(names):
        names = sorted(names, key=len, reverse=True)
        return re.compile(r"(?i)(?<![^\W_])(?:%s)(?![^\W_])" % "|".join(re.escape(n) for n in names)) if names else None

    def prepare(self):
        alt = "|".join(re.escape(r.rstrip("/")) for r in self.roots if r.rstrip("/"))
        self.root_re = re.compile(r"(?<![\w.~-])(?:" + alt + r")(?=/|$|[\s\"'\\,;)\]}>])(/[^\s\"'\\,;)\]}>]*)?") if alt else None
        self.host_re = self._word_re(self.hosts)
        auto = {u for u in self.auto_users if len(u) >= 3 and u.lower() not in GENERIC_USERS}
        self.user_re = self._word_re(self.users | auto)

    def rel(self, prefix, tail):
        """Run-relative form of one absolute path, else its basename."""
        full = (prefix + tail).replace("\\", "/").rstrip("/")
        nskip = len([p for p in re.split(r"[\\/]", prefix) if p])
        root = str(self.root)
        if full == root:
            return "."
        if full.startswith(root + "/"):
            return full[len(root) + 1:]
        parts = [p for p in full.split("/") if p]
        if self.root.name in parts[nskip:]:
            rest = parts[parts.index(self.root.name, nskip) + 1:]
            return "/".join(rest) if rest else "."
        return parts[-1] if len(parts) > nskip else ""   # bare prefix: nothing left but the user/drive dir

    def text(self, s):
        """Scrub one string with every rule (no early exit)."""
        if not s:
            return s
        if self.root_re:
            s = self.root_re.sub(lambda m: (self.counts.update(path=1), (m.group(1) or "/").strip("/") or ".")[1], s)
        m = WHOLE_PATH_RE.match(s)
        if m:
            self.counts["path"] += 1
            s = self.rel(m.group(1), m.group(2)) or "."
        else:
            def path_sub(m):
                self.counts["path"] += 1
                return self.rel(m.group(1), m.group(2)) or "."
            s = PATH_RE.sub(path_sub, s)

        def uh(m):
            # WxH@fps / known-pixel-format@rate: not a login, unless it holds a supplied name
            left = m.group(1)
            if (RESOLUTION_RE.fullmatch(left) or left.lower() in FORMAT_LEFT) and NUMERIC_HOST_RE.fullmatch(m.group(2)) and not any(
                    rx and rx.search(m.group(0)) for rx in (self.host_re, self.user_re)):
                return m.group(0)
            self.counts["user"] += 1
            self.counts["host"] += 1
            return "user@host"
        s = USERHOST_RE.sub(uh, s)

        def kv(cat, tok):
            def f(m):
                if m.group(3).lower() == tok:
                    return m.group(0)
                self.counts[cat] += 1
                return m.group(1) + m.group(2) + tok
            return f
        s = HOSTKV_RE.sub(kv("host", "host"), s)
        s = USERKV_RE.sub(kv("user", "user"), s)

        def loc(m):
            self.counts["host"] += 1
            return "host"
        s = LOCAL_RE.sub(loc, s)
        for rx, cat, tok in ((self.host_re, "host", "host"), (self.user_re, "user", "user")):
            if rx:
                def named(m, cat=cat, tok=tok):
                    if m.group(0) == tok:
                        return tok
                    self.counts[cat] += 1
                    return tok
                s = rx.sub(named, s)

        def ip6(m):
            run, zone = m.group(1), m.group(2) or ""
            if run.count(":") < 2:
                return m.group(0)
            for cand in (run, run.rstrip("."), run.rstrip(".:")):
                try:
                    ipaddress.IPv6Address(cand)
                except ValueError:
                    continue
                if cand == "::" and not zone:
                    return m.group(0)
                self.counts["ip"] += 1
                return "::" + run[len(cand):]
            return m.group(0)
        s = IP6_RE.sub(ip6, s)

        def ip(m):
            if any(int(o) > 255 for o in m.group(1).split(".")) or m.group(1) == "0.0.0.0":
                return m.group(0)
            self.counts["ip"] += 1
            return "0.0.0.0"
        return IP_RE.sub(ip, s)

    def nested(self, s):
        """Scrubbed re-serialisation of a string holding a JSON object/array, else None."""
        t = s.strip()
        if len(t) < 2 or t[0] not in "{[":
            return None
        try:
            obj = loads(t)
        except (ValueError, FileSkip):
            return None
        if not isinstance(obj, (dict, list)):
            return None
        before = numbers(obj)
        new = self.value(obj)
        try:
            out = json.dumps(new, ensure_ascii=False, allow_nan=False)
            again = loads(out)
        except ValueError as e:
            raise FileSkip("nested JSON string is not representable as strict JSON (%s)" % e)
        if numbers(again) != before:
            raise FileSkip("numeric values changed inside a nested JSON string")
        return out

    def value(self, v, key=None, force=None):
        """Scrub one JSON value. force = 'host'/'user' when an enclosing key says every string below is one."""
        force = force or (_forced(key) if key is not None else None)
        if key is not None and key.lower() == "owner" and not isinstance(v, (int, float, bool)):
            force = "owner"
        if isinstance(v, str):
            if force == "owner":
                if v in OWNER_MODES:
                    return v
                force = "user"
            if force:
                if v in ("", force):
                    return v
                self.counts[force] += 1
                return force
            n = self.nested(v)
            return n if n is not None else self.text(v)
        if isinstance(v, list):
            return [self.value(x, None, force) for x in v]
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                nk = self.text(k)
                if nk in out:
                    raise FileSkip("scrubbing makes two keys collide (%r)" % nk)
                out[nk] = self.value(x, k, force)
            return out
        return v


# ---------------------------------------------------------------------------- JSON handling

def _no_dups(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise FileSkip("duplicate JSON key %r (a re-serialise would drop data)" % k)
        d[k] = v
    return d


def _no_const(name):
    raise ValueError("non-finite constant %s is not strict JSON" % name)


def loads(text):
    return json.loads(text, object_pairs_hook=_no_dups, parse_constant=_no_const)


def numbers(obj, out=None):
    """Every non-string scalar in document order, floats as exact bits."""
    out = [] if out is None else out
    if isinstance(obj, dict):
        out.append(("obj", len(obj)))
        for v in obj.values():
            numbers(v, out)
    elif isinstance(obj, list):
        out.append(("arr", len(obj)))
        for v in obj:
            numbers(v, out)
    elif isinstance(obj, bool) or obj is None:
        out.append(("lit", obj))
    elif isinstance(obj, int):
        out.append(("int", obj))
    elif isinstance(obj, float):
        out.append(("float", "nan" if math.isnan(obj) else struct.pack(">d", obj)))
    else:
        out.append(("str", None))
    return out


def scrub_doc(sc, doc, where):
    before = numbers(doc)
    new = sc.value(doc)
    if numbers(new) != before:
        raise FileSkip("numeric values changed in %s (scrub bug); file not written" % where)
    return new


def render(sc, doc, where, indent=None):
    """Scrubbed, validated JSON text of one document (strict JSON, no raw line separators)."""
    new = scrub_doc(sc, doc, where)
    try:
        txt = json.dumps(new, ensure_ascii=False, allow_nan=False, indent=indent)
    except ValueError as e:
        raise FileSkip("output of %s is not strict JSON (%s)" % (where, e))
    for ch, esc in _SEPS.items():
        txt = txt.replace(ch, esc)
    try:
        txt.encode("utf-8")
    except UnicodeEncodeError:
        raise FileSkip("%s holds a lone surrogate (not encodable as UTF-8)" % where)
    try:
        back = loads(txt)
    except (ValueError, FileSkip) as e:
        raise FileSkip("output of %s does not re-parse as strict JSON (%s)" % (where, e))
    if numbers(back) != numbers(doc) or back != new:
        raise FileSkip("re-parsed output of %s differs from what was intended; file not written" % where)
    return txt


def iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from iter_strings(x)
    elif isinstance(obj, dict):
        for k, x in obj.items():
            yield k
            yield from iter_strings(x)


def read_docs(path):
    """Yield (kind, payload) per JSONL line / whole JSON file; raises FileSkip if unparsable."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        raise FileSkip("not readable as UTF-8 (%s)" % type(e).__name__)
    if path.name == "frames.jsonl":
        lines = text.split("\n")   # not splitlines(): U+2028 etc. may sit inside a JSON string
        if lines and lines[-1] == "":
            lines.pop()
        for n, line in enumerate(lines, 1):
            if not line.strip():
                yield ("blank", line)
                continue
            try:
                yield ("doc", loads(line))
            except ValueError as e:
                raise FileSkip("line %d is not valid strict JSON (%s)" % (n, e))
    else:
        try:
            yield ("whole", loads(text))
        except ValueError as e:
            raise FileSkip("not valid strict JSON (%s)" % e)


def collect_users(path, found):
    for kind, d in read_docs(path):
        if kind in ("doc", "whole"):
            for s in iter_strings(d):
                for m in HOME_USER_RE.finditer(s):
                    found.add(m.group(1))


def scrub_json_bytes(sc, src):
    """The complete scrubbed output of one JSON/JSONL file, or FileSkip. Nothing is written here."""
    lines = []
    for n, (kind, d) in enumerate(read_docs(src), 1):
        if kind == "blank":
            lines.append("")
        elif kind == "doc":
            lines.append(render(sc, d, "%s record %d" % (src.name, n)))
        else:
            lines.append(render(sc, d, src.name, indent=2))
    if not any(lines):
        raise FileSkip("no records")
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_atomic(dst, data):
    """Write dst completely or not at all (temp file in the same dir, then rename)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".scrub-tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------- npz handling

_NPZ_NAME_RE = re.compile(r"[A-Za-z0-9_.-]+\.npy")
_DESCR_RE = re.compile(r"[<>=|]?[biufcmM]\d*(?:\[[A-Za-z0-9]+\])?")
_HEADER_BYTES_RE = re.compile(rb"[\x20-\x7e]*\n?")
_LOCAL_HDR = struct.Struct("<4s5H3I2H")


def _extra_ok(extra):
    """Only zip64 (id 1) fields of plausible size."""
    i = 0
    while i < len(extra):
        if i + 4 > len(extra):
            return False
        tid, ln = struct.unpack("<HH", extra[i:i + 4])
        if tid != 1 or ln > 28:
            return False
        i += 4 + ln
    return i == len(extra)


def _header_problem(raw, major):
    if not _HEADER_BYTES_RE.fullmatch(raw) or b"#" in raw:
        return "header is not a plain dict (comment or odd bytes)"
    try:
        tree = ast.parse(raw.decode("latin-1" if major < 3 else "utf-8").strip(), mode="eval")
        if not isinstance(tree.body, ast.Dict) or len(tree.body.keys) != 3:
            return "header is not a 3-key dict"
        meta = ast.literal_eval(tree.body)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return "header unreadable"
    if set(meta) != {"descr", "fortran_order", "shape"}:
        return "header keys are not exactly descr/fortran_order/shape"
    descr = meta["descr"]
    if isinstance(descr, list):
        return "structured dtype"
    if not isinstance(descr, str) or not _DESCR_RE.fullmatch(descr):
        return "dtype %r is not numeric/bool/datetime" % (descr if isinstance(descr, str) else type(descr).__name__)
    if not isinstance(meta["fortran_order"], bool):
        return "fortran_order is not a bool"
    shape = meta["shape"]
    if not isinstance(shape, tuple) or not all(type(d) is int and d >= 0 for d in shape):
        return "shape is not a tuple of ints"
    return None


def npz_problem(path, sc=None):
    """None if the archive is a plain numeric npz safe to copy byte-for-byte, else the reason to skip."""
    try:
        data_size = os.path.getsize(path)
        with open(path, "rb") as raw_f, zipfile.ZipFile(path) as zf:
            if zf.comment:
                return "zip comment present"
            infos = zf.infolist()
            if not infos:
                return "empty archive"
            if len({i.filename for i in infos}) != len(infos):
                return "duplicate member names"
            raw_f.seek(max(0, data_size - 22))
            tail = raw_f.read()
            if len(tail) != 22 or tail[:4] != b"PK\x05\x06" or tail[20:22] != b"\0\0":
                return "bytes after the end-of-archive record"
            expect = 0
            for info in sorted(infos, key=lambda i: i.header_offset):
                name = info.filename
                if not _NPZ_NAME_RE.fullmatch(name):
                    return "member %r is not a plain name.npy" % name
                if sc is not None and sc.text(name) != name:
                    return "member name %r would be changed by the scrub" % name
                if info.comment:
                    return "member %r has a comment" % name
                if info.flag_bits & 0x8:
                    return "member %r uses a data descriptor" % name
                if not _extra_ok(info.extra):
                    return "member %r has a non-zip64 extra field" % name
                if info.header_offset != expect:
                    return "bytes between or before members"
                raw_f.seek(info.header_offset)
                lh = raw_f.read(_LOCAL_HDR.size)
                if len(lh) != _LOCAL_HDR.size:
                    return "truncated local header"
                sig, _v, _fl, _c, _t, _d, _crc, csize, _usize, fnlen, exlen = _LOCAL_HDR.unpack(lh)
                if sig != b"PK\x03\x04":
                    return "bad local header"
                if raw_f.read(fnlen) != name.encode("utf-8") or not _extra_ok(raw_f.read(exlen)):
                    return "member %r local header differs or has an extra field" % name
                expect = info.header_offset + _LOCAL_HDR.size + fnlen + exlen + info.compress_size
                with zf.open(info) as fp:
                    head = fp.read(10)
                    if head[:6] != b"\x93NUMPY":
                        return "member %r has no .npy header" % name
                    major = head[6]
                    if major == 1:
                        hlen = int.from_bytes(head[8:10], "little")
                    elif major in (2, 3):
                        hlen = int.from_bytes(head[8:10] + fp.read(2), "little")
                    else:
                        return "member %r has unknown .npy version %d" % (name, major)
                    if hlen > 65536:
                        return "member %r has an oversized header" % name
                    raw = fp.read(hlen)
                if len(raw) != hlen:
                    return "member %r header truncated" % name
                why = _header_problem(raw, major)
                if why:
                    return "member %r: %s" % (name, why)
            if expect != getattr(zf, "start_dir", expect):
                return "bytes between the members and the directory"
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError, struct.error, ValueError) as e:
        return "not a readable zip (%s)" % type(e).__name__
    return None


# ---------------------------------------------------------------------------- driver

def check_dirs(src, out):
    s, o = Path(src), Path(out)
    if not s.is_dir():
        raise Refused("source %s is not a directory" % src)
    if o.is_symlink() and not o.exists():
        raise Refused("output is a dangling symlink")
    s, o = s.resolve(), o.resolve()
    if o == s:
        raise Refused("output equals the source")
    if s in o.parents:
        raise Refused("output is inside the source")
    if o.exists():
        if not o.is_dir():
            raise Refused("output exists and is not a directory")
        if any(o.iterdir()):
            raise Refused("output exists and is not empty")
    return s, o


def classify(path):
    n = path.name.lower()
    if path.name == "frames.jsonl" or n.endswith(".json"):
        return "json"
    if n.endswith(".npz"):
        return "npz"
    return None


def name_problem(sc, rel):
    """Why this staged path may not be used as is (every component), else None."""
    for part in rel.parts:
        try:
            part.encode("utf-8")
        except UnicodeEncodeError:
            return "name %r is not valid UTF-8; not staged" % part
        if sc.text(part) != part:
            return "name %r would be changed by the scrub; not staged (never renamed)" % part
    return None


def run(src, out, hosts=(), users=(), log=print):
    s, o = check_dirs(src, out)
    spelled = [str(Path(src).absolute()), os.path.abspath(str(src))]
    files, skipped = [], []
    for p in sorted(s.rglob("*")):
        rel = p.relative_to(s)
        if p.is_symlink():
            skipped.append((str(rel), "symlink (never followed)"))
        elif p.is_dir():
            continue
        elif not p.is_file():
            skipped.append((str(rel), "not a regular file"))
        else:
            kind = classify(p)
            if kind:
                files.append((p, rel, kind))
            else:
                skipped.append((str(rel), "image/video or unrecognised type (%s)" % (p.suffix.lower() or "no extension")))
    # pass 1: user names that appear in paths anywhere in the run
    found = set()
    for p, rel, kind in files:
        if kind == "json":
            try:
                collect_users(p, found)
            except (FileSkip, RecursionError, ValueError):
                pass
    results, errors = [], []
    o.mkdir(parents=True, exist_ok=True)
    namer = Scrubber(s, hosts, users, spelled, found)
    namer.prepare()
    for p, rel, kind in files:
        sc = Scrubber(s, hosts, users, spelled, found)
        sc.prepare()
        dst = o / rel
        try:
            why = name_problem(namer, rel)
            if why:
                errors.append(str(rel))
                raise FileSkip(why)
            if kind == "json":
                data = scrub_json_bytes(sc, p)
            else:
                why = npz_problem(p, sc)
                if why:
                    raise FileSkip(why)
                data = p.read_bytes()
            write_atomic(dst, data)
            results.append((str(rel), "copied" if kind == "npz" else "scrubbed", sc.counts))
        except FileSkip as e:
            skipped.append((str(rel), str(e)))
            if kind == "json" and str(rel) not in errors:
                errors.append(str(rel))
        except (RecursionError, ValueError, UnicodeError, OSError, MemoryError) as e:
            skipped.append((str(rel), "%s while processing (%s); file not written" % (type(e).__name__, str(e)[:80])))
            errors.append(str(rel))
    log("scrub-run: %s -> %s" % (s, o))
    log("%-48s %-9s %5s %5s %5s %5s" % (("file", "action") + CATEGORIES))
    for name, action, c in results:
        log("%-48s %-9s %5d %5d %5d %5d" % ((name, action) + tuple(c[k] for k in CATEGORIES)))
    tot = Counter()
    for _, _, c in results:
        tot.update(c)
    log("%-58s %5d %5d %5d %5d" % (("TOTAL (rewritten occurrences)",) + tuple(tot[k] for k in CATEGORIES)))
    log("skipped %d:" % len(skipped))
    for name, why in skipped:
        log("  %s: %s" % (name, why))
    return results, skipped, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="Copy a run's text into a staging dir with paths/hosts/users/IPs scrubbed.")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--host", action="append", default=[], help="extra host name to replace with 'host'")
    ap.add_argument("--user", action="append", default=[], help="extra user name to replace with 'user'")
    a = ap.parse_args(argv)
    try:
        _, _, errors = run(a.src, a.out, a.host, a.user)
    except Refused as e:
        print("scrub-run: REFUSED - %s" % e, file=sys.stderr)
        return 2
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
