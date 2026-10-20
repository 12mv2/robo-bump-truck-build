#!/usr/bin/env python3
"""test_scrub_run.py - tests for scripts/scrub-run.py (stdlib unittest, synthetic run in a tempdir).
Run: python3 scripts/test_scrub_run.py
Planted strings are generic (/home/someuser/, 10.1.2.3, ...); nothing here reads or prints the
real denylist. The check.py --text test uses the real denylist file only through check.py and is
skipped when that file is absent."""
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("scrub_run", HERE / "scrub-run.py")
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

VOL = "/Vol" + "umes/"   # split: the literal is a denylist hit
USR = "/Us" + "ers/"
NAN_BITS = struct.pack(">d", 0.1 + 0.2)


def npy(descr, payload=b""):
    hdr = ("{'descr': '%s', 'fortran_order': False, 'shape': (0,), }" % descr).encode("latin-1")
    hdr += b" " * (63 - (10 + len(hdr)) % 64) + b"\n"
    return b"\x93NUMPY\x01\x00" + len(hdr).to_bytes(2, "little") + hdr + payload


def npy_raw(header_text, payload=b""):
    """A .npy member with an arbitrary header text (padded to 64 bytes, newline-terminated)."""
    hdr = header_text.encode("latin-1")
    hdr += b" " * (63 - (10 + len(hdr)) % 64) + b"\n"
    return b"\x93NUMPY\x01\x00" + len(hdr).to_bytes(2, "little") + hdr + payload


def tree_hash(root):
    h = hashlib.sha256()
    for p in sorted(Path(root).rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


RECORDS = [
    {"t": 1.5, "n": 3, "big": 12345678901234567890, "f": 0.1 + 0.2, "neg": -0.0, "ok": True, "none": None,
     "image": "/home/someuser/runs/run99/frames/000001.jpg",
     "log": "saved " + USR + "someone/other/place/map.json from 10.1.2.3 by robot@robot-box.local",
     "drive": VOL + "SomeDrive/archive/x.bin",
     "host": "robot-box", "user": "someuser", "list": [1, 2.5, "ssh 10.1.2.3 port 22"],
     "/home/someuser/key/path.txt": 7},
    {"t": 2.5, "note": "hostname=robot-box user=someuser ip 172.16.5.9 and version 1.2.3", "img": "/home/someuser/runs/run99"},
]


class ScrubBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.src = base / "run99"
        self.out = base / "staging"
        (self.src / "frames").mkdir(parents=True)
        (self.src / "frames.jsonl").write_text("\n".join(json.dumps(r) for r in RECORDS) + "\n")
        (self.src / "meta.json").write_text(json.dumps({"dir": str(self.src / "frames"), "host": "robot-box", "fps": 30}))
        (self.src / "frames" / "000001.jpg").write_bytes(b"\xff\xd8fakejpeg")
        (self.src / "clip.mp4").write_bytes(b"fakevideo")
        (self.src / "notes.txt").write_text("unrecognised")
        (self.src / "good.npz").write_bytes(self.make_npz({"a": npy("<f8"), "b": npy("|b1")}))
        (self.src / "strs.npz").write_bytes(self.make_npz({"a": npy("<f8"), "names": npy("<U8")}))
        (self.src / "obj.npz").write_bytes(self.make_npz({"o": npy("|O")}))
        self.log = []

    def make_npz(self, members):
        p = Path(self.tmp.name) / "tmp.npz"
        with zipfile.ZipFile(p, "w") as zf:
            for k, v in members.items():
                zf.writestr(k + ".npy", v)
        return p.read_bytes()

    def run_scrub(self, **kw):
        return S.run(self.src, self.out, log=self.log.append, **kw)

    def out_text(self, name):
        return (self.out / name).read_text()


class TestScrub(ScrubBase):
    def test_rewrites(self):
        self.run_scrub(hosts=["robot-box"], users=["someuser"])
        t = self.out_text("frames.jsonl")
        rows = [json.loads(l) for l in t.splitlines()]
        r = rows[0]
        self.assertEqual(r["image"], "frames/000001.jpg")
        self.assertEqual(r["drive"], "x.bin")
        self.assertEqual(r["log"], "saved map.json from 0.0.0.0 by user@host")
        self.assertEqual(r["host"], "host")
        self.assertEqual(r["user"], "user")
        self.assertEqual(r["list"][2], "ssh 0.0.0.0 port 22")
        self.assertIn("path.txt", r)   # key holding a path -> basename
        self.assertEqual(rows[1]["note"], "hostname=host user=user ip 0.0.0.0 and version 1.2.3")
        self.assertEqual(rows[1]["img"], ".")
        for bad in ("/home/", USR, VOL, "10.1.2.3", "172.16.5.9", "robot-box", "someuser"):
            self.assertNotIn(bad, t)
        self.assertEqual(json.loads(self.out_text("meta.json")), {"dir": "frames", "host": "host", "fps": 30})

    def test_absolute_path_under_source_is_run_relative(self):
        (self.src / "q.json").write_text(json.dumps({"a": str(self.src), "b": "x %s/frames/1.jpg y" % self.src, "c": str(self.src) + "2/z"}))
        self.run_scrub()
        d = json.loads(self.out_text("q.json"))
        self.assertEqual((d["a"], d["b"]), (".", "x frames/1.jpg y"))
        self.assertEqual(d["c"], str(self.src) + "2/z")   # a sibling with the same prefix is not the root

    def test_summary_counts(self):
        results, skipped, errors = self.run_scrub(hosts=["robot-box"], users=["someuser"])
        counts = {n: c for n, _, c in results}
        c = counts["frames.jsonl"]
        self.assertGreater(c["path"], 3)
        self.assertGreaterEqual(c["ip"], 3)
        self.assertGreater(c["host"], 0)
        self.assertGreater(c["user"], 0)
        self.assertTrue(any("TOTAL" in l for l in self.log))
        self.assertEqual(errors, [])

    def test_numbers_bit_identical(self):
        self.run_scrub(hosts=["robot-box"], users=["someuser"])
        for orig, new in zip(RECORDS, [json.loads(l) for l in self.out_text("frames.jsonl").splitlines()]):
            self.assertEqual(S.numbers(orig), S.numbers(new))
        r = json.loads(self.out_text("frames.jsonl").splitlines()[0])
        self.assertEqual(struct.pack(">d", r["f"]), NAN_BITS)
        self.assertEqual(r["big"], 12345678901234567890)
        self.assertEqual(str(r["neg"]), "-0.0")

    def test_number_assertion_fires(self):
        class Bad(S.Scrubber):
            def value(self, v, key=None, force=None):
                return 99 if v == 3 else super().value(v, key, force)
        b = Bad(self.src)
        b.prepare()
        with self.assertRaises(S.FileSkip):
            b = Bad(self.src)
            b.prepare()
            S.scrub_doc(b, {"n": 3}, "x")

    def test_images_video_unknown_skipped(self):
        results, skipped, _ = self.run_scrub()
        sk = dict(skipped)
        for name in ("frames/000001.jpg", "clip.mp4", "notes.txt"):
            self.assertIn(name, sk)
            self.assertFalse((self.out / name).exists())
        self.assertFalse((self.out / "frames").exists())

    def test_npz(self):
        results, skipped, _ = self.run_scrub()
        sk = dict(skipped)
        self.assertEqual((self.out / "good.npz").read_bytes(), (self.src / "good.npz").read_bytes())
        self.assertIn("strs.npz", sk)
        self.assertIn("obj.npz", sk)
        self.assertFalse((self.out / "strs.npz").exists())
        self.assertFalse((self.out / "obj.npz").exists())

    def test_source_unchanged(self):
        before = tree_hash(self.src)
        self.run_scrub(hosts=["robot-box"], users=["someuser"])
        self.assertEqual(before, tree_hash(self.src))

    def test_bad_json_skipped_not_half_written(self):
        (self.src / "bad.json").write_text("{not json")
        (self.src / "dup.json").write_text('{"a": 1, "a": 2}')
        results, skipped, errors = self.run_scrub()
        self.assertCountEqual(errors, ["bad.json", "dup.json"])
        self.assertFalse((self.out / "bad.json").exists())
        self.assertFalse((self.out / "dup.json").exists())

    def test_key_collision_skipped(self):
        (self.src / "k.json").write_text(json.dumps({"/home/a1/x/f": 1, "/home/b2/y/f": 2}))
        _, _, errors = self.run_scrub()
        self.assertEqual(errors, ["k.json"])

    def test_path_with_spaces_whole_string(self):
        (self.src / "p.json").write_text(json.dumps({"p": VOL + "My Drive/some dir/a.npz"}))
        self.run_scrub()
        self.assertEqual(json.loads(self.out_text("p.json")), {"p": "a.npz"})

    def test_unicode_line_separator_inside_record(self):
        (self.src / "frames.jsonl").write_text('{"a": "x\u2028y /home/someuser/r/f.jpg", "n": 1}\n\n{"n": 2}\n', encoding="utf-8")
        _, _, errors = self.run_scrub()
        self.assertEqual(errors, [])
        lines = self.out_text("frames.jsonl").split("\n")
        self.assertEqual(lines[:1], ['{"a": "x\\u2028y f.jpg", "n": 1}'])   # escaped, never raw
        self.assertEqual(lines[1:], ["", '{"n": 2}', ""])

    def test_symlink_skipped(self):
        os.symlink(self.src / "meta.json", self.src / "link.json")
        _, skipped, _ = self.run_scrub()
        self.assertIn("link.json", dict(skipped))
        self.assertFalse((self.out / "link.json").exists())


class TestReviewFixes(ScrubBase):
    """One regression test per CONFIRMED finding of the B31r review (synthetic strings only)."""

    def scrub_json(self, doc, name="r.json", **kw):
        (self.src / name).write_text(json.dumps(doc))
        self.run_scrub(**kw)
        return json.loads(self.out_text(name))

    def test_no_early_return_after_whole_path(self):
        d = self.scrub_json({"m": "/home/someuser/run.py: connecting to 10.1.2.3 host=robot-box",
                             "n": "%s/f.log 10.1.2.9 by robot-box" % self.src}, hosts=["robot-box"])
        self.assertEqual(d["m"], "run.py: connecting to 0.0.0.0 host=host")
        self.assertEqual(d["n"], "f.log 0.0.0.0 by host")

    def test_host_user_keys_scrub_nested_values(self):
        d = self.scrub_json({"host": ["robot-box", {"a": "other-box"}], "x": {"host": {"name": "bot", "n": 5}},
                             "user": [["someuser"]], "owner": {"k": ["a", "b"]}})
        self.assertEqual(d["host"], ["host", {"a": "host"}])
        self.assertEqual(d["x"], {"host": {"name": "host", "n": 5}})
        self.assertEqual(d["user"], [["user"]])
        self.assertEqual(d["owner"], {"k": ["user", "user"]})

    def test_names_that_change_are_refused_not_staged(self):
        (self.src / "robot-box-dir").mkdir()
        (self.src / "robot-box-dir" / "ok.json").write_text("{}")
        (self.src / "notes_10.1.2.5.json").write_text("{}")
        (self.src / "fine.json").write_text("{}")
        _, skipped, errors = self.run_scrub(hosts=["robot-box"])
        sk = dict(skipped)
        self.assertIn("robot-box-dir/ok.json", sk)
        self.assertIn("would be changed", sk["robot-box-dir/ok.json"])
        self.assertIn("notes_10.1.2.5.json", sk)
        self.assertIn("robot-box-dir/ok.json", errors)
        self.assertFalse((self.out / "robot-box-dir").exists())
        self.assertFalse((self.out / "notes_10.1.2.5.json").exists())
        self.assertTrue((self.out / "fine.json").exists())

    def test_embedded_path_segments_with_spaces(self):
        d = self.scrub_json({"a": "see " + VOL + "Some Drive/run1x/a.png ok", "b": "at /home/Some User/runs/b.txt now",
                             "c": "in " + VOL + "Some Drive/x/y.png and /home/someuser/z/w.png"})
        self.assertEqual(d["a"], "see a.png ok")
        self.assertEqual(d["b"], "at b.txt now")
        self.assertEqual(d["c"], "in y.png and w.png")
        for v in d.values():
            self.assertNotIn("Drive", v)
            self.assertNotIn("Some", v)

    def test_npz_comment_names_and_header_comment_skipped(self):
        def put(name, members, comment=b""):
            with zipfile.ZipFile(self.src / name, "w") as zf:
                zf.comment = comment
                for k, v in members.items():
                    zf.writestr(k, v)
        good = npy("<f8")
        put("c.npz", {"a.npy": good}, comment=b"made by someuser")
        put("n.npz", {"someuser_secret_name.npy": good, "a.npy": good})
        put("h.npz", {"a.npy": npy_raw("{'descr': '<f8', 'fortran_order': False, 'shape': (0,), }  # owner someuser")})
        put("k.npz", {"a.npy": npy_raw("{'descr': '<f8', 'fortran_order': False, 'shape': (0,), 'note': 'x'}")})
        put("d.npz", {"a.npy": npy_raw("{'descr': 'someuser', 'descr': '<f8', 'fortran_order': False, 'shape': (0,)}")})
        put("s.npz", {"sp ace.npy": good})
        put("b.npz", {"a.npy": good, "robot-box.npy": good})
        _, skipped, _ = self.run_scrub(hosts=["robot-box"], users=["someuser"])
        sk = dict(skipped)
        self.assertIn("comment", sk["c.npz"])
        self.assertIn("changed by the scrub", sk["n.npz"])
        for n in ("h.npz", "k.npz", "d.npz"):
            self.assertIn(n, sk)
            self.assertFalse((self.out / n).exists())
        self.assertIn("plain name.npy", sk["s.npz"])
        self.assertIn("robot-box.npy", sk["b.npz"])
        self.assertEqual((self.out / "good.npz").read_bytes(), (self.src / "good.npz").read_bytes())

    def test_npz_trailing_bytes_and_member_comment_skipped(self):
        data = (self.src / "good.npz").read_bytes()
        (self.src / "t.npz").write_bytes(data + b"someuser trailing text")
        with zipfile.ZipFile(self.src / "mc.npz", "w") as zf:
            zi = zipfile.ZipInfo("a.npy")
            zi.comment = b"someuser"
            zf.writestr(zi, npy("<f8"))
        _, skipped, _ = self.run_scrub()
        sk = dict(skipped)
        self.assertIn("t.npz", sk)
        self.assertIn("mc.npz", sk)
        self.assertFalse((self.out / "t.npz").exists() or (self.out / "mc.npz").exists())

    def test_underscore_is_a_separator(self):
        d = self.scrub_json({"a": "run_by_someuser_2026", "b": "robotbox_log nuc_box_1", "c": "xrobotbox"},
                            hosts=["robotbox", "nuc_box_1"], users=["someuser"])
        self.assertEqual(d["a"], "run_by_user_2026")
        self.assertEqual(d["b"], "host_log host")
        self.assertEqual(d["c"], "xrobotbox")   # letters still bound a token

    def test_nested_json_in_string(self):
        d = self.scrub_json({"a": '{"hostname": "robot-box", "n": [1, 2.50, "10.1.2.3"], "p": "/home/someuser/q/z.txt"}',
                             "b": '[{"user": "someuser"}]', "c": "[INFO] 10.1.2.3 up [x]", "d": '{"hostname": "robot-box"'})
        self.assertEqual(json.loads(d["a"]), {"hostname": "host", "n": [1, 2.5, "0.0.0.0"], "p": "z.txt"})
        self.assertEqual(json.loads(d["b"]), [{"user": "user"}])
        self.assertEqual(d["c"], "[INFO] 0.0.0.0 up [x]")
        self.assertNotIn("robot-box", d["d"])

    def test_nested_json_with_bad_number_refuses_file(self):
        (self.src / "nb.json").write_text(json.dumps({"a": "[1e400]"}))
        _, _, errors = self.run_scrub()
        self.assertIn("nb.json", errors)
        self.assertFalse((self.out / "nb.json").exists())

    def test_more_path_roots_and_user_option(self):
        d = self.scrub_json({"a": "/mnt/data/someuser/run/x.bin", "b": "/media/someuser/Backup Disk/r/y.bin",
                             "c": "/root/secret/z.log", "d": "/tmp/someuser_scratch/w.txt",
                             "e": "C:\\" + "Us" + "ers\\someuser\\runs\\v.csv", "f": "who is someuser at /tmp/q/e.txt",
                             "g": "~/ok and /rootfs/keep"}, users=["someuser"])
        self.assertEqual([d[k] for k in "abcd"], ["x.bin", "y.bin", "z.log", "w.txt"])
        self.assertEqual(d["e"], "v.csv")
        self.assertEqual(d["f"], "who is user at e.txt")
        self.assertEqual(d["g"], "~/ok and /rootfs/keep")

    def test_user_option_applies_everywhere(self):
        d = self.scrub_json({"a": "someuser", "someuser": 1, "b": ["x someuser-y"]}, users=["someuser"])
        self.assertEqual(d, {"a": "user", "user": 1, "b": ["x user-y"]})

    def test_dangling_symlink_output_refused(self):
        target = Path(self.tmp.name) / "nowhere"
        os.symlink(target, self.out)
        with self.assertRaises(S.Refused):
            self.run_scrub()
        self.assertFalse(target.exists())

    def test_lone_surrogate_and_deep_nesting_skip_file_cleanly(self):
        (self.src / "frames.jsonl").write_text('{"a": 1}\n{"a": "\\ud800 x"}\n')
        (self.src / "deep.json").write_text("[" * 100000 + "]" * 100000)
        (self.src / "deep2.json").write_text(json.dumps({"s": "[" * 3000 + "]" * 3000}))
        (self.src / "ok.json").write_text('{"fine": true}')
        _, skipped, errors = self.run_scrub()
        self.assertCountEqual(errors, ["frames.jsonl", "deep.json", "deep2.json"])
        for n in ("frames.jsonl", "deep.json"):
            self.assertIn(n, dict(skipped))
            self.assertFalse((self.out / n).exists())
        self.assertTrue((self.out / "ok.json").exists())
        for p in self.out.rglob("*"):
            if p.is_file():
                self.assertGreater(p.stat().st_size, 0, p)
                self.assertFalse(p.name.startswith(".scrub-tmp-"), p)

    def test_non_strict_numbers_refused(self):
        (self.src / "inf.json").write_text('{"a": 1e400}')
        (self.src / "nan.json").write_text('{"a": NaN}')
        (self.src / "fine.json").write_text('{"a": -1E-2, "b": 5e-324, "c": 1.7976931348623157e308, "d": [1E2, -0, 12345678901234567890]}')
        _, skipped, errors = self.run_scrub()
        self.assertCountEqual(errors, ["inf.json", "nan.json"])
        self.assertIn("strict JSON", dict(skipped)["inf.json"])
        self.assertFalse((self.out / "inf.json").exists() or (self.out / "nan.json").exists())
        out = json.loads(self.out_text("fine.json"), parse_constant=lambda c: self.fail(c))
        self.assertEqual(S.numbers(out), S.numbers(json.loads((self.src / "fine.json").read_text())))
        self.assertEqual(out["d"][0], 100.0)   # decimal text may change, the value does not

    def test_line_separators_escaped_in_output(self):
        (self.src / "frames.jsonl").write_text('{"a": "p\\u2028q\\u2029r\\u0085s", "n": 1}\n{"n": 2}\n')
        self.run_scrub()
        t = self.out_text("frames.jsonl")
        self.assertEqual(len(t.splitlines()), 2)
        self.assertEqual(json.loads(t.splitlines()[0])["a"], "p\u2028q\u2029r\u0085s")

    def test_ipv6(self):
        d = self.scrub_json({"a": "peer fe80::1ff:fe23:4567:890a%eth0 up", "b": "[2001:db8::7]:8080", "c": "addr ::ffff:10.1.2.3.",
                             "d": "at 12:30:45 and std::vector and aa:bb:cc:dd:ee:ff", "e": "lo ::1: refused"})
        self.assertEqual(d["a"], "peer :: up")
        self.assertEqual(d["b"], "[::]:8080")
        self.assertEqual(d["c"], "addr ::.")
        self.assertEqual(d["d"], "at 12:30:45 and std::vector and aa:bb:cc:dd:ee:ff")
        self.assertEqual(d["e"], "lo ::: refused")

    def test_owner_mode_and_capture_format_survive(self):
        d = self.scrub_json({"owner": "simple", "output": {"owner": "legacy"}, "args": {"owner": "simple"},
                             "capture_format": "1920x1440@30", "pf": "yuyv422@29.97", "pf2": "MJPG@30"},
                            users=["someuser"], hosts=["robot-box"])
        self.assertEqual(d, {"owner": "simple", "output": {"owner": "legacy"}, "args": {"owner": "simple"},
                             "capture_format": "1920x1440@30", "pf": "yuyv422@29.97", "pf2": "MJPG@30"})

    def test_unsupplied_logins_and_unknown_owners_still_scrubbed(self):
        d = self.scrub_json({"owner": "alice", "o2": {"owner": ["simple", "alice"]}, "a": "alice@10",
                             "b": "alice@1920x1080", "n": {"x": ["alice@10"]}, "j": json.dumps({"k": "alice@10"}),
                             "c": "1920x1440@30 and alice@10"})
        self.assertEqual(d["owner"], "user")
        self.assertEqual(d["o2"], {"owner": ["simple", "user"]})
        self.assertEqual(d["a"], "user@host")
        self.assertEqual(d["b"], "user@host")
        self.assertEqual(d["n"], {"x": ["user@host"]})
        self.assertEqual(json.loads(d["j"]), {"k": "user@host"})
        self.assertEqual(d["c"], "1920x1440@30 and user@host")

    def test_owner_with_supplied_user_and_paths_still_scrubbed(self):
        d = self.scrub_json({"owner": "someuser", "o2": "owned by someuser", "capture_format": "someuser@30",
                             "p": USR + "someuser/x/run.py", "uh": "pi@robot-7"},
                            users=["someuser"], hosts=["robot-box"])
        self.assertEqual(d["owner"], "user")
        self.assertEqual(d["o2"], "owned by user")
        self.assertEqual(d["capture_format"], "user@host")
        self.assertEqual(d["p"], "run.py")
        self.assertEqual(d["uh"], "user@host")

    def test_user_at_host_keeps_port(self):
        d = self.scrub_json({"a": "pi@robot-7:22", "b": "host: 22 user: 1000"})
        self.assertEqual(d["a"], "user@host:22")
        self.assertEqual(d["b"], "host: 22 user: 1000")


class TestRefusals(ScrubBase):
    def test_equal(self):
        with self.assertRaises(S.Refused):
            S.run(self.src, self.src, log=self.log.append)

    def test_inside_source(self):
        with self.assertRaises(S.Refused):
            S.run(self.src, self.src / "staging", log=self.log.append)
        self.assertFalse((self.src / "staging").exists())

    def test_inside_via_dotdot(self):
        with self.assertRaises(S.Refused):
            S.run(self.src, self.src / "frames" / ".." / "x", log=self.log.append)

    def test_nonempty_output(self):
        self.out.mkdir()
        (self.out / "x").write_text("x")
        with self.assertRaises(S.Refused):
            self.run_scrub()
        self.assertEqual(os.listdir(self.out), ["x"])

    def test_empty_existing_output_ok(self):
        self.out.mkdir()
        self.run_scrub()
        self.assertTrue((self.out / "frames.jsonl").exists())

    def test_output_is_file(self):
        self.out.write_text("x")
        with self.assertRaises(S.Refused):
            self.run_scrub()

    def test_symlink_output_into_source(self):
        os.symlink(self.src, self.out)
        with self.assertRaises(S.Refused):
            self.run_scrub()

    def test_missing_source(self):
        with self.assertRaises(S.Refused):
            S.run(self.src / "nope", self.out, log=self.log.append)

    def test_cli_exit_codes(self):
        cmd = [sys.executable, str(HERE / "scrub-run.py")]
        r = subprocess.run(cmd + [str(self.src), str(self.src)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run(cmd + [str(self.src), str(self.out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipped", r.stdout)


@unittest.skipUnless((Path.home() / "Projects/clients/robo-bump-truck-build/private-strings.txt").is_file(),
                     "denylist not present")
class TestGateOnOutput(ScrubBase):
    def test_check_text_clean_after_scrub(self):
        self.run_scrub(hosts=["robot-box"], users=["someuser"])
        # .npz is binary: check.py --text refuses it as not UTF-8 by design (it needs a manifest entry)
        outs = [p for p in self.out.rglob("*") if p.is_file() and p.suffix != ".npz"]
        self.assertTrue(outs)
        for p in outs:
            r = subprocess.run([sys.executable, str(HERE / "check.py"), "--text", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "check.py --text refused %s (%s)" % (p.name, r.stdout.splitlines()[:1]))


if __name__ == "__main__":
    unittest.main()
