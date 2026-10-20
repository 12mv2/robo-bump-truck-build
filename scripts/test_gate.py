#!/usr/bin/env python3
"""test_gate.py - planted-violation tests for the leak gate (stdlib unittest, no network).

Run:  python3 scripts/test_gate.py
Every planted violation uses the synthetic string ZZLEAKZZ (via GATE_EXTRA_STRINGS or an explicit
entry list), never a real denylist word. Every subprocess (hooks, check.py, git) runs with HOME set
to a temp dir holding a synthetic denylist, so no test reads or depends on the real denylist.
No test sets ALLOW_PUSH: the pre-push hook is run directly with constructed stdin (its scan runs
before the ALLOW_PUSH refusal), or through `git push --dry-run`, where that refusal is the assertion."""
import atexit
import hashlib
import json
import re
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import struct
import zlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import gate_lib as G  # noqa: E402

PRE_PUSH = HERE / "hooks" / "pre-push"
PRE_COMMIT = HERE / "hooks" / "pre-commit"
COMMIT_MSG = HERE / "hooks" / "commit-msg"
CHECK = HERE / "check.py"
PLANT = "ZZLEAKZZ"
ZERO = "0" * 40
DENY = G.parse_entries([PLANT], "test")
NO_MANIFEST = frozenset()
ALLOW_LINE = ("pre-push: REFUSED — set ALLOW_PUSH=1 only on the author's explicit go "
              "(nothing leaves this repo by habit).\n")
TRAILER = "Co-" + "Authored-" + "By: someone <someone@example.com>"
DATA_URI = "da" + "ta:text/plain;base64,aGVsbG8="   # built from parts: the literal would refuse this file
STRIP = HERE / "strip-image-metadata.sh"
EXIFTOOL = shutil.which("exiftool")
GATE_FILES = ("scripts/check.py", "scripts/gate_lib.py", "scripts/media_walk.py", "scripts/strip_media.py",
              "scripts/test_gate.py", "scripts/test_media_walk.py", "scripts/binary-manifest.txt",
              "scripts/hooks/pre-commit", "scripts/hooks/commit-msg", "scripts/hooks/pre-push")


def b64run(n):
    """A base64-looking run of n characters with many distinct characters (a real encoded blob)."""
    alpha = "QWxlcnRZdUlvUGFTZGZHaEprTHpYY1Zi0123456789+/"
    return (alpha * (n // len(alpha) + 1))[:n]


def tiny_png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0")) + chunk(b"IEND", b""))


def make_image(directory, name, *tags):
    """A real tiny image in a temp dir, with exiftool tags like '-GPSLatitude=40.0' added."""
    f = pathlib.Path(directory) / name
    f.write_bytes(tiny_png())
    if tags:
        subprocess.run([EXIFTOOL, "-q", "-overwrite_original", *tags, str(f)], check=True)
    return f


def make_jpeg(directory, name, *tags):
    """A real tiny JPEG (made with sips or ffmpeg, whichever exists) with exiftool tags added; None if neither exists."""
    f = pathlib.Path(directory) / name
    png = pathlib.Path(directory) / (name + ".src.png")
    png.write_bytes(tiny_png())
    if shutil.which("sips"):
        cmd = ["sips", "-s", "format", "jpeg", str(png), "--out", str(f)]
    elif shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(png), str(f)]
    else:
        return None
    subprocess.run(cmd, check=True, capture_output=True)
    if tags:
        subprocess.run([EXIFTOOL, "-q", "-overwrite_original", *tags, str(f)], check=True)
    return f


def make_heic(directory, name, *tags):
    """A real tiny HEIC (sips only) with exiftool tags added; None where sips is absent."""
    f = pathlib.Path(directory) / name
    png = pathlib.Path(directory) / (name + ".src.png")
    png.write_bytes(tiny_png())
    if not shutil.which("sips"):
        return None
    res = subprocess.run(["sips", "-s", "format", "heic", str(png), "--out", str(f)], capture_output=True)
    if res.returncode or not f.is_file():
        return None
    if tags:
        subprocess.run([EXIFTOOL, "-q", "-overwrite_original", *tags, str(f)], check=True)
    return f


def make_mov(directory, name, *tags):
    """A hand-built minimal QuickTime file (ftyp + mdat), with exiftool tags like '-QuickTime:GPSCoordinates=40 -105' added."""
    def atom(t, d):
        return struct.pack(">I", 8 + len(d)) + t + d
    f = pathlib.Path(directory) / name
    f.write_bytes(atom(b"ftyp", b"qt  \0\0\0\0qt  ") + atom(b"mdat", b"\0" * 16))
    if tags:
        subprocess.run([EXIFTOOL, "-q", "-overwrite_original", *tags, str(f)], check=True)
    return f


def _tmpdir(prefix):
    d = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, str(d), True)
    return d


def _make_home():
    home = _tmpdir("gate-home-")
    d = home / "Projects/clients/robo-bump-truck-build"
    d.mkdir(parents=True)
    (d / "private-strings.txt").write_text("zz-synthetic-entry-never-matches\n")
    return home


TEST_HOME = _make_home()
NO_GH = _tmpdir("gate-nogh-")          # a `gh` that fails, so check.py never reaches the network
(NO_GH / "gh").write_text("#!/bin/sh\nexit 1\n")
(NO_GH / "gh").chmod(0o755)
CLEAN_ENV = {"HOME": str(TEST_HOME), "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_AUTHOR_NAME": "Test Author", "GIT_AUTHOR_EMAIL": "author@example.com",
             "GIT_COMMITTER_NAME": "Test Author", "GIT_COMMITTER_EMAIL": "author@example.com"}


def env_for(extra=None, **over):
    """A subprocess environment: no inherited GIT_*, ALLOW_PUSH or GATE_EXTRA_STRINGS; temp HOME."""
    e = {k: v for k, v in os.environ.items()
         if not k.startswith("GIT_") and k not in ("ALLOW_PUSH", G.EXTRA_ENV)}
    e.update(CLEAN_ENV)
    e["PYTHONDONTWRITEBYTECODE"] = "1"   # no __pycache__ binaries in a temp clone's scan
    e["PATH"] = str(NO_GH) + os.pathsep + e.get("PATH", "")
    if extra:
        e[G.EXTRA_ENV] = extra
    e.update(over)
    assert "ALLOW_PUSH" not in e
    return e


def run(repo, *args, env=None, inp=None, cmd=None):
    e = env_for()
    e.update(env or {})
    return subprocess.run(cmd or ["git", *args], cwd=str(repo), env=e, input=inp,
                          capture_output=True, text=True)


class TempRepo:
    """Throwaway git repo with hooks off; commit() takes {path: str|bytes}."""

    def __init__(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="gate-test-"))
        for a in (("init", "-q", "-b", "main"), ("config", "commit.gpgsign", "false"),
                  ("config", "tag.gpgsign", "false"), ("config", "core.hooksPath", "/dev/null")):
            run(self.dir, *a).check_returncode()

    def write(self, files):
        for p, data in files.items():
            fp = self.dir / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_bytes(data if isinstance(data, bytes) else data.encode())

    def commit(self, files, msg="clean", env=None):
        self.write(files)
        run(self.dir, "add", "-f", "-A").check_returncode()
        run(self.dir, "commit", "-q", "--allow-empty", "-m", msg, env=env).check_returncode()
        return self.rev("HEAD")

    def rev(self, what):
        return run(self.dir, "rev-parse", what).stdout.strip()

    def audit(self, manifest=NO_MANIFEST, **kw):
        commits = run(self.dir, "rev-list", "--all").stdout.split()
        return G.audit_commits(str(self.dir), commits, DENY, manifest=manifest, **kw)

    def pre_push(self, stdin, extra=PLANT, home=None):
        """The real pre-push hook, invoked directly. ALLOW_PUSH is never set."""
        e = env_for(extra, HOME=str(home or TEST_HOME))
        return subprocess.run([sys.executable, str(PRE_PUSH)], cwd=str(self.dir), env=e, input=stdin,
                              capture_output=True, text=True)

    def commit_msg(self, data, extra=PLANT):
        f = self.dir / "MSG"
        f.write_bytes(data if isinstance(data, bytes) else data.encode())
        return subprocess.run([sys.executable, str(COMMIT_MSG), str(f)], cwd=str(self.dir),
                              env=env_for(extra), capture_output=True, text=True)

    def use_real_hooks(self):
        run(self.dir, "config", "core.hooksPath", str(HERE / "hooks")).check_returncode()

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def push_line(ref, sha, remote_sha=ZERO, remote_ref=None):
    return "%s %s %s %s\n" % (ref, sha, remote_ref or ref, remote_sha)


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.r = TempRepo()
        self.addCleanup(self.r.cleanup)

    def new_repo(self):
        r = TempRepo()
        self.addCleanup(r.cleanup)
        return r

    def replaced_history(self):
        """Leaky commit X, clean follow-up Z, and `git replace X Y` with Y a clean twin: reads that
        honour replace refs see Y, but a push sends X. Returns (X, Z)."""
        x = self.r.commit({"a.txt": "leak " + PLANT}, msg="leaky")
        z = self.r.commit({"a.txt": "clean"}, msg="fixed")
        empty = run(self.r.dir, "hash-object", "-t", "tree", "-w", "--stdin", inp="").stdout.strip()
        y = run(self.r.dir, "commit-tree", empty, "-m", "clean twin").stdout.strip()
        run(self.r.dir, "replace", x, y).check_returncode()
        return x, z


# ---------------------------------------------------------------- normalisation (done once)

class NormalisationTests(unittest.TestCase):
    def hit(self, text, entries=None):
        return G.scan_text(text, entries or DENY)

    def test_plain_substring_and_case(self):
        self.assertTrue(self.hit("aa " + PLANT.lower() + " bb"))
        self.assertTrue(self.hit("xx" + PLANT + "yy"))
        self.assertIsNone(self.hit("nothing private in here at all"))

    def test_zero_width_and_format_chars_stripped(self):
        self.assertTrue(self.hit("ZZLEAK​ZZ"))          # zero-width space
        self.assertTrue(self.hit("ZZ⁠LEAK﻿ZZ"))    # word joiner, BOM
        self.assertTrue(self.hit("ZZ‌LE‍AKZZ"))    # ZWNJ, ZWJ

    def test_soft_hyphen(self):
        self.assertTrue(self.hit("ZZ­LEAKZZ"))

    def test_fullwidth_nfkc(self):
        self.assertTrue(self.hit("ＺＺＬＥＡＫＺＺ"))

    def test_normalise_is_exactly_cf_strip_nfkc_casefold(self):
        self.assertEqual(G.normalise("A­B​CＤ"), "abcd")
        self.assertEqual(G.normalise("Straße"), "strasse")

    def test_whitespace_runs_collapse_in_text_and_in_entries(self):
        ent = G.parse_entries(["Zz  Leak\tZz"], "t")        # an entry with odd spacing is normalised too
        self.assertEqual(ent[0].pattern, "zz leak zz")
        for text in ("zz leak zz", "zz\nleak\nzz", "ZZ\tLEAK ZZ", "zz   leak  \r\n zz", "ZZ\u00a0LEAK\u2003ZZ"):
            self.assertTrue(G.scan_text(text, ent), repr(text))
        self.assertIsNone(G.scan_text("zzleakzz", ent))        # collapse is not removal
        two = G.parse_entries(["Marker Word"], "t")
        for text in ("marker word", "Marker\nWord", "marker\tword", "marker  word", "MARKER \n\t WORD"):
            self.assertTrue(G.scan_text(text, two), repr(text))
        self.assertIsNone(G.scan_text("markerword", two))
        self.assertEqual(G.normalise("A \n\t B"), "a b")

    def test_regex_entries(self):
        rx = G.parse_entries([r"re:\bZZLEAK\w+\b"], "test")
        self.assertEqual(rx[0].kind, "re")
        self.assertTrue(G.scan_text("see zzleakabc here", rx))      # case-insensitive
        self.assertTrue(G.scan_text("ZZ​LEAKZZ", rx))          # matched against normalised text
        self.assertIsNone(G.scan_text("aZZLEAKZZ", rx))             # word boundary honoured

    def test_plain_lines_keep_substring_behaviour(self):
        ent = G.parse_entries(["leak"], "test")
        self.assertEqual(ent[0].kind, "sub")
        self.assertTrue(G.scan_text("fooLEAKbar", ent))

    def test_comments_and_blanks_skipped_and_bad_regex_fails_closed(self):
        self.assertEqual(G.parse_entries(["# note", "", "   "], "t"), [])
        with self.assertRaises(G.GateError):
            G.parse_entries(["re:("], "t")
        with self.assertRaises(G.GateError):
            G.parse_entries(["re:"], "t")

    def test_comment_is_only_hash_space(self):
        stats = {}
        ent = G.parse_entries(["# real comment", "#channel", "#", "#12345", " # indented", "plain",
                               "", "\u200b", "\u200b\u200d"], "t", stats)
        self.assertEqual([e.pattern for e in ent], ["#channel", "#", "#12345", "# indented", "plain"])
        self.assertEqual(stats, {"comments": 1, "blank": 1, "dropped": 2})
        self.assertTrue(G.scan_text("join #channel now", ent))
        self.assertTrue(G.scan_text("ticket #12345", ent))

    def test_extra_strings_env_and_missing_or_empty_denylist(self):
        d = _tmpdir("gate-deny-")
        f = d / "deny.txt"
        f.write_text("# c\nplainword\nre:foo\\d+\n")
        base = G.load_denylist(f, environ={})
        self.assertEqual([e.kind for e in base], ["sub", "re"])
        both = G.load_denylist(f, environ={G.EXTRA_ENV: PLANT + ",re:bar\\d"})
        self.assertEqual(len(both), 4)
        self.assertTrue(G.scan_text("x " + PLANT, both))
        self.assertTrue(G.scan_text("bar7", both))
        with self.assertRaises(G.GateError):
            G.load_denylist(d / "missing.txt", environ={G.EXTRA_ENV: PLANT})
        f.write_text("# only comments\n")
        with self.assertRaises(G.GateError):
            G.load_denylist(f, environ={G.EXTRA_ENV: PLANT})

    def test_gate_decodes_nothing(self):
        # an encoded copy of a private string is not looked inside; a short run is not refused either
        import base64
        enc = base64.b64encode(("zz " + PLANT + " zz").encode()).decode()
        self.assertLess(len(enc), 200)
        self.assertEqual(G.text_problems(enc, DENY, "x"), [])
        src = (HERE / "gate_lib.py").read_text()
        for gone in ("import base64", "b64decode", "binascii", "_decoded_texts", "EXEMPT_PATHS"):
            self.assertNotIn(gone, src)


# ---------------------------------------------------------------- item 1: what gets scanned

class ScanCoverageTests(RepoTestCase):
    def test_author_and_committer_name_and_email(self):
        for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            r = self.new_repo()
            val = PLANT + "@example.com" if "EMAIL" in var else "Pat " + PLANT
            sha = r.commit({"a.txt": "clean"}, env={var: val})
            probs = r.audit()
            self.assertTrue(any("private string" in p and sha[:8] in p for p in probs), (var, probs))

    def test_commit_message_in_full_raw_form(self):
        self.r.commit({"a.txt": "clean"}, msg="subject\n\nline two\n\n# hash line " + PLANT)
        self.assertTrue(any("private string" in p and "commit " in p for p in self.r.audit()))

    def test_clean_commit_passes(self):
        self.r.commit({"README.md": "hello"})
        self.assertEqual(self.r.audit(), [])

    def test_path_names(self):
        self.r.commit({"sub/" + PLANT + "/notes.txt": "clean"})
        self.assertTrue(any("file name" in p for p in self.r.audit()))

    def test_ref_name_local_and_remote(self):
        sha = self.r.commit({"a.txt": "clean"})
        self.assertTrue(G.audit_ref(str(self.r.dir), "refs/heads/" + PLANT, sha, DENY, manifest=NO_MANIFEST))
        self.assertEqual(G.audit_ref(str(self.r.dir), "refs/heads/main", sha, DENY, manifest=NO_MANIFEST), [])
        probs = G.audit_push(str(self.r.dir), [push_line("refs/heads/main", sha, remote_ref="refs/heads/x" + PLANT)],
                             DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("ref name" in p for p in probs), probs)

    def test_annotated_tag_full_content(self):
        self.r.commit({"a.txt": "clean"})
        run(self.r.dir, "tag", "-a", "v1", "-m", "release note " + PLANT).check_returncode()
        probs = G.audit_ref(str(self.r.dir), "refs/tags/v1", self.r.rev("v1"), DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("tag object" in p for p in probs), probs)
        run(self.r.dir, "tag", "-a", "v2", "-m", "fine").check_returncode()
        self.assertEqual(G.audit_ref(str(self.r.dir), "refs/tags/v2", self.r.rev("v2"), DENY,
                                     manifest=NO_MANIFEST), [])

    def test_tagger_identity(self):
        self.r.commit({"a.txt": "clean"})
        run(self.r.dir, "tag", "-a", "v1", "-m", "fine",
            env={"GIT_COMMITTER_NAME": "Pat " + PLANT}).check_returncode()
        self.assertTrue(G.audit_ref(str(self.r.dir), "refs/tags/v1", self.r.rev("v1"), DENY, manifest=NO_MANIFEST))

    def test_nested_tag_is_scanned_to_the_bottom(self):
        self.r.commit({"a.txt": "clean"})
        run(self.r.dir, "tag", "-a", "inner", "-m", "inner " + PLANT).check_returncode()
        run(self.r.dir, "tag", "-a", "outer", "-m", "clean outer", "inner").check_returncode()
        run(self.r.dir, "tag", "-d", "inner").check_returncode()
        outer = self.r.rev("outer")
        self.assertEqual(run(self.r.dir, "cat-file", "-t", outer).stdout.strip(), "tag")
        probs = G.audit_ref(str(self.r.dir), "refs/tags/outer", outer, DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("private string" in p and "tag object" in p for p in probs), probs)

    def test_tag_on_a_blob_scans_the_blob(self):
        self.r.commit({"a.txt": "clean"})
        (self.r.dir / "loose").write_text("blob " + PLANT)
        blob = run(self.r.dir, "hash-object", "-w", "loose").stdout.strip()
        run(self.r.dir, "tag", "-a", "b1", "-m", "fine", blob).check_returncode()
        probs = G.audit_ref(str(self.r.dir), "refs/tags/b1", self.r.rev("b1"), DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("blob under" in p for p in probs), probs)

    def test_blobs_scanned_regardless_of_suffix(self):
        for name in ("Dockerfile", "Makefile", "pkg/package.xml", "robot.urdf", "a.launch", "x.service",
                     "99-udev.rules", "wpa.conf", "n.ipynb", "s.js", ".env", "p.gcode", "noext"):
            r = self.new_repo()
            r.commit({name: "header\n# owner " + PLANT + "\n"})
            self.assertTrue(any("private string" in p and name in p for p in r.audit()), name)

    def test_deleted_then_readded_content_still_caught_in_history(self):
        self.r.commit({"a.txt": "oops " + PLANT})
        self.r.commit({"a.txt": "fixed"})
        self.assertTrue(self.r.audit())

    def test_trailer_in_history_is_caught_without_the_hook(self):
        self.r.commit({"a.txt": "clean"}, msg="subject\n\n" + TRAILER)
        self.assertTrue(any("co-author trailer" in p for p in self.r.audit()))

    def test_size_check_on_committed_blobs(self):
        self.r.commit({"big.txt": "x" * (2 * 2 ** 20)})
        self.assertTrue(any("over 1 MB" in p for p in self.r.audit(limit_mb=1)))


class StagedTests(RepoTestCase):
    def stage(self, files):
        self.r.write(files)
        for name in files:
            run(self.r.dir, "add", "-f", "--", name).check_returncode()

    def test_staged_blob_is_scanned_not_the_working_copy(self):
        self.r.commit({"a.txt": "clean"})
        self.stage({"leak.txt": "secret " + PLANT})
        (self.r.dir / "leak.txt").write_text("clean now")
        self.assertTrue(any("leak.txt" in p for p in G.audit_staged(str(self.r.dir), DENY, manifest=NO_MANIFEST)))
        self.r.use_real_hooks()
        head = self.r.rev("HEAD")
        bad = run(self.r.dir, "commit", "-q", "-m", "x", env={G.EXTRA_ENV: PLANT})
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("pre-commit: REFUSED", bad.stderr)
        self.assertEqual(self.r.rev("HEAD"), head)

    def test_names_that_look_like_stage_specs_are_read_exactly(self):
        self.r.commit({"a.txt": "clean"})
        self.stage({"foo": "clean", "0:foo": "secret " + PLANT})
        probs = G.audit_staged(str(self.r.dir), DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("in 0:foo" in p for p in probs), probs)

    def test_gitlink_is_skipped_not_a_crash(self):
        sha = self.r.commit({"a.txt": "clean"})
        run(self.r.dir, "update-index", "--add", "--cacheinfo", "160000,%s,sub" % sha).check_returncode()
        self.assertEqual(G.audit_staged(str(self.r.dir), DENY, manifest=NO_MANIFEST), [])

    def test_type_change_to_symlink_scans_the_target_string(self):
        self.r.commit({"link": "regular file"})
        (self.r.dir / "link").unlink()
        os.symlink("target-" + PLANT, str(self.r.dir / "link"))
        run(self.r.dir, "add", "--", "link").check_returncode()
        probs = G.audit_staged(str(self.r.dir), DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("in link" in p for p in probs), probs)
        self.assertTrue(G.audit_files(str(self.r.dir), ["link"], DENY, manifest=NO_MANIFEST))


# ---------------------------------------------------------------- item 2: opaque content refused

class OpaqueContentTests(RepoTestCase):
    def test_base64_like_run_over_200_refused_200_passes(self):
        for alphabet in ("Qa1+/Z2x", "b2_-Yq9Tm", b64run(201)):
            long_run = (alphabet * 201)[:201]
            probs = G.text_problems("key = " + long_run + "\n", DENY, "f.txt")
            self.assertTrue(any(G.OPAQUE in p and "base64-like" in p for p in probs), alphabet)
            self.assertEqual(G.text_problems("key = " + long_run[:200] + "\n", DENY, "f.txt"), [])

    def test_low_variety_runs_are_not_opaque(self):
        for text in ("-" * 300, "|" + "---|" * 80, "|" + "---|---|" * 40, "=" * 300, "_" * 250, "/" * 300,
                     "a-" * 150, "abc" * 100, "0123456" * 40, "x" * 1000):
            self.assertEqual(G.text_problems(text, DENY, "f.md"), [], text[:20])
        for n in (201, 300, 5000):
            probs = G.text_problems("k = " + b64run(n), DENY, "f")
            self.assertTrue(any("base64-like" in p for p in probs), n)
        self.assertTrue(G.text_problems("-" * 300 + "\n" + b64run(300), DENY, "f"))   # one real run among rules
        self.assertTrue(G.text_problems("0123456789abcdef" * 20, DENY, "f"))        # hex: 16 distinct

    def test_data_uri_refused(self):
        for t in ("<img src=\"" + DATA_URI + "\">", "url(" + "da" + "ta:,plain)", "DA" + "TA:image/png;x=1,AAAA"):
            self.assertTrue(any("data: URI" in p for p in G.text_problems(t, DENY, "f")), t)
        for ok in ("metadata: something", "data: 42, then more", "the data, in brief"):
            self.assertEqual(G.text_problems(ok, DENY, "f"), [], ok)

    def test_non_utf8_refused_never_decoded(self):
        probs = G.bytes_problems(b"caf\xe9 " + PLANT.encode(), DENY, "f.txt")
        self.assertEqual(len(probs), 1)
        self.assertIn(G.OPAQUE, probs[0])
        self.assertIn("not UTF-8", probs[0])

    def test_opaque_blob_and_message_in_history(self):
        self.r.commit({"a.txt": "x = " + b64run(300)}, msg="clean")
        self.r.commit({"b.md": DATA_URI})
        self.r.commit({"c.txt": b"caf\xe9"})
        self.r.commit({}, msg="subject\n\n" + b64run(250))
        probs = self.r.audit()
        for needle in ("in a.txt", "in b.md", "in c.txt"):
            self.assertTrue(any(G.OPAQUE in p and needle in p for p in probs), (needle, probs))
        self.assertTrue(any(G.OPAQUE in p and "commit " in p and "base64-like" in p for p in probs), probs)

    def test_non_utf8_commit_object_refused(self):
        (self.r.dir / "MSGFILE").write_bytes(b"caf\xe9 subject\n")
        self.r.write({"a.txt": "clean"})
        run(self.r.dir, "add", "a.txt").check_returncode()
        run(self.r.dir, "-c", "i18n.commitEncoding=ISO-8859-1", "commit", "-q", "-F", "MSGFILE").check_returncode()
        self.assertTrue(any(G.OPAQUE in p and "not UTF-8" in p for p in self.r.audit()))

    def test_long_input_scans_in_linear_time(self):
        for text in ("A" * 1000000, ("word\n" * 200000), ("a" * 199 + "\n") * 5000, b64run(1000000)):
            t0 = time.monotonic()
            G.text_problems(text, DENY, "big")
            self.assertLess(time.monotonic() - t0, 2.0)


class BinaryManifestTests(RepoTestCase):
    def manifest_file(self, lines):
        f = _tmpdir("gate-manifest-") / "m.txt"
        f.write_text("# comment\n" + "".join(l + "\n" for l in lines))
        return f

    def test_tracked_manifest_is_complete_and_true(self):
        """The tracked manifest exists; each entry's path is tracked and its SHA-256 matches the file;
        every tracked binary (NUL byte, not UTF-8, or an image/video) is listed with its exact SHA-256."""
        manifest_path = REPO / "scripts" / "binary-manifest.txt"
        self.assertTrue(manifest_path.is_file())
        tracked = [t for t in run(REPO, "ls-files", "-z").stdout.split("\0") if t]
        self.assertIn("scripts/binary-manifest.txt", tracked)
        entries = G.load_manifest(manifest_path)
        for path, sha in sorted(entries):
            self.assertIn(path, tracked, "manifest entry is not a tracked path: " + path)
            self.assertEqual(hashlib.sha256((REPO / path).read_bytes()).hexdigest(), sha, path)
        for path in tracked:
            fp = REPO / path
            if fp.is_symlink() or not fp.is_file():
                continue
            data = fp.read_bytes()
            try:
                data.decode("utf-8")
                unreadable = b"\0" in data
            except UnicodeDecodeError:
                unreadable = True
            if unreadable or G.is_image(path, data):
                self.assertIn((path, hashlib.sha256(data).hexdigest()), entries,
                              "tracked binary missing from scripts/binary-manifest.txt: " + path)

    def test_gate_reads_the_main_checkouts_manifest_and_hooks(self):
        self.assertEqual(G.MAIN_ROOT, G.find_main_root())
        self.assertEqual(G.MANIFEST_FILE, G.MAIN_ROOT / "scripts" / "binary-manifest.txt")
        self.assertEqual(G.HOOKS_DIR, G.MAIN_ROOT / "scripts" / "hooks")
        self.assertTrue(G.MANIFEST_FILE.is_file())

    def test_binary_refused_without_entry(self):
        self.r.commit({"photo.png": b"\x89PNG\0\0data", "n.noext": b"\0", "late.txt": b"a" * 9000 + b"\0"})
        probs = self.r.audit()
        for name in ("photo.png", "n.noext", "late.txt"):
            self.assertTrue(any("binary" in p and name in p for p in probs), (name, probs))

    def test_exact_path_and_sha_allows_a_binary(self):
        data = tiny_png()
        sha = hashlib.sha256(data).hexdigest()
        m = G.load_manifest(self.manifest_file([sha + "  img/a.png"]))
        self.assertEqual(G.check_blob("img/a.png", data, DENY, m), [])
        self.assertTrue(G.check_blob("img/b.png", data, DENY, m))              # other path
        self.assertTrue(G.check_blob("img/a.png", data + b"x", DENY, m))       # other content
        self.assertTrue(G.check_blob("IMG/A.PNG", data, DENY, m))              # exact, not case-folded
        latin = b"caf\xe9"
        m2 = G.load_manifest(self.manifest_file([hashlib.sha256(latin).hexdigest() + "  doc.txt"]))
        self.assertEqual(G.check_blob("doc.txt", latin, DENY, m2), [])         # unreadable text, listed

    def test_manifest_never_exempts_readable_text(self):
        data = ("text " + PLANT).encode()
        m = G.load_manifest(self.manifest_file([hashlib.sha256(data).hexdigest() + "  notes.txt"]))
        self.assertTrue(any("private string" in p for p in G.check_blob("notes.txt", data, DENY, m)))

    def test_missing_or_malformed_manifest_fails_closed(self):
        with self.assertRaises(G.GateError):
            G.load_manifest(_tmpdir("gate-nomanifest-") / "absent.txt")
        with self.assertRaises(G.GateError):
            G.load_manifest(self.manifest_file(["not-a-hash  x.png"]))


@unittest.skipUnless(EXIFTOOL, "exiftool absent: image metadata tests skipped")
class ImageMetadataTests(RepoTestCase):
    GPS = ("-GPSLatitude=40.0", "-GPSLatitudeRef=N", "-GPSLongitude=105.0", "-GPSLongitudeRef=W")

    def setUp(self):
        super().setUp()
        self.tmp = _tmpdir("gate-img-")

    def blob(self, f, name="img/a.png"):
        data = pathlib.Path(f).read_bytes()
        m = frozenset({(name, hashlib.sha256(data).hexdigest())})
        return G.check_blob(name, data, DENY, m)

    def test_gps_image_refused(self):
        probs = self.blob(make_image(self.tmp, "g.png", *self.GPS))
        self.assertTrue(any("emits" in p and "img/a.png" in p for p in probs), probs)

    def test_serial_images_refused(self):
        for tag in ("-EXIF:SerialNumber=789", "-XMP-aux:SerialNumber=789", "-XMP-aux:LensSerialNumber=1"):
            probs = self.blob(make_image(self.tmp, "s.png", tag))
            self.assertTrue(any("emits" in p for p in probs), (tag, probs))

    def test_stripped_image_passes(self):
        f = make_image(self.tmp, "p.png", *self.GPS, "-XMP-aux:SerialNumber=123")
        self.assertTrue(self.blob(f))
        out = self.tmp / "p-clean.png"
        res = subprocess.run([str(STRIP), str(f), str(out)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.blob(out), [])

    def test_image_without_manifest_entry_still_refused_as_binary(self):
        data = pathlib.Path(make_image(self.tmp, "n.png", *self.GPS)).read_bytes()
        probs = G.check_blob("n.png", data, DENY, NO_MANIFEST)
        self.assertTrue(any("emits" in p for p in probs) and any("manifest" in p for p in probs), probs)

    def test_unreadable_image_fails_closed(self):
        self.assertTrue(any("emits" in p for p in G.check_blob("x.jpg", b"\xff\xd8\xff junk", DENY, NO_MANIFEST)))

    def test_git_scans_refuse_gps_image(self):
        self.r.commit({"img/a.png": pathlib.Path(make_image(self.tmp, "g.png", *self.GPS)).read_bytes()})
        data = (self.r.dir / "img/a.png").read_bytes()
        m = frozenset({("img/a.png", hashlib.sha256(data).hexdigest())})
        self.assertTrue(any("emits" in p for p in self.r.audit(manifest=m)))          # history / pre-push path
        self.assertTrue(any("emits" in p for p in G.audit_files(str(self.r.dir), ["img/a.png"], DENY, manifest=m)))
        self.r.write({"img/b.png": data})
        run(self.r.dir, "add", "-f", "img/b.png").check_returncode()
        m = frozenset({("img/b.png", hashlib.sha256(data).hexdigest())})
        self.assertTrue(any("emits" in p for p in G.audit_staged(str(self.r.dir), DENY, manifest=m)))   # pre-commit path


@unittest.skipUnless(EXIFTOOL, "exiftool absent: it makes the dirty fixtures and is the independent oracle")
class ImageMediumFixTests(RepoTestCase):
    """The strip helper and the formats the gate treats as images (exiftool only builds dirty files and re-reads clean ones)."""

    def setUp(self):
        super().setUp()
        self.tmp = _tmpdir("gate-med-")

    def check(self, f, name=None):
        data = pathlib.Path(f).read_bytes()
        name = name or pathlib.Path(f).name
        return G.check_blob(name, data, DENY, frozenset({(name, hashlib.sha256(data).hexdigest())}))

    def groups(self, f):
        res = subprocess.run([EXIFTOOL, "-a", "-G1", "-s", "-ee", "-u", str(f)], capture_output=True, text=True)
        return {line.split("]", 1)[0].lstrip("[") for line in res.stdout.splitlines() if line.startswith("[")}

    def test_1_strip_writes_a_copy_with_nothing_but_pixels(self):
        src = make_jpeg(self.tmp, "-x.jpg", "-Orientation#=6", "-GPSLatitude=40", "-GPSLatitudeRef=N", "-EXIF:SerialNumber=77",
                        "-Software=zz-generic-value")
        if src is None:
            self.skipTest("no sips or ffmpeg to make a JPEG")
        before = src.read_bytes()
        self.assertTrue(self.check(src))
        out = self.tmp / "out.jpg"
        res = subprocess.run([str(STRIP), "-x.jpg", "out.jpg"], cwd=str(self.tmp), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)                    # a name starting with '-' works
        self.assertEqual(src.read_bytes(), before)                         # the original is never touched
        self.assertEqual(self.check(out), [])
        self.assertLessEqual(self.groups(out), {"ExifTool", "System", "File", "JFIF", "Composite"})     # exiftool agrees: no EXIF/ICC/XMP
        for bad in (["only-one-arg"], [str(src), str(src)], [str(src), str(out)]):   # usage, same file, existing output
            self.assertNotEqual(subprocess.run([str(STRIP), *bad], capture_output=True).returncode, 0, bad)
        self.assertEqual(src.read_bytes(), before)

    def test_2_more_formats_and_video_are_images(self):
        for ext in (".gif", ".bmp", ".heif", ".avif", ".dng", ".jxl", ".mov", ".mp4", ".m4v", ".MOV"):
            self.assertTrue(G.is_image("a/b" + ext, b"x"), ext)
        for brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis", b"isom", b"qt  ", b"M4V "):
            self.assertTrue(G.is_image("a.bin", b"\0\0\0\x18ftyp" + brand + b"\0" * 12), brand)
        for magic in (b"GIF89a" + b"\0" * 8, b"BM" + b"\0" * 4 + b"\0\0\0\0" + b"\0" * 6, b"\xff\x0a" + b"\0" * 8):
            self.assertTrue(G.is_image("a.bin", magic), magic)
        self.assertFalse(G.is_image("a.txt", b"plain text"))

    def test_2_mov_is_refused_whatever_its_name_and_whatever_it_carries(self):
        f = make_mov(self.tmp, "clip.mov", "-QuickTime:GPSCoordinates=40.0 -105.0")
        for name in ("clip.mov", "renamed.bin"):
            probs = G.check_blob(name, f.read_bytes(), DENY, NO_MANIFEST)
            self.assertTrue(any("emits" in p for p in probs), (name, probs))
        self.assertTrue(self.check(make_mov(self.tmp, "clean.mov")))          # a QuickTime file is never the helper's MP4
        out = self.tmp / "clean-copy.mp4"                                   # the hand-built file has no movie header: the helper fails closed
        res = subprocess.run([str(STRIP), str(f), str(out)], capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(out.exists())

    def test_2_heic_is_never_admitted_as_is(self):
        f = make_heic(self.tmp, "h.heic", *ImageMetadataTests.GPS)
        if f is None:
            self.skipTest("no sips to make a HEIC")
        self.assertTrue(any("emits" in p for p in self.check(f)))
        self.assertTrue(self.check(make_heic(self.tmp, "c.heic")))
        out = self.tmp / "h.jpg"
        res = subprocess.run([str(STRIP), str(f), str(out)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.check(out), [])

    def test_4_truncated_trailing_or_garbled_files_are_refused(self):
        png = tiny_png()
        self.assertEqual(G.image_problems("a.png", png, DENY), [])
        for label, data in (("trailer", png + b"trailing bytes after IEND"), ("truncated", png[:-20]), ("xx", png + b"xx")):
            probs = G.image_problems("a.png", data, DENY)
            self.assertTrue(any("emits" in p for p in probs), (label, probs))


def _git_only_path():
    """A PATH made of a temp dir holding a symlink to git and nothing else (no exiftool whatever the host layout)."""
    d = _tmpdir("gate-gitonly-")
    (d / "git").symlink_to(shutil.which("git"))
    return str(d)


class ImageGateNeedsNoExiftoolTests(RepoTestCase):
    def test_images_are_judged_without_exiftool_on_the_path(self):
        from unittest import mock
        env = {k: v for k, v in os.environ.items() if k != "EXIFTOOL"}
        env["PATH"] = _git_only_path()
        data = tiny_png()
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(G.image_problems("a.png", data, DENY), [])
            self.assertTrue(G.image_problems("a.png", data + b"x", DENY))
            self.assertEqual(G.check_blob("a.png", data, DENY, frozenset({("a.png", hashlib.sha256(data).hexdigest())})), [])

    def test_pre_commit_admits_a_clean_png_and_refuses_a_dirty_one_without_exiftool(self):
        e = env_for(PATH=_git_only_path())
        self.r.write({"a.png": tiny_png()})
        run(self.r.dir, "add", "-f", "a.png").check_returncode()
        # the manifest the hook reads is the tracked scripts/binary-manifest.txt (empty): the binary rule fires, the image rule does not
        res = subprocess.run([sys.executable, str(PRE_COMMIT)], cwd=str(self.r.dir), env=e, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("binary file not in the SHA-256 manifest", res.stderr)
        self.assertNotIn("emits", res.stderr)
        self.assertNotIn("exiftool", res.stderr)
        dirty = tiny_png() + b"PLANTED"
        self.r.write({"b.png": dirty})
        run(self.r.dir, "add", "-f", "b.png").check_returncode()
        res = subprocess.run([sys.executable, str(PRE_COMMIT)], cwd=str(self.r.dir), env=e, capture_output=True, text=True)
        self.assertIn("emits", res.stderr)


# ---------------------------------------------------------------- item 3: path rules, no exemptions

class PathRuleTests(RepoTestCase):
    def test_b14u_words_are_path_rules(self):
        for w in ("orchestrator/", "tier0/", "worktrees/", "SECRETARY", r"re:\bhandoff\b", "PROCESS-DIARY", "SESSION-LOG"):
            self.assertIn(w, G.PATH_RULES)

    def test_every_rule_refuses_case_insensitively(self):
        for rule in G.PATH_RULES:
            word = "handoff" if rule.startswith("re:") else rule
            for variant in (word, word.lower(), word.upper()):
                path = "x/" + variant + ("y.txt" if variant.endswith("/") else "/y.txt")
                probs = G.scan_path(path, DENY)
                self.assertTrue(any("private path" in p for p in probs), path)

    def test_handoff_rule_is_a_word_match(self):
        for bad in ("docs/handoff.md", "HANDOFF-2026-10-09.md", "a/handoff/b.txt"):
            self.assertTrue(G.scan_path(bad, DENY), bad)
        for ok in ("notes/a-handoffs-b.md", "prehandoffpost.txt"):
            self.assertEqual(G.scan_path(ok, DENY), [], ok)

    def test_private_stem_prefix_rule(self):
        for bad in ("session_log.md", "HANDOFFS.md", "Handoff-2026-10-10.md", "orchestrator.md", "DIARY.txt",
                    "process_diary.md", "Standing-Orders.md", "STATUS.md", "orchestrator/x.txt", "a/b/Session Log.txt"):
            self.assertTrue(any("private path" in p for p in G.scan_path(bad, DENY)), bad)

    def test_status_stem_only_md_txt_files(self):
        for bad in ("STATUS.md", "status.txt", "a/Status-Report.txt"):
            self.assertTrue(G.scan_path(bad, DENY), bad)
        for ok in ("run/status.json", "status/run1.json", "statusdir/x.csv"):
            self.assertEqual(G.scan_path(ok, DENY), [], ok)

    def test_private_stem_prefix_rule_passes_legitimate_names(self):
        for ok in ("src/orin/truck_status.py", "src/orin/README.md", "docs/steering.md", "notes/a-handoffs-b.md"):
            self.assertEqual(G.scan_path(ok, DENY), [], ok)

    def test_path_rules_apply_in_history_and_staged(self):
        self.r.commit({"tier0/x.txt": "clean", "notes/SECRETARY-notes.md": "clean"})
        probs = self.r.audit()
        self.assertTrue(any("tier0/x.txt" in p for p in probs))
        self.assertTrue(any("SECRETARY-notes.md" in p for p in probs))

    def test_path_rule_words_in_content_are_not_refused(self):
        text = "see orchestrator/ tier0/ worktrees/ SECRETARY handoff PROCESS-DIARY SESSION-LOG\n"
        self.assertEqual(G.check_blob("docs/plain.md", text.encode(), DENY, NO_MANIFEST), [])

    def test_no_exempt_paths(self):
        self.assertFalse(hasattr(G, "EXEMPT_PATHS"))
        for p in GATE_FILES:
            self.assertTrue(G.check_blob(p, ("x " + PLANT).encode(), DENY, NO_MANIFEST), p)

    def test_gate_files_pass_the_scan(self):
        # against the fixture denylist plus every opaque-content rule (check.py covers the real one)
        for p in GATE_FILES:
            self.assertEqual(G.scan_path(p, DENY), [], p)
            probs = G.check_blob(p, (REPO / p).read_bytes(), [], NO_MANIFEST)
            self.assertEqual(probs, [], p)


# ---------------------------------------------------------------- item 4: commit-msg reads the raw file

class CommitMsgTests(RepoTestCase):
    def test_clean_message_accepted(self):
        res = self.r.commit_msg("Fix the bracket\n\nPlain body.\n")
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_git_template_with_clean_content_accepted(self):
        tpl = ("Subject\n\n# Please enter the commit message for your changes. Lines starting\n"
               "# with '#' will be ignored, and an empty message aborts the commit.\n#\n"
               "# On branch main\n# Changes to be committed:\n#\tmodified:   README.md\n#\n")
        self.assertEqual(self.r.commit_msg(tpl).returncode, 0)

    def test_trailer_refused_any_case_and_spacing(self):
        for t in (TRAILER, TRAILER.lower(), "co_" + "authored by: x", "CO-" + "AUTHORED-BY x"):
            res = self.r.commit_msg("Subject\n\n" + t + "\n")
            self.assertEqual(res.returncode, 1, t)
            self.assertIn("co-author trailer", res.stderr)

    def test_denylist_hit_refused_by_line_label_only(self):
        res = self.r.commit_msg("Subject " + PLANT + "\n")
        self.assertEqual(res.returncode, 1)
        self.assertIn("(GATE_EXTRA_STRINGS line 1)", res.stderr)
        self.assertNotIn(PLANT, res.stderr)

    def test_comment_and_scissors_lines_are_scanned(self):
        self.assertEqual(self.r.commit_msg("Subject\n# note " + PLANT + "\n").returncode, 1)
        scissors = "# ------------------------ >8 ------------------------\n"
        self.assertEqual(self.r.commit_msg("Subject\n" + scissors + "diff --git a/x b/x\n+" + PLANT + "\n").returncode, 1)
        self.assertEqual(self.r.commit_msg("Subject\n# " + TRAILER + "\n").returncode, 1)

    def test_opaque_message_refused(self):
        self.assertEqual(self.r.commit_msg("Subject\n\n" + b64run(201) + "\n").returncode, 1)
        self.assertEqual(self.r.commit_msg("Subject " + DATA_URI + "\n").returncode, 1)
        res = self.r.commit_msg(b"caf\xe9\n")
        self.assertEqual(res.returncode, 1)
        self.assertIn(G.OPAQUE, res.stderr)

    def test_missing_denylist_refuses(self):
        f = self.r.dir / "MSG"
        f.write_text("fine\n")
        res = subprocess.run([sys.executable, str(COMMIT_MSG), str(f)], cwd=str(self.r.dir),
                             env=env_for(HOME=str(_tmpdir("gate-empty-home-"))), capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)

    def test_real_git_commit_refused_and_head_unchanged(self):
        head = self.r.commit({"a.txt": "clean"})
        self.r.use_real_hooks()
        self.r.write({"b.txt": "clean"})
        run(self.r.dir, "add", "b.txt").check_returncode()
        bad = run(self.r.dir, "commit", "-q", "-m", "subject\n\n" + TRAILER, env={G.EXTRA_ENV: PLANT})
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("commit-msg: REFUSED", bad.stderr)
        self.assertEqual(self.r.rev("HEAD"), head)
        ok = run(self.r.dir, "commit", "-q", "-m", "Add b", env={G.EXTRA_ENV: PLANT})
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_check_py_docstring_warns_against_commit_v(self):
        self.assertIn("Do not use `git commit -v` in this repo (the commit-msg scan reads the whole message file).",
                      CHECK.read_text())


# ---------------------------------------------------------------- item 5: hooks verified by SHA-256

class HookHashTests(unittest.TestCase):
    def copy_hooks(self):
        d = _tmpdir("gate-hooks-")
        for n in G.HOOK_SHA256:
            shutil.copy2(HERE / "hooks" / n, d / n)
        return d

    def test_real_hooks_match(self):
        self.assertEqual(set(G.HOOK_SHA256), {"pre-commit", "commit-msg", "pre-push"})
        self.assertEqual(G.hook_problems(), [])
        for n, want in G.HOOK_SHA256.items():
            self.assertEqual(hashlib.sha256((HERE / "hooks" / n).read_bytes()).hexdigest(), want)

    def test_one_byte_change_fails(self):
        for n in G.HOOK_SHA256:
            d = self.copy_hooks()
            with open(d / n, "ab") as f:
                f.write(b"\n")
            self.assertTrue(any(n in p and "SHA-256" in p for p in G.hook_problems(d)), n)

    def test_stub_that_calls_gate_lib_fails(self):
        d = self.copy_hooks()
        (d / "pre-push").write_text("#!/usr/bin/env python3\nimport gate_lib\ngate_lib.load_denylist()  # ALLOW_PUSH\n")
        self.assertTrue(any("pre-push" in p for p in G.hook_problems(d)))

    def test_missing_and_non_executable_fail(self):
        d = self.copy_hooks()
        (d / "commit-msg").unlink()
        os.chmod(d / "pre-commit", 0o644)
        probs = G.hook_problems(d)
        self.assertTrue(any("commit-msg missing" in p for p in probs), probs)
        self.assertTrue(any("pre-commit is not executable" in p for p in probs), probs)


class CheckPyTests(RepoTestCase):
    """check.py run against a throwaway copy of this repo's scripts/ in a temp git repo."""

    def clone_scripts(self, hooks_path=None):
        shutil.copytree(HERE, self.r.dir / "scripts", ignore=shutil.ignore_patterns("__pycache__", "test_gate.py"))
        hooks_path = hooks_path or str((self.r.dir / "scripts/hooks").resolve())   # absolute: the only accepted form
        run(self.r.dir, "config", "core.hooksPath", hooks_path).check_returncode()

    def run_check(self, home=None, *args):
        return subprocess.run([sys.executable, str(self.r.dir / "scripts/check.py"), *args], cwd=str(self.r.dir),
                              env=env_for(PLANT, HOME=str(home or TEST_HOME)), capture_output=True, text=True)

    def test_clean_clone_passes(self):
        self.clone_scripts()
        self.r.commit({"README.md": "clean"})
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("[OK  ] push_gate", res.stdout)

    def test_planted_history_tree_and_opaque_violations(self):
        self.clone_scripts()
        self.r.commit({"README.md": "clean"}, msg="planted " + PLANT)
        self.r.write({"pages/checklist.md": "pending " + PLANT, "pages/img.md": DATA_URI})
        res = self.run_check()
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("[FAIL] history", res.stdout)
        self.assertIn("[FAIL] private_material", res.stdout)
        self.assertIn("checklist.md", res.stdout)
        self.assertIn("data: URI) in pages/img.md", res.stdout)
        self.assertIn("(GATE_EXTRA_STRINGS line 1)", res.stdout)
        for line in res.stdout.splitlines():
            if "private string" in line:
                self.assertNotIn(PLANT, line)

    def test_withheld_names(self):
        self.r.commit({"sub/" + PLANT + ".txt": "x " + PLANT, "tier0/" + PLANT: "clean", "b.bin": b"\0"})
        probs = self.r.audit() + G.audit_ref(str(self.r.dir), "refs/heads/" + PLANT, self.r.rev("HEAD"), DENY,
                                             manifest=NO_MANIFEST)
        self.assertGreaterEqual(sum(G.WITHHELD in p for p in probs), 4, probs)
        self.assertFalse([p for p in probs if PLANT in p])

    def test_modified_hook_fails_push_gate(self):
        self.clone_scripts()
        with open(self.r.dir / "scripts/hooks/pre-push", "a") as f:
            f.write("# harmless-looking edit\n")
        res = self.run_check()
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("[FAIL] push_gate", res.stdout)
        self.assertIn("pre-push does not match its expected SHA-256", res.stdout)

    def test_non_executable_hook_fails_push_gate(self):
        self.clone_scripts()
        os.chmod(self.r.dir / "scripts/hooks/commit-msg", 0o644)
        res = self.run_check()
        self.assertIn("commit-msg is not executable", res.stdout)
        self.assertEqual(res.returncode, 1)

    def test_wrong_hooks_path_fails_push_gate(self):
        self.clone_scripts(hooks_path=str(HERE / "hooks"))
        res = self.run_check()
        self.assertIn("[FAIL] push_gate", res.stdout)
        self.assertIn("core.hooksPath", res.stdout)

    def test_relative_hooks_path_fails_push_gate(self):
        self.clone_scripts(hooks_path="scripts/hooks")
        res = self.run_check()
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("[FAIL] push_gate", res.stdout)
        self.assertIn("must be an absolute path", res.stdout)

    def test_absolute_hooks_path_of_this_clone_passes_push_gate(self):
        self.clone_scripts()
        res = self.run_check()
        self.assertIn("[OK  ] push_gate", res.stdout)
        self.assertIn("(absolute)", res.stdout)

    def test_absolute_path_through_a_symlink_resolving_here_passes(self):
        link = _tmpdir("gate-link-") / "hooks-link"
        self.clone_scripts()
        link.symlink_to(self.r.dir / "scripts/hooks")
        run(self.r.dir, "config", "core.hooksPath", str(link)).check_returncode()
        self.assertIn("[OK  ] push_gate", self.run_check().stdout)

    def test_unset_hooks_path_fails_push_gate(self):
        self.clone_scripts()
        run(self.r.dir, "config", "--unset", "core.hooksPath").check_returncode()
        self.assertIn("[FAIL] push_gate", self.run_check().stdout)

    def test_denylist_line_reports_skipped_counts(self):
        self.clone_scripts()
        home = _tmpdir("gate-counts-home-")
        d = home / "Projects/clients/robo-bump-truck-build"
        d.mkdir(parents=True)
        (d / "private-strings.txt").write_text("# a comment\n\n#tag\nword\n\u200b\n\u200d\nre:x\\d\n")
        res = self.run_check(home)
        line = [l for l in res.stdout.splitlines() if "entries loaded" in l][0]
        self.assertIn("1 comment lines", line)
        self.assertIn("1 blank", line)
        self.assertIn("2 dropped as empty after normalising", line)
        self.assertIn("[WARN] denylist", res.stdout)
        res2 = self.run_check()
        self.assertIn("[OK  ] denylist", res2.stdout)
        self.assertIn("0 dropped", res2.stdout)

    def test_replace_ref_does_not_hide_history_from_check_py(self):
        self.clone_scripts()
        self.replaced_history()
        res = self.run_check()
        self.assertIn("[FAIL] history", res.stdout)
        self.assertEqual(res.returncode, 1)

    def test_missing_denylist_is_fail_never_ok(self):
        self.clone_scripts()
        res = self.run_check(_tmpdir("gate-empty-home-"))
        self.assertEqual(res.returncode, 1, res.stdout)
        for name in ("denylist", "private_material", "history"):
            self.assertIn("[FAIL] " + name, res.stdout)
            self.assertNotIn("[OK  ] " + name, res.stdout)

    def test_crashing_check_exits_1(self):
        shim = self.r.dir / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            "import sys\nsys.path.insert(0, %r)\nimport gate_lib\n"
            "def boom(*a, **k):\n    raise RuntimeError('forced')\n"
            "gate_lib.audit_files = boom\n" % str(HERE))
        res = subprocess.run([sys.executable, str(CHECK)], cwd=str(REPO),
                             env=env_for(PLANT, PYTHONPATH=str(shim)), capture_output=True, text=True)
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("check errored", res.stdout)

    def test_placeholders_warn_daily_fail_under_flip(self):
        self.clone_scripts()
        self.r.commit({"pages/a.md": "Weight: not yet stated\n", "pages/b.md": "Details to follow.\n"})
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("[WARN] placeholders", res.stdout)
        self.assertIn("pages/a.md:1", res.stdout)
        self.assertIn("pages/b.md:1", res.stdout)
        res = self.run_check(None, "--flip")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("[FAIL] placeholders", res.stdout)

    def test_photo_slot_passes_daily_fails_under_flip(self):
        self.clone_scripts()
        self.r.commit({"pages/a.md": "# Truck\nphoto: x\n"})
        res = self.run_check()
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("[OK  ] placeholders", res.stdout)
        res = self.run_check(None, "--flip")
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("[FAIL] placeholders", res.stdout)
        self.assertIn("pages/a.md:2", res.stdout)

    def test_clean_pages_pass_under_flip(self):
        self.clone_scripts()
        self.r.commit({"pages/a.md": "A photograph of the truck.\n"})
        self.assertEqual(self.run_check(None, "--flip").returncode, 0)

    def test_photo_slot_forms_fail_under_flip(self):
        self.clone_scripts()
        forms = ["- photo: x", "<!-- photo: x -->", "> Photo: x", "**Photo:** x", "photo : s", "\ufeffphoto: s", "see PHOTO: s"]
        files = {"pages/f%d.md" % i: f + "\n" for i, f in enumerate(forms)}
        files["pages/p.html"] = "<p>photo:</p>\n"
        self.r.commit(files)
        self.assertEqual(self.run_check().returncode, 0)
        res = self.run_check(None, "--flip")
        self.assertEqual(res.returncode, 1, res.stdout)
        for name in files:
            self.assertIn(name + ":1", res.stdout)

    def test_photograph_is_not_a_photo_slot(self):
        self.clone_scripts()
        self.r.commit({"pages/a.md": "A photograph: the truck.\n"})
        self.assertEqual(self.run_check(None, "--flip").returncode, 0)

    def test_unknown_arguments_are_refused(self):
        self.clone_scripts()
        self.r.commit({"README.md": "clean"})
        for bad in ("--flp", "-flip", "flip"):
            res = self.run_check(None, bad)
            self.assertEqual(res.returncode, 2, bad + res.stdout)
            self.assertIn("usage", res.stdout)

    def test_flip_with_text_is_refused(self):
        self.clone_scripts()
        f = self.r.dir / "f.txt"
        f.write_text("clean")
        res = self.run_check(None, "--flip", "--text", str(f))
        self.assertEqual(res.returncode, 2, res.stdout)
        self.assertIn("cannot be combined", res.stdout)
        self.assertEqual(self.run_check(None, "--text", str(f)).returncode, 0)

    def test_to_follow_only_as_an_ending(self):
        self.clone_scripts()
        self.r.commit({"pages/a.md": "Steps to follow the plan are short.\n"})
        self.assertIn("[OK  ] placeholders", self.run_check().stdout)
        for i, ok in enumerate(["| print file to follow |", "Files to follow.", "Files (to follow)", "Files to follow  "]):
            self.r.commit({"pages/b%d.md" % i: ok + "\n"})
            self.assertIn("[WARN] placeholders", self.run_check().stdout, ok)
            (self.r.dir / ("pages/b%d.md" % i)).unlink()
            run(self.r.dir, "add", "-A").check_returncode()

    def test_hooks_path_configured_in_this_clone(self):
        out = subprocess.run(["git", "-C", str(REPO), "config", "--get", "core.hooksPath"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, str(G.MAIN_ROOT / "scripts/hooks"))   # the main checkout's, shared by its worktrees
        self.assertTrue(os.path.isabs(out))


class LinkedWorktreeTests(RepoTestCase):
    """The manifest of record, the hooks and the denylist are the main checkout's, from any linked worktree."""

    def setup_main_and_worktree(self):
        main = self.r.dir.resolve()
        shutil.copytree(HERE, main / "scripts", ignore=shutil.ignore_patterns("__pycache__", "test_gate.py"))
        run(main, "add", "-f", "-A").check_returncode()
        run(main, "commit", "-q", "-m", "scripts").check_returncode()
        wt = _tmpdir("gate-wt-") / "wt"
        run(main, "worktree", "add", "-q", "-b", "builder", str(wt)).check_returncode()
        return main, wt.resolve()

    def probe(self, scripts_dir, cwd, code):
        res = subprocess.run([sys.executable, "-c", "import gate_lib as g, pathlib; " + code], cwd=str(cwd),
                             env=env_for(PYTHONPATH=str(scripts_dir)), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout.strip()

    def test_paths_resolve_to_the_main_checkout_from_a_worktree(self):
        main, wt = self.setup_main_and_worktree()
        want = "\n".join([str(main), str(main / "scripts/binary-manifest.txt"), str(main / "scripts/hooks")])
        code = "print(g.MAIN_ROOT, g.MANIFEST_FILE, g.HOOKS_DIR, sep='\\n')"
        for scripts in (main / "scripts", wt / "scripts"):       # the main's gate_lib, and the worktree's own copy
            for cwd in (wt, wt / "scripts", main):
                got = self.probe(scripts, cwd, code)
                self.assertEqual(got, want, (scripts, cwd, got))

    def test_worktree_hook_uses_the_main_manifest_not_its_own(self):
        main, wt = self.setup_main_and_worktree()
        blob = b"\0binary\0"
        sha = hashlib.sha256(blob).hexdigest()
        listed = "# t\n%s  data/b.bin\n" % sha
        for in_main, in_wt, ok in ((listed, "# t\n", True), ("# t\n", listed, False)):
            (main / "scripts/binary-manifest.txt").write_text(in_main)      # the main checkout's (of record)
            (wt / "scripts/binary-manifest.txt").write_text(in_wt)          # a builder's own edit: ignored
            (wt / "data").mkdir(exist_ok=True)
            (wt / "data/b.bin").write_bytes(blob)
            run(wt, "add", "-f", "data/b.bin").check_returncode()
            res = subprocess.run([sys.executable, str(wt / "scripts/hooks/pre-commit")], cwd=str(wt),
                                 env=env_for(), capture_output=True, text=True)
            run(wt, "reset", "-q").check_returncode()
            if ok:
                self.assertEqual(res.returncode, 0, res.stderr)
            else:
                self.assertEqual(res.returncode, 1, res.stderr)
                self.assertIn("binary file not in the SHA-256 manifest", res.stderr)

    def test_worktree_hook_reads_the_denylist_of_record(self):
        main, wt = self.setup_main_and_worktree()
        home = _tmpdir("gate-home2-")
        (home / "Projects/clients/robo-bump-truck-build").mkdir(parents=True)
        (home / "Projects/clients/robo-bump-truck-build/private-strings.txt").write_text("# t\n" + PLANT + "\n")
        (wt / "leak.txt").write_text("x " + PLANT)
        run(wt, "add", "leak.txt").check_returncode()
        for hook in (main / "scripts/hooks/pre-commit", wt / "scripts/hooks/pre-commit"):
            res = subprocess.run([sys.executable, str(hook)], cwd=str(wt), env=env_for(HOME=str(home)),
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 1, res.stderr)
            self.assertIn("denylist line 2", res.stderr)
            self.assertNotIn(PLANT, res.stderr)

    def test_real_git_commit_in_a_worktree_runs_the_mains_hooks(self):
        main, wt = self.setup_main_and_worktree()
        run(main, "config", "core.hooksPath", str(main / "scripts/hooks")).check_returncode()   # shared config
        (wt / "leak.txt").write_text("x " + PLANT)
        run(wt, "add", "leak.txt").check_returncode()
        before = run(wt, "rev-parse", "HEAD").stdout
        res = run(wt, "commit", "-q", "-m", "clean", env={G.EXTRA_ENV: PLANT})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("pre-commit: REFUSED", res.stderr)
        self.assertEqual(run(wt, "rev-parse", "HEAD").stdout, before)

    def test_hook_sha_check_reads_the_mains_hooks(self):
        main, wt = self.setup_main_and_worktree()
        code = "print(g.hook_problems())"
        with open(wt / "scripts/hooks/pre-push", "ab") as f:                # a builder's edit: ignored
            f.write(b"\n")
        self.assertEqual(self.probe(wt / "scripts", wt, code), "[]")
        with open(main / "scripts/hooks/pre-push", "ab") as f:              # the main's hook changed: seen
            f.write(b"\n")
        self.assertIn("pre-push does not match", self.probe(wt / "scripts", wt, code))

    def test_check_py_push_gate_from_a_worktree_accepts_the_mains_hooks_path(self):
        main, wt = self.setup_main_and_worktree()
        run(main, "config", "core.hooksPath", str(main / "scripts/hooks")).check_returncode()
        res = subprocess.run([sys.executable, str(wt / "scripts/check.py")], cwd=str(wt),
                             env=env_for(PLANT), capture_output=True, text=True)
        self.assertIn("[OK  ] push_gate", res.stdout)

    def test_outside_git_falls_back_to_the_files_own_checkout(self):
        d = _tmpdir("gate-nogit-")
        shutil.copytree(HERE, d / "scripts", ignore=shutil.ignore_patterns("__pycache__", "test_gate.py"))
        self.assertEqual(G.find_main_root(d / "scripts"), d)


class TextModeTests(RepoTestCase):
    def check_text(self, content, extra=PLANT):
        f = self.r.dir / "issue.txt"
        f.write_bytes(content if isinstance(content, bytes) else content.encode())
        return subprocess.run([sys.executable, str(CHECK), "--text", str(f)], cwd=str(self.r.dir),
                              env=env_for(extra), capture_output=True, text=True)

    def test_clean_file_exit_0(self):
        res = self.check_text("A clean release note.\n")
        self.assertEqual(res.returncode, 0, res.stdout)

    def test_hit_exit_1_reported_by_label(self):
        res = self.check_text("see " + PLANT + " here")
        self.assertEqual(res.returncode, 1)
        self.assertIn("(GATE_EXTRA_STRINGS line 1)", res.stdout)
        self.assertNotIn(PLANT, res.stdout)

    def test_normalised_and_opaque_caught(self):
        self.assertEqual(self.check_text("ZZLEAK​ZZ").returncode, 1)
        self.assertEqual(self.check_text(b64run(201)).returncode, 1)
        self.assertEqual(self.check_text("-" * 300 + "\n|---|---|\n" + "=" * 250).returncode, 0)
        self.assertEqual(self.check_text(DATA_URI).returncode, 1)
        self.assertEqual(self.check_text(b"caf\xe9").returncode, 1)

    def test_missing_file_exit_2(self):
        res = subprocess.run([sys.executable, str(CHECK), "--text", str(self.r.dir / "nope.txt")],
                             cwd=str(self.r.dir), env=env_for(), capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)


# ---------------------------------------------------------------- item 6: pre-push without ALLOW_PUSH

class PrePushTests(RepoTestCase):
    def test_clean_push_refused_only_for_allow_push_message_unchanged(self):
        sha = self.r.commit({"a.txt": "clean"})
        res = self.r.pre_push(push_line("refs/heads/main", sha))
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.stderr, ALLOW_LINE)

    def test_planted_violations_reported_before_the_allow_push_refusal(self):
        cases = {
            "blob": ({"a.txt": "x " + PLANT}, {}, "clean", "in a.txt"),
            "path": ({PLANT + ".txt": "clean"}, {}, "clean", "file name"),
            "rule path": ({"worktrees/a.txt": "clean"}, {}, "clean", "private path"),
            "message": ({"a.txt": "clean"}, {}, "msg " + PLANT, "private string"),
            "author": ({"a.txt": "clean"}, {"GIT_AUTHOR_EMAIL": PLANT + "@example.com"}, "clean", "private string"),
            "committer": ({"a.txt": "clean"}, {"GIT_COMMITTER_NAME": PLANT}, "clean", "private string"),
            "binary": ({"a.bin": b"\0\1"}, {}, "clean", "binary"),
            "opaque": ({"a.txt": b64run(201)}, {}, "clean", G.OPAQUE),
            "trailer": ({"a.txt": "clean"}, {}, "s\n\n" + TRAILER, "co-author trailer"),
        }
        for name, (files, env, msg, needle) in cases.items():
            r = self.new_repo()
            sha = r.commit(files, msg=msg, env=env)
            res = r.pre_push(push_line("refs/heads/main", sha))
            self.assertEqual(res.returncode, 1, name)
            self.assertIn(needle, res.stderr, name)
            self.assertNotIn(PLANT, res.stderr, name)
            self.assertTrue(res.stderr.endswith(ALLOW_LINE), name)

    def test_ref_name_and_tag_object(self):
        sha = self.r.commit({"a.txt": "clean"})
        self.assertIn("ref name", self.r.pre_push(push_line("refs/heads/" + PLANT, sha)).stderr)
        run(self.r.dir, "tag", "-a", "v1", "-m", "note " + PLANT).check_returncode()
        res = self.r.pre_push(push_line("refs/tags/v1", self.r.rev("v1")))
        self.assertIn("tag object", res.stderr)

    def test_only_pushed_range_is_scanned_but_history_in_range_is(self):
        old = self.r.commit({"a.txt": "leak " + PLANT})
        mid = self.r.commit({"a.txt": "clean"})
        new = self.r.commit({"b.txt": "clean"})
        self.assertEqual(self.r.pre_push(push_line("refs/heads/main", new, mid)).stderr, ALLOW_LINE)
        self.assertIn("private string", self.r.pre_push(push_line("refs/heads/main", new, ZERO)).stderr)
        self.assertNotEqual(old, new)

    def test_branch_delete_not_scanned(self):
        self.r.commit({"a.txt": "leak " + PLANT})
        res = self.r.pre_push("(delete) %s refs/heads/gone %s\n" % (ZERO, self.r.rev("HEAD")))
        self.assertEqual(res.stderr, ALLOW_LINE)

    def test_missing_denylist_refuses(self):
        sha = self.r.commit({"a.txt": "clean"})
        res = self.r.pre_push(push_line("refs/heads/main", sha), home=_tmpdir("gate-empty-home-"))
        self.assertEqual(res.returncode, 1)
        self.assertIn("denylist missing", res.stderr)

    def test_git_push_dry_run_is_refused_by_the_hook(self):
        bare = _tmpdir("gate-bare-")
        run(bare, "init", "-q", "--bare").check_returncode()
        self.r.commit({"a.txt": "clean"})
        self.r.use_real_hooks()
        res = run(self.r.dir, "push", "--dry-run", str(bare), "main", env={G.EXTRA_ENV: PLANT})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(ALLOW_LINE.strip(), res.stderr)
        self.assertEqual(run(bare, "rev-list", "--all").stdout, "")

    def test_replace_ref_is_ignored_pre_push(self):
        x, z = self.replaced_history()
        res = self.r.pre_push(push_line("refs/heads/main", z))
        self.assertIn("private string", res.stderr)
        self.assertIn("(commit %s)" % x[:8], res.stderr)
        self.assertEqual(res.returncode, 1)
        # the same fixture proves the helper matters: with replace honoured the leak is invisible
        seen = run(self.r.dir, "cat-file", "-p", x).stdout
        self.assertNotIn("leaky", seen)
        self.assertIn("clean twin", seen)
        self.assertEqual(G.git(self.r.dir, "cat-file", "commit", x).decode().count("leaky"), 1)
        direct = G.audit_commits(str(self.r.dir), [x], DENY, manifest=NO_MANIFEST)
        self.assertTrue(any("private string" in p for p in direct))

    def test_git_env_sets_no_replace_objects(self):
        self.assertEqual(G.git_env()["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_no_test_environment_carries_allow_push(self):
        self.assertNotIn("ALLOW_PUSH", env_for(PLANT))


class MediaAdmissionLivesInOnePlaceTests(unittest.TestCase):
    """B50c: the old exiftool tag allowlists are gone; the container walk is one module used by every caller."""

    def test_old_allowlists_are_gone(self):
        for f in HERE.glob("*.py"):
            if f.name.startswith("test_"):
                continue
            text = f.read_text()
            for old in ("METADATA_ALLOWLIST", "METADATA_VALUE_ALLOWLIST", "find_exiftool", "off_allowlist"):
                self.assertNotIn(old, text, (f.name, old))
        for old in ("METADATA_ALLOWLIST", "METADATA_VALUE_ALLOWLIST", "find_exiftool", "off_allowlist", "EXIFTOOL_ENV", "WARNING_ALLOWLIST"):
            self.assertFalse(hasattr(G, old), old)

    def test_one_walk_one_caller_function(self):
        for f in HERE.glob("*.py"):
            if f.name.startswith("test_") or f.name == "media_walk.py":
                continue
            self.assertNotIn("def walk_", f.read_text(), f.name)
        self.assertEqual((HERE / "media_walk.py").read_text().count("def walk_jpeg"), 1)
        self.assertEqual((HERE / "gate_lib.py").read_text().count("media_problems("), 1)
        for name in ("check.py", "release-manifest.py"):
            self.assertNotIn("media_walk", (HERE / name).read_text().replace("scripts/media_walk.py", ""), name)

    def test_the_helper_checks_its_own_output_with_the_same_module(self):
        text = STRIP.read_text()
        self.assertIn("media_walk", text)
        self.assertIn("media_problems", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
