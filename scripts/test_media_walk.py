#!/usr/bin/env python3
"""test_media_walk.py - B50c: the gate admits exactly the JPEG / PNG / MP4 structure scripts/strip-image-metadata.sh
emits and refuses everything else (stdlib unittest; Pillow for the image fixtures, ffmpeg + libx264 for the video).

Run:  python3 scripts/test_media_walk.py
Every dirty file is built here in pure python from a clean helper output (a chunk / segment / box / NAL unit inserted
or a field changed), so each of the 12 bypass classes of research/B50-gate-recheck.md is a test that fails if the walk
ever lets that channel through, and a control (an identity rewrite) proves the mutator itself keeps the file valid."""
import atexit
import hashlib
import os
import pathlib
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gate_lib as G  # noqa: E402
import media_walk as M  # noqa: E402

STRIP = HERE / "strip-image-metadata.sh"
DENY = G.parse_entries(["zz-synthetic-" + "entry-never-matches"], "test")
PLANT = b"PLANTED-PRIVATE-NAME 303-555-0100"
LONG_TEXT = (PLANT + b" ") * 50                  # ~1.7 KB of text
ICC_DIR = pathlib.Path("/System/Library/ColorSync/Profiles")
FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if os.access("/opt/homebrew/bin/ffmpeg", os.X_OK) else None)
try:
    from PIL import Image, ImageCms
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
HAVE_X264 = bool(FFMPEG) and b"libx264" in subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True).stdout
HAVE_X265 = bool(FFMPEG) and b"libx265" in subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True).stdout
REAL_HEIC = pathlib.Path.home() / "Downloads/IMG_5897.HEIC"      # a real iPhone photo / video, if this machine has them
REAL_MOV = pathlib.Path.home() / "Downloads/IMG_5891.MOV"


def _tmpdir(prefix):
    d = pathlib.Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, str(d), True)
    return d


def problems(name, data):
    return G.image_problems(name, data, DENY)


def run_strip(src, out, **kw):
    return subprocess.run([str(STRIP), str(src), str(out)], capture_output=True, text=True, **kw)


def noise_image(w=70, h=50, seed=1):
    rng = random.Random(seed)
    im = Image.new("RGB", (w, h))
    im.putdata([((x * 255) // w, (y * 255) // h, rng.randrange(256)) for y in range(h) for x in range(w)])
    return im


# ---------------------------------------------------------------- PNG / JPEG byte helpers

def png_chunk(t, body):
    return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xffffffff)


def png_split(d):
    pos, out = 8, []
    while pos < len(d):
        ln, t = struct.unpack(">I4s", d[pos:pos + 8])
        out.append((t, d[pos + 8:pos + 8 + ln]))
        pos += 12 + ln
    return out


def png_join(chunks):
    return M.PNG_SIG + b"".join(png_chunk(t, b) for t, b in chunks)


def png_insert(d, t, body, where="before_idat"):
    ch = png_split(d)
    i = next(k for k, c in enumerate(ch) if c[0] == b"IDAT") if where == "before_idat" else len(ch) - 1
    ch.insert(i, (t, body))
    return png_join(ch)


def jpeg_insert(d, marker, payload):
    """A segment right after the JFIF APP0 (offset 20)."""
    return d[:20] + bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload + d[20:]


def exif_ascii(tag, text):
    """APP1 Exif payload whose only IFD0 entry is `tag` written with ASCII type."""
    val = text + b"\0"
    ifd = struct.pack("<H", 1) + struct.pack("<HHII", tag, 2, len(val), 8 + 2 + 12 + 4) + struct.pack("<I", 0)
    return b"Exif\0\0" + b"II*\0" + struct.pack("<I", 8) + ifd + val


def icc_blob(text, pad=0):
    body = b"acsp" + text + b"\0" * pad
    return struct.pack(">I", 128 + len(body)) + b"\0" * 124 + body


# ---------------------------------------------------------------- MP4 box-tree mutator

_CONT = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
_HEAD = {b"stsd": 8, b"avc1": 78}


class Box:
    def __init__(self, t, head=b"", kids=None, body=b""):
        self.t, self.head, self.kids, self.body = t, head, kids, body

    def dump(self):
        payload = self.head + (b"".join(k.dump() for k in self.kids) if self.kids is not None else self.body)
        return struct.pack(">I4s", 8 + len(payload), self.t) + payload

    def find(self, t):
        return next(k for k in self.kids if k.t == t)


def parse_boxes(d, s=0, e=None):
    e = len(d) if e is None else e
    out = []
    while s < e:
        size, t = struct.unpack(">I4s", d[s:s + 8])
        end = s + size
        if t in _CONT:
            out.append(Box(t, b"", parse_boxes(d, s + 8, end)))
        elif t in _HEAD:
            out.append(Box(t, d[s + 8:s + 8 + _HEAD[t]], parse_boxes(d, s + 8 + _HEAD[t], end)))
        else:
            out.append(Box(t, b"", None, d[s + 8:end]))
        s = end
    return out


class Root:
    def __init__(self, boxes):
        self.kids = boxes

    def find(self, t):
        return next(k for k in self.kids if k.t == t)


def mp4_mutate(data, fn, shift_offsets=True):
    """Parse, let fn(root) change the tree, serialise, and move every stco offset by the growth of moov (it precedes mdat)."""
    root = Root(parse_boxes(data))
    old_moov = len(root.find(b"moov").dump())
    fn(root)
    if shift_offsets:
        delta = len(root.find(b"moov").dump()) - old_moov
        stco = root.find(b"moov").find(b"trak").find(b"mdia").find(b"minf").find(b"stbl").find(b"stco")
        n = struct.unpack(">I", stco.body[4:8])[0]
        offs = struct.unpack(">%dI" % n, stco.body[8:])
        stco.body = stco.body[:8] + struct.pack(">%dI" % n, *[o + delta for o in offs])
    return b"".join(k.dump() for k in root.kids)


def stbl_of(root):
    return root.find(b"moov").find(b"trak").find(b"mdia").find(b"minf").find(b"stbl")


def avc1_of(root):
    return stbl_of(root).find(b"stsd").find(b"avc1")


def append_nal(root, nal_header, payload):
    """Add one length-prefixed NAL unit (`nal_header` + payload) to the last sample of mdat."""
    nal = struct.pack(">I", 1 + len(payload)) + bytes([nal_header]) + payload
    stsz = stbl_of(root).find(b"stsz")
    n = struct.unpack(">I", stsz.body[8:12])[0]
    last = struct.unpack(">I", stsz.body[-4:])[0]
    stsz.body = stsz.body[:-4] + struct.pack(">I", last + len(nal))
    root.find(b"mdat").body += nal
    assert n > 0


# ---------------------------------------------------------------- fixtures

@unittest.skipUnless(HAVE_PIL, "Pillow required")
class Fixtures(unittest.TestCase):
    """Class-level clean helper outputs, built once."""
    tmp = None
    jpeg = png = mp4 = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = _tmpdir("mw-")
        src = cls.tmp / "src.png"
        noise_image().save(src)
        # a JPEG source goes through the JPEG path of the helper, a PNG source through the PNG path
        noise_image().save(cls.tmp / "src.jpg", quality=80)
        assert run_strip(cls.tmp / "src.jpg", cls.tmp / "clean.jpg").returncode == 0
        assert run_strip(src, cls.tmp / "clean.png").returncode == 0
        cls.jpeg = (cls.tmp / "clean.jpg").read_bytes()
        cls.png = (cls.tmp / "clean.png").read_bytes()
        cls.mp4 = None
        if HAVE_X264:
            clip = cls.tmp / "clip.mp4"
            res = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                                  "testsrc2=size=128x96:rate=10:duration=2", "-f", "lavfi", "-i", "sine=duration=2", "-shortest",
                                  "-metadata", "title=" + PLANT.decode(), "-metadata", "creation_time=2020-01-02T03:04:05Z",
                                  "-c:v", "libx264", "-g", "5", "-bf", "2", "-c:a", "aac", str(clip)], capture_output=True)
            assert res.returncode == 0, res.stderr
            res = run_strip(clip, cls.tmp / "clean.mp4")
            assert res.returncode == 0, res.stderr
            cls.mp4 = (cls.tmp / "clean.mp4").read_bytes()

    def refused(self, name, data, reason=None):
        probs = problems(name, data)
        self.assertTrue(probs, "NOT refused: " + name)
        self.assertIn("scripts/strip-image-metadata.sh", probs[0])
        self.assertNotIn(PLANT.decode(), probs[0])
        self.assertNotIn("does not parse", probs[0], "refused by accident (an exception), not by a rule: " + name)
        if reason:
            self.assertIn(reason, probs[0])
        return probs


class CleanOutputTests(Fixtures):
    def test_clean_helper_outputs_are_admitted(self):
        self.assertEqual(problems("a.jpg", self.jpeg), [])
        self.assertEqual(problems("a.jpeg", self.jpeg), [])
        self.assertEqual(problems("A.JPG", self.jpeg), [])
        self.assertEqual(problems("a.png", self.png), [])

    @unittest.skipUnless(HAVE_X264, "no ffmpeg / libx264")
    def test_clean_mp4_is_admitted(self):
        self.assertEqual(problems("a.mp4", self.mp4), [])
        self.assertEqual(problems("a.m4v", self.mp4), [])

    def test_a_clean_file_passes_check_blob_only_with_its_manifest_entry(self):
        sha = hashlib.sha256(self.png).hexdigest()
        self.assertEqual(G.check_blob("a.png", self.png, DENY, frozenset({("a.png", sha)})), [])
        self.assertTrue(any("manifest" in p for p in G.check_blob("a.png", self.png, DENY, frozenset())))

    def test_extension_must_match_content(self):
        self.refused("a.png", self.jpeg, "extension")
        self.refused("a.jpg", self.png, "extension")
        self.refused("a.txt", self.jpeg, "extension")          # an image renamed to .txt is still an image (magic bytes)
        self.refused("a", self.png, "extension")
        self.refused("a.mov", self.mp4 or self.jpeg, "extension")

    def test_unreadable_and_non_images_named_as_images(self):
        self.refused("x.jpg", b"\xff\xd8\xff junk")
        self.refused("x.jpg", b"just text, not an image\n")
        self.refused("x.jpg", os.urandom(500))
        self.refused("x.png", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.refused("x.jpg", b"")
        for cut in (2, 20, 100, len(self.jpeg) - 3):
            self.refused("x.jpg", self.jpeg[:cut])
        for cut in (8, 40, len(self.png) - 5):
            self.refused("x.png", self.png[:cut])

    def test_other_formats_are_refused_outright(self):
        gif = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
        self.refused("a.gif", gif)
        for ext in (".heic", ".heif", ".avif", ".tif", ".tiff", ".webp", ".bmp", ".dng", ".jxl", ".mov"):
            self.refused("a" + ext, b"\0\0\0\x18ftypheic" + b"\0" * 20)
        self.refused("a.bmp", b"BM" + b"\0" * 50)
        self.refused("a.webp", b"RIFF\0\0\0\0WEBPVP8 ")
        self.refused("a.tif", b"II*\0\x08\0\0\0\0\0\0\0\0\0")

    def test_no_exiftool_is_involved(self):
        env_path = _tmpdir("mw-nopath-")
        old = os.environ.get("PATH")
        os.environ["PATH"] = str(env_path)
        try:
            self.assertEqual(problems("a.png", self.png), [])
        finally:
            os.environ["PATH"] = old
        self.assertFalse(hasattr(G, "find_exiftool"))
        self.assertFalse(hasattr(G, "METADATA_ALLOWLIST"))
        self.assertFalse(hasattr(G, "METADATA_VALUE_ALLOWLIST"))
        self.assertNotIn("exiftool", (HERE / "gate_lib.py").read_text().replace("no exiftool", ""))


# ---------------------------------------------------------------- the 12 bypass classes (B50-gate-recheck.md)

class JpegBypassTests(Fixtures):
    def test_identity_insert_of_nothing_is_still_clean(self):
        self.assertEqual(problems("a.jpg", self.jpeg[:20] + self.jpeg[20:]), [])

    def test_A1_icc_text_tags_in_a_jpeg_app2(self):
        for text in (b"desc" + PLANT, b"cprt" + PLANT, b"tech" + PLANT, b"lumi" + PLANT):
            self.refused("a.jpg", jpeg_insert(self.jpeg, 0xE2, b"ICC_PROFILE\0\1\1" + icc_blob(text)))

    def test_A2_icc_binary_tag_blob_a2b0(self):
        self.refused("a.jpg", jpeg_insert(self.jpeg, 0xE2, b"ICC_PROFILE\0\1\1" + icc_blob(b"A2B0" + LONG_TEXT, 1300)))

    def test_B2_exif_ascii_typed_allowlisted_tags(self):
        for tag in (0x0112, 0x0100, 0x0213):           # Orientation, ImageWidth, YCbCrPositioning
            self.refused("a.jpg", jpeg_insert(self.jpeg, 0xE1, exif_ascii(tag, PLANT)))
        self.refused("a.jpg", jpeg_insert(self.jpeg, 0xE1, exif_ascii(0xC000, PLANT)))       # unknown tag
        self.refused("a.jpg", jpeg_insert(self.jpeg, 0xE1, exif_ascii(0x0111, LONG_TEXT)))   # StripOffsets-like blob

    def test_B2_tiff_fillorder_ascii_is_refused_as_a_format(self):
        buf = _tmpdir("mw-tiff-") / "t.tif"
        noise_image(8, 8).save(buf)
        data = bytearray(buf.read_bytes())
        data += PLANT
        self.refused("t.tif", bytes(data))

    def test_B3_trailers_after_eoi(self):
        second = jpeg_insert(self.jpeg, 0xE0, b"JFXX\0\x10")[:0] + self.jpeg
        for trailer in (b"\n" + PLANT, LONG_TEXT * 4, second, b"PK\x03\x04" + b"\0" * 30 + PLANT):
            self.refused("a.jpg", self.jpeg + trailer)

    def test_unknown_app_segments_and_comments(self):
        for marker in (0xE1, 0xE2, 0xED, 0xEF, 0xFE, 0xEC):
            self.refused("a.jpg", jpeg_insert(self.jpeg, marker, b"zz" + PLANT))
        self.refused("a.jpg", jpeg_insert(self.jpeg, 0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta>" + PLANT))
        self.refused("a.jpg", self.jpeg[:2] + b"\xff\xfe\x00\x24" + PLANT[:34] + self.jpeg[2:])        # COM before JFIF

    def test_scan_slack_and_corruption_are_refused(self):
        d = self.jpeg
        self.refused("a.jpg", d[:-2] + b"\x00" + d[-2:], "scan")                       # one byte of slack before EOI
        self.refused("a.jpg", d[:-2] + PLANT + d[-2:])                                 # text before EOI
        self.refused("a.jpg", d[:-2] + b"\xff\x00" + d[-2:])
        self.refused("a.jpg", d[:-2] + b"\xff\xd0" + d[-2:])                           # restart marker
        self.refused("a.jpg", d[:-3] + b"\x00" + d[-2:])                               # padding bits not ones (usually)

    def test_tables_and_headers_must_be_the_helpers(self):
        d = bytearray(self.jpeg)
        q = bytes(d).index(b"\xff\xdb") + 6
        d[q] ^= 1
        self.refused("a.jpg", bytes(d), "DQT")
        d = bytearray(self.jpeg)
        d[bytes(d).index(b"\xff\xc4") + 8] ^= 1
        self.refused("a.jpg", bytes(d), "DHT")
        d = bytearray(self.jpeg)
        d[bytes(d).index(b"\xff\xc0") + 1] = 0xC2                                       # progressive
        self.refused("a.jpg", bytes(d))
        d = bytearray(self.jpeg)
        d[bytes(d).index(b"JFIF") + 8] = 1                                              # JFIF units
        self.refused("a.jpg", bytes(d))

    def test_other_jpeg_encodings_are_refused(self):
        tmp = _tmpdir("mw-jenc-")
        im = noise_image()
        for label, kw in (("gray", {}), ("prog", {"progressive": True}), ("opt", {"optimize": True}), ("q75", {"quality": 75}),
                          ("444", {"subsampling": 0}), ("icc", {"icc_profile": ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()})):
            f = tmp / (label + ".jpg")
            (im.convert("L") if label == "gray" else im).save(f, **kw)
            self.refused(f.name, f.read_bytes())


class PngBypassTests(Fixtures):
    def test_identity_rebuild_is_clean(self):
        self.assertEqual(problems("a.png", png_join(png_split(self.png))), [])

    def test_A1_A4_iccp_profile_and_free_text_name(self):
        z = zlib.compress(icc_blob(b"desc" + PLANT))
        self.refused("a.png", png_insert(self.png, b"iCCP", b"sRGB IEC61966-2.1\0\0" + z))
        self.refused("a.png", png_insert(self.png, b"iCCP", PLANT[:30] + b"\0\0" + zlib.compress(icc_blob(b"x"))))      # A4: the name
        self.refused("a.png", png_insert(self.png, b"iCCP", b"p\0\0" + zlib.compress(icc_blob(b"A2B0" + LONG_TEXT, 1300))))   # A2

    def test_A3_unknown_ancillary_and_private_chunks(self):
        for t in (b"zzZz", b"prVt", b"tEXt", b"eXIf", b"tIME", b"pHYs", b"gAMA", b"sRGB", b"cHRM", b"bKGD", b"sBIT", b"hIST"):
            self.refused("a.png", png_insert(self.png, t, b"x" + PLANT))
            self.refused("a.png", png_insert(self.png, t, b"x" + PLANT, "after_idat"))

    def test_B1_text_chunks_whose_keyword_collides_with_an_allowlisted_name(self):
        for kw in (b"Gamma", b"ProfileName", b"ImageWidth", b"Interlace", b"Comment"):
            self.refused("a.png", png_insert(self.png, b"tEXt", kw + b"\0" + PLANT))
            self.refused("a.png", png_insert(self.png, b"iTXt", kw + b"\0\0\0\0\0" + PLANT))
            self.refused("a.png", png_insert(self.png, b"zTXt", kw + b"\0\0" + zlib.compress(PLANT)))

    def test_B3_trailer_after_iend_and_bad_chunks(self):
        self.refused("a.png", self.png + PLANT, "after IEND")
        self.refused("a.png", self.png + b"\0")
        bad_crc = bytearray(self.png)
        bad_crc[30] ^= 1
        self.refused("a.png", bytes(bad_crc))
        ch = png_split(self.png)
        idat = [b for t, b in ch if t == b"IDAT"]
        self.refused("a.png", png_join([c for c in ch if c[0] != b"IDAT"]))                                   # no pixels
        self.refused("a.png", png_join([c if c[0] != b"IDAT" else (b"IDAT", b"".join(idat) + PLANT) for c in ch]), "zlib")   # slack in the stream
        self.refused("a.png", png_join(ch[:1] + [(b"IDAT", b"")] + ch[1:]))
        self.refused("a.png", png_join(ch[:-1] + [(b"IEND", b"x")]))

    def test_ihdr_fields_must_be_8bit_rgb_non_interlaced(self):
        ch = png_split(self.png)
        w, h = struct.unpack(">II", ch[0][1][:8])
        for bad in (struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 1), struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0),
                    struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0), struct.pack(">IIBBBBB", w, h, 8, 2, 1, 0, 0),
                    struct.pack(">IIBBBBB", w + 1, h, 8, 2, 0, 0, 0), struct.pack(">IIBBBBB", 0, h, 8, 2, 0, 0, 0)):
            self.refused("a.png", png_join([(b"IHDR", bad)] + ch[1:]))

    def test_other_png_encodings_are_refused(self):
        tmp = _tmpdir("mw-penc-")
        im = noise_image(20, 20)
        for label, img, kw in (("rgba", im.convert("RGBA"), {}), ("gray", im.convert("L"), {}), ("pal", im.convert("P"), {}),
                               ("dpi", im, {"dpi": (300, 300)}), ("16bit", im.convert("I;16"), {})):
            f = tmp / (label + ".png")
            try:
                img.save(f, **kw)
            except Exception:
                continue
            self.refused(f.name, f.read_bytes())


@unittest.skipUnless(HAVE_X264, "no ffmpeg / libx264")
class Mp4BypassTests(Fixtures):
    def mut(self, fn, shift_offsets=True):
        return mp4_mutate(self.mp4, fn, shift_offsets)

    def test_identity_rewrite_is_clean(self):
        self.assertEqual(problems("a.mp4", self.mut(lambda r: None)), [])

    def test_helper_output_shape(self):
        self.assertEqual([b.t for b in parse_boxes(self.mp4)], [b"ftyp", b"moov", b"free", b"mdat"])
        self.assertNotIn(PLANT, self.mp4)
        self.assertNotIn(b"2020", self.mp4)
        self.assertNotIn(b"x264 - core", self.mp4)
        self.assertNotIn(b"Lavf", self.mp4)
        probe = subprocess.run([FFMPEG.replace("ffmpeg", "ffprobe"), "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt",
                                "-of", "csv=p=0", str(self.tmp / "clean.mp4")], capture_output=True, text=True).stdout
        self.assertEqual(sorted(probe.strip().split(",")), ["h264", "video", "yuv420p"])    # one video stream; audio was dropped

    def test_A5_colr_prof_carrying_an_icc_with_text(self):
        def f(root):
            colr = avc1_of(root).find(b"colr")
            colr.body = b"prof" + icc_blob(b"desc" + PLANT, 1000)
        self.refused("a.mp4", self.mut(f), "colr")

    def test_A6_non_sei_nal_units(self):
        for header, label in ((0x6C, "filler 12"), (0x78, "unspecified 24"), (0x66, "SEI 6"), (0x69, "AUD 9"), (0x67, "SPS 7")):
            data = self.mut(lambda r, h=header: append_nal(r, h, b"\x05" + PLANT))
            self.refused("a.mp4", data)

    @unittest.skipUnless(HAVE_X265, "no libx265")
    def test_A6_hevc_streams_are_refused(self):
        tmp = _tmpdir("mw-hevc-")
        f = tmp / "h.mp4"
        subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=128x96:rate=10:duration=1",
                        "-c:v", "libx265", "-tag:v", "hvc1", "-x265-params", "log-level=none", "-movflags", "+faststart", str(f)], check=True, capture_output=True)
        self.refused("h.mp4", f.read_bytes())

    def test_B4_tables_and_slack(self):
        def sdtp(root):
            stbl_of(root).kids.insert(1, Box(b"sdtp", body=b"\0\0\0\0" + LONG_TEXT))
        self.refused("a.mp4", self.mut(sdtp), "stbl")

        def stss(root):
            stbl_of(root).find(b"stss").body = b"\0\0\0\0" + struct.pack(">I", len(LONG_TEXT) // 4) + LONG_TEXT[:len(LONG_TEXT) // 4 * 4]
        self.refused("a.mp4", self.mut(stss))

        def avcc(root):
            avc1_of(root).find(b"avcC").body += PLANT
        self.refused("a.mp4", self.mut(avcc))

        def colr(root):
            avc1_of(root).find(b"colr").body += PLANT
        self.refused("a.mp4", self.mut(colr))

        def stsd_extra(root):
            stbl_of(root).find(b"stsd").kids.append(Box(b"zzzz", body=PLANT))
        self.refused("a.mp4", self.mut(stsd_extra))

        def avc1_extra(root):
            avc1_of(root).kids.append(Box(b"zzzz", body=PLANT))
        self.refused("a.mp4", self.mut(avc1_extra))

        def mdat_text(root):
            root.find(b"mdat").body += PLANT
        self.refused("a.mp4", self.mut(mdat_text), "mdat")

        def mdat_front(root):
            root.find(b"mdat").body = PLANT + root.find(b"mdat").body
        self.refused("a.mp4", self.mut(mdat_front, shift_offsets=True))

    def test_B5_small_blobs_esds_sgpd_hvcc(self):
        for t, size, where in ((b"esds", 310, "avc1"), (b"sgpd", 282, "stbl"), (b"hvcC", 344, "avc1"), (b"hvcC", 509, "avc1")):
            def f(root, t=t, size=size, where=where):
                parent = avc1_of(root) if where == "avc1" else stbl_of(root)
                parent.kids.append(Box(t, body=(PLANT * 20)[:size]))
            self.refused("a.mp4", self.mut(f))

    def test_dates_names_vendor_and_strings_chosen_by_someone(self):
        def mvhd_date(root):
            b = root.find(b"moov").find(b"mvhd")
            b.body = b.body[:4] + struct.pack(">I", 3786825600) + b.body[8:]
        self.refused("a.mp4", self.mut(mvhd_date), "mvhd")

        def hdlr_name(root):
            h = root.find(b"moov").find(b"trak").find(b"mdia").find(b"hdlr")
            h.body = h.body.replace(b"VideoHandler", b"PLANTED-NAME")
        self.refused("a.mp4", self.mut(hdlr_name), "hdlr")

        def vendor(root):
            h = root.find(b"moov").find(b"trak").find(b"mdia").find(b"hdlr")
            h.body = h.body[:12] + b"ZZZZ" + h.body[16:]
        self.refused("a.mp4", self.mut(vendor))

        def compressor(root):
            a = avc1_of(root)
            a.head = a.head[:42] + bytes([12]) + b"PLANTED-NAME" + b"\0" * 19 + a.head[75:]
        self.refused("a.mp4", self.mut(compressor), "avc1")

        def tdate(root):
            t = root.find(b"moov").find(b"trak").find(b"tkhd")
            t.body = t.body[:4] + struct.pack(">I", 3786825600) + t.body[8:]
        self.refused("a.mp4", self.mut(tdate), "tkhd")

        def mdate(root):
            m = root.find(b"moov").find(b"trak").find(b"mdia").find(b"mdhd")
            m.body = m.body[:8] + struct.pack(">I", 3786825600) + m.body[12:]
        self.refused("a.mp4", self.mut(mdate), "mdhd")

    def test_extra_boxes_everywhere(self):
        def udta_nam(root):
            m = root.find(b"moov")
            m.kids = [k for k in m.kids if k.t != b"udta"] + [Box(b"udta", body=struct.pack(">I4s", 8 + len(PLANT) + 8, b"\xa9nam") + struct.pack(">I4s", 8 + len(PLANT), b"data") + PLANT)]
        self.refused("a.mp4", self.mut(udta_nam), "udta")

        def ilst(root):
            u = root.find(b"moov").find(b"udta")
            u.body = u.body.replace(b"ilst", b"ilst")[:-8] + struct.pack(">I4s", 8 + len(PLANT), b"ilst") + PLANT
        self.refused("a.mp4", self.mut(ilst), "udta")

        def uuid(root):
            root.find(b"moov").kids.append(Box(b"uuid", body=b"\x01" * 16 + PLANT))
        self.refused("a.mp4", self.mut(uuid), "moov")

        def top_unknown(root):
            root.kids.insert(2, Box(b"zzzz", body=PLANT))
        self.refused("a.mp4", self.mut(top_unknown), "top-level")

        def free_text(root):
            root.find(b"free").body = PLANT
        self.refused("a.mp4", self.mut(free_text), "free")

        def edts(root):
            root.find(b"moov").find(b"trak").kids.insert(1, Box(b"edts", body=PLANT))
        self.refused("a.mp4", self.mut(edts), "trak")

        def meta_top(root):
            root.kids.insert(1, Box(b"meta", body=b"\0\0\0\0" + PLANT))
        self.refused("a.mp4", self.mut(meta_top), "top-level")

        def second_trak(root):
            m = root.find(b"moov")
            m.kids.insert(2, m.find(b"trak"))
        self.refused("a.mp4", self.mut(second_trak), "moov")

        def ftyp(root):
            root.find(b"ftyp").body = root.find(b"ftyp").body.replace(b"iso2", b"zzzz")
        self.refused("a.mp4", self.mut(ftyp), "ftyp")

    def test_sample_table_fields_cannot_leave_slack(self):
        def stsz_short(root):
            s = stbl_of(root).find(b"stsz")
            s.body = s.body[:-4] + struct.pack(">I", struct.unpack(">I", s.body[-4:])[0] - 1)
        self.refused("a.mp4", self.mut(stsz_short))

        def stco_off(root):
            c = stbl_of(root).find(b"stco")
            c.body = c.body[:-4] + struct.pack(">I", struct.unpack(">I", c.body[-4:])[0] + 3)
        self.refused("a.mp4", self.mut(stco_off, shift_offsets=False))

        def stts_huge(root):
            s = stbl_of(root).find(b"stts")
            s.body = s.body[:-4] + struct.pack(">I", 0x61626364)
        self.refused("a.mp4", self.mut(stts_huge))

        def btrt_text(root):
            b = avc1_of(root).find(b"btrt")
            b.body = PLANT[:12]
        self.refused("a.mp4", self.mut(btrt_text))

    def test_mp4_truncated_or_with_trailer(self):
        self.refused("a.mp4", self.mp4 + PLANT)
        self.refused("a.mp4", self.mp4[:-5])
        self.refused("a.mp4", self.mp4[:100])


# ---------------------------------------------------------------- the helper, end to end

@unittest.skipUnless(HAVE_PIL, "Pillow required")
class HelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir("mw-helper-")

    def strip_ok(self, src, name):
        out = self.tmp / name
        res = run_strip(src, out)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = out.read_bytes()
        self.assertEqual(problems(name, data), [], name)
        return data

    def test_jpeg_with_exif_gps_icc_comment_and_orientation(self):
        im = noise_image(48, 32)
        ex = Image.Exif()
        ex[0x010F] = PLANT.decode()
        ex[0x0131] = "Software-" + PLANT.decode()
        ex[0x0112] = 6
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        src = self.tmp / "-photo.JPG"
        im.save(src, exif=ex, icc_profile=icc, comment=PLANT)
        before = src.read_bytes()
        self.assertTrue(problems("photo.jpg", before))
        out = self.tmp / "clean.jpg"
        res = subprocess.run([str(STRIP), "-photo.JPG", "clean.jpg"], cwd=str(self.tmp), capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = out.read_bytes()
        self.assertEqual(problems("clean.jpg", data), [])
        self.assertEqual(src.read_bytes(), before)                                 # original untouched
        for leaked in (PLANT, b"Exif", b"ICC_PROFILE", b"Software", b"XMP"):
            self.assertNotIn(leaked, data)
        self.assertEqual(Image.open(out).size, (32, 48))                           # orientation 6 baked into the pixels
        self.assertEqual(Image.open(out).getexif(), {})

    def test_display_p3_png_is_converted_to_srgb_and_loses_the_profile(self):
        p3 = ICC_DIR / "Display P3.icc"
        if not p3.is_file():
            self.skipTest("no Display P3 profile")
        src = self.tmp / "p3.png"
        im = Image.new("RGB", (16, 16), (0, 255, 0))
        im.save(src, icc_profile=p3.read_bytes(), pnginfo=self._text_info())
        data = self.strip_ok(src, "p3-clean.png")
        for t in (b"iCCP", b"tEXt", b"eXIf", b"pHYs", b"sRGB", b"gAMA", PLANT):
            self.assertNotIn(t, data)
        px = Image.open(self.tmp / "p3-clean.png").getpixel((3, 3))
        self.assertGreaterEqual(px[1], 250)                                        # P3 green clips to sRGB green
        self.assertLessEqual(px[0], 5)

    @staticmethod
    def _text_info():
        from PIL import PngImagePlugin
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", PLANT.decode())
        info.add_text("Gamma", PLANT.decode())
        return info

    def test_png_with_text_chunks_and_alpha(self):
        src = self.tmp / "t.png"
        Image.new("RGBA", (16, 16), (255, 0, 0, 0)).save(src, pnginfo=self._text_info(), dpi=(300, 300))
        self.assertTrue(problems("t.png", src.read_bytes()))
        data = self.strip_ok(src, "t-clean.png")
        self.assertNotIn(PLANT, data)
        self.assertEqual(Image.open(self.tmp / "t-clean.png").getpixel((1, 1)), (255, 255, 255))   # transparency flattened on white

    def test_bmp_becomes_png_and_tiff_webp_become_jpeg(self):
        for ext, fmt in ((".bmp", "png"), (".tif", "jpg"), (".tiff", "jpg"), (".webp", "jpg")):
            src = self.tmp / ("s" + ext)
            noise_image(24, 20).save(src)
            self.strip_ok(src, "o-" + ext[1:] + "." + fmt)
        res = run_strip(self.tmp / "s.bmp", self.tmp / "wrong.jpg")
        self.assertEqual(res.returncode, 2)
        res = run_strip(self.tmp / "s.tif", self.tmp / "wrong.png")
        self.assertEqual(res.returncode, 2)

    def test_gif_and_unknown_types_are_refused(self):
        src = self.tmp / "a.gif"
        Image.new("P", (8, 8)).save(src)
        for name in ("o.png", "o.gif", "o.jpg"):
            res = run_strip(src, self.tmp / name)
            self.assertEqual(res.returncode, 2, name)
            self.assertFalse((self.tmp / name).exists())
        (self.tmp / "x.xyz").write_bytes(b"data")
        self.assertEqual(run_strip(self.tmp / "x.xyz", self.tmp / "o.jpg").returncode, 2)

    def test_never_overwrites_and_usage_errors(self):
        src = self.tmp / "a.png"
        noise_image(8, 8).save(src)
        taken = self.tmp / "taken.png"
        taken.write_bytes(b"keep me")
        res = run_strip(src, taken)
        self.assertEqual((res.returncode, taken.read_bytes()), (2, b"keep me"))
        self.assertEqual(run_strip(src, src).returncode, 2)
        self.assertEqual(subprocess.run([str(STRIP), "only-one"], capture_output=True).returncode, 2)
        self.assertEqual(run_strip(self.tmp / "missing.png", self.tmp / "o.png").returncode, 2)

    def test_undecodable_input_writes_nothing(self):
        bad = self.tmp / "bad.jpg"
        bad.write_bytes(b"not a jpeg")
        out = self.tmp / "o.jpg"
        res = run_strip(bad, out)
        self.assertEqual(res.returncode, 1)
        self.assertFalse(out.exists())

    def test_a_large_noisy_photo_is_walked_in_reasonable_time(self):
        import time
        src = self.tmp / "big.tif"
        noise_image(2400, 1800, 3).save(src)
        out = self.tmp / "big.jpg"
        self.assertEqual(run_strip(src, out).returncode, 0)
        data = out.read_bytes()
        t0 = time.monotonic()
        self.assertEqual(problems("big.jpg", data), [])
        self.assertLess(time.monotonic() - t0, 20)

    @unittest.skipUnless(shutil.which("sips"), "sips (macOS) required")
    def test_heic_is_decoded_by_sips_and_comes_out_as_jpeg(self):
        jpg = self.tmp / "o.jpg"
        im = noise_image(64, 48)
        ex = Image.Exif()
        ex[0x0112] = 6
        ex[0x010F] = PLANT.decode()
        im.save(jpg, exif=ex)
        heic = self.tmp / "p.heic"
        res = subprocess.run(["sips", "-s", "format", "heic", str(jpg), "--out", str(heic)], capture_output=True)
        if res.returncode or not heic.is_file():
            self.skipTest("sips cannot write a HEIC here")
        self.assertTrue(problems("p.heic", heic.read_bytes()))                     # a HEIC is never admitted as is
        data = self.strip_ok(heic, "p-clean.jpg")
        self.assertNotIn(PLANT, data)
        self.assertEqual(run_strip(heic, self.tmp / "p-clean.png").returncode, 2)  # a HEIC is written as JPEG only

    @unittest.skipUnless(REAL_HEIC.is_file() and shutil.which("sips"), "no real iPhone HEIC on this machine")
    def test_real_iphone_photo_passes_only_after_the_helper(self):
        self.assertTrue(problems("IMG.jpg", REAL_HEIC.read_bytes()))
        out = self.tmp / "iphone.jpg"
        res = run_strip(REAL_HEIC, out)
        self.assertEqual(res.returncode, 0, res.stderr)
        data = out.read_bytes()
        self.assertEqual(problems("iphone.jpg", data), [])
        for leaked in (b"Apple", b"iPhone", b"Exif", b"ICC_PROFILE", b"XMP"):
            self.assertNotIn(leaked, data)

    # ---- video
    def clip(self, name, *extra):
        f = self.tmp / name
        res = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=128x96:rate=10",
                              "-f", "lavfi", "-i", "sine=duration=2", "-shortest",
                              "-metadata", "title=" + PLANT.decode(), "-metadata", "comment=" + PLANT.decode(),
                              "-metadata", "creation_time=2020-01-02T03:04:05Z", "-metadata", "location=+40.0000-105.0000/",
                              "-metadata:s:v:0", "handler_name=" + PLANT.decode(), "-metadata:s:a:0", "handler_name=" + PLANT.decode(),
                              *extra, str(f)], capture_output=True)
        if res.returncode:
            self.skipTest("ffmpeg cannot make %s: %s" % (name, res.stderr.decode()[:100]))
        return f

    @unittest.skipUnless(HAVE_X264, "no ffmpeg / libx264")
    def test_planted_video_is_refused_before_and_admitted_after_a_full_reencode(self):
        src = self.clip("v.mp4", "-c:v", "libx264", "-c:a", "aac")
        before = src.read_bytes()
        self.assertTrue(problems("v.mp4", before))
        out = self.tmp / "v-clean.mp4"
        res = run_strip(src, out)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("re-encoded", res.stderr)
        data = out.read_bytes()
        self.assertEqual(problems("v-clean.mp4", data), [])
        self.assertEqual(src.read_bytes(), before)
        for leaked in (PLANT, b"2020", b"40.0000", b"Lavf", b"x264 - core", b"mdta", b"keys", b"smhd", b"mp4a"):
            self.assertNotIn(leaked, data)
        res = run_strip(src, self.tmp / "named.mov")
        self.assertEqual(res.returncode, 2)
        taken = self.tmp / "taken.mp4"
        taken.write_bytes(b"keep")
        self.assertEqual((run_strip(src, taken).returncode, taken.read_bytes()), (2, b"keep"))

    @unittest.skipUnless(HAVE_X264, "no ffmpeg / libx264")
    def test_odd_sizes_frame_rates_and_a_mov_input(self):
        src = self.tmp / "odd.mov"
        res = subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=321x241:rate=30000/1001:duration=1",
                              "-c:v", "libx264", "-bf", "2", "-g", "7", str(src)], capture_output=True)
        if res.returncode:
            self.skipTest("ffmpeg cannot make a .mov here")
        self.strip_ok(src, "odd.mp4")

    @unittest.skipUnless(HAVE_X264 and HAVE_X265, "no libx264 / libx265")
    def test_hevc_ten_bit_hdr_input_is_reencoded_to_sdr_h264(self):
        src = self.clip("h.mp4", "-pix_fmt", "yuv420p10le", "-c:v", "libx265", "-tag:v", "hvc1", "-x265-params", "log-level=none",
                        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-c:a", "aac")
        self.strip_ok(src, "h-clean.mp4")

    @unittest.skipUnless(REAL_MOV.is_file() and HAVE_X264, "no real iPhone MOV on this machine")
    def test_real_iphone_video_passes_only_after_the_helper(self):
        self.assertTrue(problems("IMG.mp4", REAL_MOV.read_bytes()))
        data = self.strip_ok(REAL_MOV, "iphone.mp4")
        for leaked in (b"Apple", b"iPhone", b"com.apple", b"mdta", b"hvc1", b"mp4a"):
            self.assertNotIn(leaked, data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
