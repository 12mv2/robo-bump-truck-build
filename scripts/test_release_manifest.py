#!/usr/bin/env python3
"""test_release_manifest.py - tests for scripts/release-manifest.py (stdlib unittest, no network).

Run:  python3 scripts/test_release_manifest.py
Every test uses a synthetic tempdir and a synthetic denylist file written here (generic rules only);
the real denylist is never read."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate_lib as G  # noqa: E402

_spec = importlib.util.spec_from_file_location("release_manifest", HERE / "release-manifest.py")
RM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RM)

PLANT = "QQMANIFESTQQ"
RULES = ["# synthetic rules", PLANT, r"re:/home/[a-z]+/"]


def png(extra=b""):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b"") + extra)


def npy(descr="<f8", shape=(1,), payload=b"\0" * 8):
    hdr = ("{'descr': '%s', 'fortran_order': False, 'shape': %r, }" % (descr, shape)).encode()
    hdr += b" " * (63 - (10 + len(hdr))) + b"\n" if (10 + len(hdr) + 1) % 64 else b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(hdr)) + hdr + payload


def npz(members, comment=b""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in members.items():
            zf.writestr(n, d)
        zf.comment = comment
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stage = self.tmp / "stage"
        self.stage.mkdir()
        self.deny_file = self.tmp / "deny.txt"
        self.deny_file.write_text("\n".join(RULES) + "\n")
        self.side = self.tmp / "desc.tsv"
        self.out = self.tmp / "out" / "manifest.json"
        self.out.parent.mkdir()
        self.lines = []
        self.unlisted = set()
        self.sha_file = self.tmp / "sha.txt"

    def put(self, rel, data, desc="a sample file"):
        p = self.stage / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())
        if desc is not None:
            self.lines.append("%s\t%s" % (rel, desc))
        return p

    def run_main(self):
        self.side.write_text("\n".join(self.lines) + "\n")
        shas = ["%s  %s" % (hashlib.sha256(p.read_bytes()).hexdigest(), p.relative_to(self.stage).as_posix())
                for p in sorted(self.stage.rglob("*")) if p.is_file() and p.suffix.lower() == ".npz"
                and p.relative_to(self.stage).as_posix() not in self.unlisted]
        self.sha_file.write_text("# test sha manifest\n" + "\n".join(shas) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = RM.main([str(self.stage), "--descriptions", str(self.side), "--out", str(self.out),
                          "--denylist", str(self.deny_file), "--sha-manifest", str(self.sha_file)])
        return rc, buf.getvalue()


class Tests(Base):
    def test_clean_set_correct_sha256(self):
        body = b'{"a": 1}\n'
        self.put("run/frames.jsonl", body, "frame records")
        self.put("run/arr.NPZ", npz({"a.npy": npy()}), "array bundle")
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        m = json.loads(self.out.read_text())
        by = {f["path"]: f for f in m["files"]}
        self.assertEqual(by["run/frames.jsonl"]["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(by["run/frames.jsonl"]["bytes"], len(body))
        self.assertEqual(by["run/frames.jsonl"]["description"], "frame records")
        self.assertEqual(m["total_bytes"], len(body) + (self.stage / "run/arr.NPZ").stat().st_size)
        self.assertIn("files: 2", out)

    def test_text_behind_image_extension_scanned(self):
        for name in ("notes.jpg", "notes.mp4", "notes.heic"):
            self.put(name, "run notes: %s\n" % PLANT)
        self.put("g.bin", "GIF89a %s\n" % PLANT)
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertGreaterEqual(out.count("private string"), 4)
        self.assertNotIn(PLANT, out)

    def test_utf16_bom_text_scanned(self):
        self.put("u.csv", ("a,%s\n" % PLANT).encode("utf-16"))
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("private string", out)

    def test_npz_member_rules(self):
        cases = {
            "txt.npz": npz({"notes.txt": b"hello"}),
            "name.npz": npz({PLANT + ".npy": npy()}),
            "obj.npz": npz({"a.npy": npy("|O")}),
            "str.npz": npz({"a.npy": npy("<U4")}),
            "cmt.npz": npz({"a.npy": npy()}, comment=PLANT.encode()),
            "up.NPZ": npz({"a.NPY": npy()}),
            "magic.npz": b"PK" + PLANT.encode(),
        }
        for n, d in cases.items():
            self.put(n, d)
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        for n in cases:
            self.assertIn(n, out, n)
        self.assertNotIn(PLANT, out)

    def test_npz_header_planted_string_refused(self):
        # a trailing comment keeps the header a valid plain dict, so only the raw string scan can catch it
        hb = ("{'descr': '<f8', 'fortran_order': False, 'shape': (1,), } # %s\n" % PLANT).encode()
        bad = b"\x93NUMPY\x01\x00" + struct.pack("<H", len(hb)) + hb + b"\0" * 8
        self.put("hdr.npz", npz({"a.npy": bad}))
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("npy header", out)
        self.assertIn("private string", out)
        self.assertNotIn(PLANT, out)

    def test_npz_clean_with_manifest_entry_admitted(self):
        self.put("ok.npz", npz({"a.npy": npy()}))
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)

    def test_npz_without_manifest_entry_refused(self):
        self.put("ok.npz", npz({"a.npy": npy()}))
        self.unlisted.add("ok.npz")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("not in the SHA manifest", out)
        self.assertFalse(self.out.exists())

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_unreadable_dir_and_file_refused(self):
        self.put("sub/z.txt", "hello")
        self.put("f.txt", "hello")
        os.chmod(self.stage / "f.txt", 0)
        os.chmod(self.stage / "sub", 0)
        self.addCleanup(os.chmod, self.stage / "sub", 0o755)
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("unreadable directory", out)
        self.assertIn("unreadable file", out)

    def test_cf_only_description_is_missing(self):
        self.put("a.txt", "hello", desc="\u200b\u00a0")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("needs", out)

    def test_out_is_existing_directory(self):
        self.put("a.txt", "hello")
        self.side.write_text(self.lines[0] + "\n")
        before = set(os.listdir(self.out.parent))
        (self.out.parent / "isdir").mkdir()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = RM.main([str(self.stage), "--descriptions", str(self.side), "--out", str(self.out.parent / "isdir"),
                          "--denylist", str(self.deny_file), "--sha-manifest", str(self.sha_file)])
        self.assertEqual(rc, 2)
        self.assertEqual(set(os.listdir(self.out.parent)), before | {"isdir"})

    def test_size_and_hash_from_one_read(self):
        data = b"x" * 1000
        p = self.put("a.txt", data)
        orig = RM.read_once

        def swap(full):
            res = orig(full)
            p.write_bytes(b"changed after the read")   # a later re-stat or re-read would see this
            return res
        RM.read_once = swap
        try:
            rc, out = self.run_main()
        finally:
            RM.read_once = orig
        self.assertEqual(rc, 0, out)
        rec = json.loads(self.out.read_text())["files"][0]
        self.assertEqual(rec["bytes"], 1000)
        self.assertEqual(rec["sha256"], hashlib.sha256(data).hexdigest())


    def test_over_20mb_without_entry(self):
        p = self.put("big.npz", npz({"a.npy": npy()}), desc=None)
        with open(p, "r+b") as fh:
            fh.truncate(21 * 2 ** 20)   # sparse
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("over 20 MB and no description", out)
        self.assertFalse(self.out.exists())

    def test_no_description_small_file(self):
        self.put("a.txt", "hello", desc=None)
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("no description entry", out)

    def test_denylisted_filename_withheld(self):
        self.put("x-%s.txt" % PLANT.lower(), "hello")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", out)
        self.assertNotIn(PLANT.lower(), out.lower())

    def test_outside_symlink(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("hello")
        os.symlink(outside, self.stage / "link.txt")
        self.lines.append("link.txt\ta link")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("outside the staging dir", out)

    def test_symlinked_dir_outside(self):
        d = self.tmp / "elsewhere"
        d.mkdir()
        (d / "f.txt").write_text("hello")
        os.symlink(d, self.stage / "dirlink")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("outside the staging dir", out)

    def test_dotdot_in_sidecar(self):
        self.put("a.txt", "hello")
        self.lines.append("../stage/a.txt\tsneaky")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("no '..'", out)

    def test_private_path_in_text(self):
        self.put("log.txt", "opened /home/someone/data/run1 ok\n")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("private string", out)
        self.assertNotIn("/home/someone", out)

    def test_unreadable_type_listed(self):
        self.put("blob.bin", b"\x00\x01\x02binary")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("blob.bin", out)
        self.assertIn("not text, image/video or .npz", out)

    def test_npz_name_without_zip_magic(self):
        self.put("fake.npz", b"hello text")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("not a readable zip", out)

    def test_sidecar_entry_without_file(self):
        self.put("a.txt", "hello")
        self.lines.append("ghost.txt\tnot there")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("not in the staging dir", out)

    def test_out_inside_staging_is_usage_error(self):
        self.put("a.txt", "hello")
        self.side.write_text(self.lines[0] + "\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = RM.main([str(self.stage), "--descriptions", str(self.side), "--out", str(self.stage / "m.json"),
                          "--denylist", str(self.deny_file), "--sha-manifest", str(self.sha_file)])
        self.assertEqual(rc, 2)

    def test_clean_image_passes_gps_image_refused(self):
        exe = shutil.which("exiftool")
        if not exe:
            self.skipTest("no exiftool to build the dirty fixture")
        p = self.put("ok.png", png(), "clean image")
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)
        bad = self.put("gps.png", png(), "tagged image")
        subprocess.run([exe, "-overwrite_original", "-XMP:GPSLatitude=1.5", str(bad)], check=True, capture_output=True)
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        self.assertIn("gps.png", out)

    def test_media_walk_applies_and_has_no_second_copy(self):
        self.assertIs(RM.g, G)                                                # imported, not copied
        text = (HERE / "release-manifest.py").read_text()
        self.assertNotIn("METADATA_ALLOWLIST", text)
        self.assertNotIn("def walk_", text)
        self.put("ok.png", png(), "clean image")
        value = b"zz-generic-value"
        dirty = {"trailer.png": png(value), "jpg.jpg": b"\xff\xd8\xff\xe1" + value, "clip.mp4": b"\0\0\0\x18ftypmp42" + value,
                 "pic.gif": b"GIF89a" + value, "scan.tif": b"II*\0" + value}
        for name, data in dirty.items():
            self.put(name, data, "dirty image")
        rc, out = self.run_main()
        self.assertEqual(rc, 1)
        for name in dirty:
            self.assertIn(name, out)
        self.assertNotIn("zz-generic-value", out)
        self.assertNotIn("ok.png", out)
        self.assertIn("emits", out)

    def test_helper_output_is_accepted(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("no Pillow")
        src = self.tmp / "in.png"
        Image.new("RGB", (20, 12), (10, 200, 30)).save(src)
        clean = self.tmp / "clean.png"
        subprocess.run([str(HERE / "strip-image-metadata.sh"), str(src), str(clean)], check=True, capture_output=True)
        self.put("ok.png", clean.read_bytes(), "helper output")
        rc, out = self.run_main()
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
