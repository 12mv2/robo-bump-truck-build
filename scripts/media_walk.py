#!/usr/bin/env python3
"""media_walk.py - exact-structure admission of images and video for the leak gate (B50c, pixels-only).

The gate DECODES NOTHING FOR MEANING. scripts/strip-image-metadata.sh emits exactly one known structure per format;
this module admits a file only if it is byte-for-byte that structure, and refuses everything else. The variable parts
are only pixel or sample data and size / count / timing fields, each of them bounded and cross-checked.

  JPEG  SOI, JFIF APP0 (fixed), DQT x2 (the quality-90 tables), SOF0 (4:2:0, 3 components), DHT x4 (the standard
        tables), SOS (fixed), entropy-coded data that Huffman-decodes to exactly the MCU count of the frame with
        only 1-bit padding left, EOI, end of file. No other marker, no ICC, no EXIF, no COM, no trailer.
  PNG   signature, IHDR (8-bit RGB, no interlace), one or more IDAT, IEND, end of file. Every CRC is checked and the
        IDAT zlib stream must inflate to exactly height x (1 + 3 x width) bytes with nothing left over.
  MP4   ftyp, moov (mvhd, one trak with a fixed box tree incl. colr nclx BT.709 limited range, optional empty udta/meta), optional empty free, mdat, end of
        file. H.264 High 8-bit 4:2:0, video only. mdat is covered exactly by the chunks the sample tables describe and
        every sample is a run of length-prefixed NAL units of type 1 (slice) or 5 (IDR slice) and nothing else
        (no SEI, no filler, no AUD, no unspecified type). SPS and PPS are parsed to their trailing bits.

Anything else - GIF, TIFF, WebP, HEIC, AVIF, BMP, JXL, MOV, audio tracks, HEVC, ICC profiles, a second chunk of any
kind - is refused. media_problems() returns [] or one message naming the structural reason (a chunk / marker / box
code or a fixed word, never file content)."""
import re
import struct
import zlib

QUALITY = 90          # the JPEG quality scripts/strip_media.py writes; the DQT tables below belong to it
MAX_PIXELS = 1 << 26  # 67 Mpx: bound on what the PNG / JPEG checks will allocate or walk
MAX_SAMPLES = 1 << 22


class Refused(Exception):
    pass


def _fail(msg):
    raise Refused(msg)


def _tag(raw):
    """A chunk / marker / box code made safe to print."""
    return re.sub(r"[^A-Za-z0-9 _.-]", "?", raw.decode("latin-1"))[:8]


# ---------------------------------------------------------------- PNG

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def walk_png(d):
    if d[:8] != PNG_SIG:
        _fail("PNG signature")
    pos, n = 8, len(d)

    def chunk():
        nonlocal pos
        if pos + 12 > n:
            _fail("PNG truncated")
        ln, typ = struct.unpack(">I4s", d[pos:pos + 8])
        if ln > 0x7fffffff or pos + 12 + ln > n:
            _fail("PNG chunk length")
        body = d[pos + 8:pos + 8 + ln]
        crc = struct.unpack(">I", d[pos + 8 + ln:pos + 12 + ln])[0]
        if zlib.crc32(typ + body) & 0xffffffff != crc:
            _fail("PNG chunk CRC (%s)" % _tag(typ))
        pos += 12 + ln
        return typ, body

    typ, body = chunk()
    if typ != b"IHDR" or len(body) != 13:
        _fail("PNG first chunk is not IHDR")
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", body)
    if (depth, ctype, comp, filt, inter) != (8, 2, 0, 0, 0):
        _fail("PNG header is not 8-bit RGB, non-interlaced")
    if not (0 < w and 0 < h and w * h <= MAX_PIXELS):
        _fail("PNG dimensions")
    idat = []
    while True:
        typ, body = chunk()
        if typ == b"IDAT":
            if not body:
                _fail("PNG empty IDAT")
            idat.append(body)
        elif typ == b"IEND":
            if body:
                _fail("PNG IEND is not empty")
            break
        else:
            _fail("PNG chunk %s is not one the helper writes" % _tag(typ))
    if not idat:
        _fail("PNG has no IDAT")
    if pos != n:
        _fail("PNG bytes after IEND")
    stride = 1 + 3 * w
    want = stride * h
    z = zlib.decompressobj()
    try:
        out = z.decompress(b"".join(idat), want + 1)
    except zlib.error:
        _fail("PNG zlib stream")
    if len(out) != want or not z.eof or z.unused_data or z.unconsumed_tail:
        _fail("PNG zlib stream is not exactly the pixel rows")
    if max(out[::stride]) > 4:
        _fail("PNG filter byte")


# ---------------------------------------------------------------- JPEG

# DQT payloads (Pq/Tq byte + 64 values, zigzag) of Pillow / libjpeg quality 90, the standard DHT payloads
# (Annex K tables, not optimised), and the fixed JFIF / SOF0 / SOS payloads. Measured from the helper's own output.
_DQT0 = bytes.fromhex("000302020302020303030304030304050805050404050a070706080c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113141515150c0f171816141812141514")
_DQT1 = bytes.fromhex("0103040405040509050509140d0b0d1414141414141414141414141414141414141414141414141414141414141414141414141414141414141414141414141414")
_DHT = (
    bytes.fromhex("0000010501010101010100000000000000000102030405060708090a0b"),
    bytes.fromhex(
    "100002010303020403050504040000017d01020300041105122131410613516107227114328191a1"
    "082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748"
    "494a535455565758595a636465666768696a737475767778797a838485868788898a929394959697"
    "98999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1"
    "e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"),
    bytes.fromhex("0100030101010101010101010000000000000102030405060708090a0b"),
    bytes.fromhex(
    "11000201020404030407050404000102770001020311040521310612415107617113223281081442"
    "91a1b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a4344454647"
    "48494a535455565758595a636465666768696a737475767778797a82838485868788898a92939495"
    "969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9"
    "dae2e3e4e5e6e7e8e9eaf2f3f4f5f6f7f8f9fa"),
)
_JFIF = bytes.fromhex("4a46494600010100000100010000")
_SOF_TAIL = bytes.fromhex("03012200021101031101")      # 3 components: Y 2x2 (Tq0), Cb 1x1 (Tq1), Cr 1x1 (Tq1)
_SOS = bytes.fromhex("03010002110311003f00")           # Y: DC0/AC0, Cb and Cr: DC1/AC1, Ss 0, Se 63, Ah/Al 0


def _huff_lookup(payload):
    """16-bit-peek table: entry = (code length << 8) | symbol, or 0 for an invalid prefix. payload = Tc/Th + counts + symbols."""
    counts = payload[1:17]
    syms = payload[17:]
    table = [0] * 65536
    code = k = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            lo = code << (16 - length)
            for v in range(lo, lo + (1 << (16 - length))):
                table[v] = (length << 8) | syms[k]
            code += 1
            k += 1
        code <<= 1
    return table


_HUFF = None


def _huff():
    global _HUFF
    if _HUFF is None:
        _HUFF = [_huff_lookup(p) for p in _DHT]      # DC0, AC0, DC1, AC1
    return _HUFF


def _jpeg_scan_ok(scan, w, h):
    """Huffman-decode the entropy-coded segment (sizes only, no values) and require that it is exactly
    ceil(w/16) x ceil(h/16) MCUs of 6 blocks, with only 1-bit padding after the last one."""
    if scan.count(b"\xff") != scan.count(b"\xff\x00"):
        _fail("JPEG marker or stray 0xFF inside the scan")
    buf = scan.replace(b"\xff\x00", b"\xff") + b"\0\0\0"
    nbits = (len(buf) - 3) * 8
    dc0, ac0, dc1, ac1 = _huff()
    p = 0
    for _ in range(-(-w // 16) * -(-h // 16)):
        for blk in range(6):
            dc, ac = (dc0, ac0) if blk < 4 else (dc1, ac1)
            i = p >> 3
            e = dc[(((buf[i] << 16) | (buf[i + 1] << 8) | buf[i + 2]) >> (8 - (p & 7))) & 0xFFFF] if i < len(buf) - 3 else 0
            if not e:
                _fail("JPEG scan does not decode (DC)")
            p += (e >> 8) + (e & 15)
            k = 1
            while k < 64:
                i = p >> 3
                if i >= len(buf) - 3:
                    _fail("JPEG scan ends early")
                e = ac[(((buf[i] << 16) | (buf[i + 1] << 8) | buf[i + 2]) >> (8 - (p & 7))) & 0xFFFF]
                if not e:
                    _fail("JPEG scan does not decode (AC)")
                rs = e & 0xFF
                p += e >> 8
                s = rs & 15
                if s:
                    p += s
                    k += (rs >> 4) + 1
                elif rs == 0xF0:
                    k += 16
                elif rs == 0:
                    break
                else:
                    _fail("JPEG scan symbol")
            if k > 64:
                _fail("JPEG block runs past 64 coefficients")
        if p > nbits:
            _fail("JPEG scan ends early")
    if (p + 7) >> 3 != len(buf) - 3:
        _fail("JPEG scan has bytes after the last MCU")
    if p & 7 and (buf[p >> 3] & ((1 << (8 - (p & 7))) - 1)) != (1 << (8 - (p & 7))) - 1:
        _fail("JPEG scan padding is not 1-bits")


_SEGNAME = {0xE0: "JFIF APP0", 0xDB: "DQT"}


def _seg_found(d, pos):
    return "marker FF%02X" % d[pos + 1] if d[pos:pos + 1] == b"\xff" and len(d) > pos + 1 else "no marker"


def walk_jpeg(d):
    n = len(d)
    if d[:2] != b"\xff\xd8":
        _fail("JPEG SOI")
    if d[-2:] != b"\xff\xd9":
        _fail("JPEG does not end with EOI (trailer or truncation)")
    pos = 2
    want = [(0xE0, _JFIF), (0xDB, _DQT0), (0xDB, _DQT1)]
    dims = None
    for marker, payload in want:
        if d[pos:pos + 2] != bytes([0xFF, marker]) or d[pos + 2:pos + 4] != struct.pack(">H", len(payload) + 2) \
                or d[pos + 4:pos + 4 + len(payload)] != payload:
            _fail("JPEG: expected the helper's %s segment, found %s" % (_SEGNAME[marker], _seg_found(d, pos)))
        pos += 4 + len(payload)
    # SOF0
    if d[pos:pos + 4] != b"\xff\xc0\x00\x11" or d[pos + 4] != 8 or d[pos + 9:pos + 19] != _SOF_TAIL:
        _fail("JPEG SOF0 is not 8-bit 4:2:0 three-component")
    hh, ww = struct.unpack(">HH", d[pos + 5:pos + 9])
    if not (ww and hh and ww * hh <= MAX_PIXELS):
        _fail("JPEG dimensions")
    pos += 19
    for payload in _DHT:
        if d[pos:pos + 2] != b"\xff\xc4" or d[pos + 2:pos + 4] != struct.pack(">H", len(payload) + 2) \
                or d[pos + 4:pos + 4 + len(payload)] != payload:
            _fail("JPEG DHT is not the standard table")
        pos += 4 + len(payload)
    if d[pos:pos + 4] != b"\xff\xda\x00\x0c" or d[pos + 4:pos + 14] != _SOS:
        _fail("JPEG SOS is not the fixed scan header")
    pos += 14
    _jpeg_scan_ok(d[pos:n - 2], ww, hh)


# ---------------------------------------------------------------- MP4 / H.264

def _u32(b, o=0):
    return struct.unpack_from(">I", b, o)[0]


def _box(t, payload):
    return struct.pack(">I4s", 8 + len(payload), t) + payload


def _children(d, s, e):
    """[(type, payload_start, end)] of the boxes filling d[s:e] exactly (32-bit sizes only)."""
    out = []
    while s < e:
        if e - s < 8:
            _fail("MP4 box header truncated")
        size, t = struct.unpack_from(">I4s", d, s)
        if size < 8 or s + size > e:
            _fail("MP4 box %s size" % _tag(t))
        out.append((t, s + 8, s + size))
        s += size
    return out


_IDENT = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
_FTYP = _box(b"ftyp", b"mp42" + struct.pack(">I", 0x200) + b"mp42iso2avc1mp41")
_UDTA = _box(b"udta", _box(b"meta", b"\0\0\0\0" + _box(b"hdlr", b"\0" * 8 + b"mdir" + b"appl" + b"\0" * 9) + _box(b"ilst", b"")))
_HDLR = _box(b"hdlr", b"\0" * 8 + b"vide" + b"\0" * 12 + b"VideoHandler\0")
_VMHD = _box(b"vmhd", b"\0\0\0\1" + b"\0" * 8)
_DINF = _box(b"dinf", _box(b"dref", b"\0" * 4 + struct.pack(">I", 1) + _box(b"url ", b"\0\0\0\1")))
_FREE = _box(b"free", b"")
_PASP = _box(b"pasp", struct.pack(">II", 1, 1))
_COLR = _box(b"colr", b"nclx" + struct.pack(">HHHB", 1, 1, 1, 0))     # BT.709 primaries, transfer and matrix, limited range
_COMPNAMES = (b"\0" * 32, bytes([12]) + b"Lavc libx264" + b"\0" * 19)
_MAX_NUM = 1 << 28      # bound on every free numeric field (bitrates, timescales, durations, deltas)


def _bits_to_rbsp(b):
    out = bytearray()
    zeros = 0
    for byte in b:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


class _Bits:
    def __init__(self, b):
        self.b, self.p, self.n = b, 0, len(b) * 8

    def u(self, k):
        if self.p + k > self.n:
            _fail("H.264 parameter set truncated")
        v = 0
        for _ in range(k):
            v = (v << 1) | ((self.b[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def ue(self):
        z = 0
        while self.u(1) == 0:
            z += 1
            if z > 31:
                _fail("H.264 exp-Golomb")
        return (1 << z) - 1 + (self.u(z) if z else 0)

    def se(self):
        v = self.ue()
        return (v + 1) >> 1 if v & 1 else -(v >> 1)

    def more_data(self):
        last = self.b[-1] if self.b else 0
        if not last:
            _fail("H.264 parameter set ends in a zero byte")
        stop = (len(self.b) - 1) * 8 + 7 - ((last & -last).bit_length() - 1)     # position of the rbsp stop bit
        return self.p < stop

    def end(self):
        if self.u(1) != 1:
            _fail("H.264 parameter set trailing bit")
        rest = self.n - self.p
        if rest >= 8 or (rest and self.u(rest)):
            _fail("H.264 parameter set has bytes after its trailing bits")


def _parse_sps(nal):
    """Returns (width, height). Refuses anything but 8-bit 4:2:0 High, frame-only, no scaling matrices, no HRD."""
    if nal[0] != 0x67:
        _fail("H.264 SPS header byte")
    b = _Bits(_bits_to_rbsp(nal[1:]))
    if b.u(8) != 100:
        _fail("H.264 profile is not High")
    b.u(8)
    b.u(8)
    if b.ue() != 0:
        _fail("H.264 SPS id")
    if b.ue() != 1 or b.ue() != 0 or b.ue() != 0 or b.u(1) or b.u(1):
        _fail("H.264 SPS is not 8-bit 4:2:0 without scaling lists")
    if b.ue() > 12:
        _fail("H.264 log2_max_frame_num")
    poc = b.ue()
    if poc == 0:
        if b.ue() > 12:
            _fail("H.264 log2_max_poc_lsb")
    elif poc != 2:
        _fail("H.264 pic_order_cnt_type")
    if b.ue() > 16 or b.u(1):
        _fail("H.264 reference frames / gaps")
    wmb, hmb = b.ue() + 1, b.ue() + 1
    if not b.u(1):
        _fail("H.264 is not frame-only")
    b.u(1)
    crop = [0, 0, 0, 0]
    if b.u(1):
        crop = [b.ue() for _ in range(4)]
    width, height = wmb * 16 - 2 * (crop[0] + crop[1]), hmb * 16 - 2 * (crop[2] + crop[3])
    if not (0 < width and 0 < height and width * height <= MAX_PIXELS):
        _fail("H.264 dimensions")
    if b.u(1):                                              # VUI
        if b.u(1):
            if b.u(8) == 255:
                b.u(32)
        if b.u(1):
            b.u(1)
        if b.u(1):
            b.u(3)
            b.u(1)
            if b.u(1):
                b.u(24)
        if b.u(1):
            b.ue()
            b.ue()
        if b.u(1):
            if b.u(32) >= _MAX_NUM or b.u(32) >= _MAX_NUM:
                _fail("H.264 timing field")
            b.u(1)
        if b.u(1) or b.u(1) or b.u(1):
            _fail("H.264 HRD / pic_struct")
        if b.u(1):
            b.u(1)
            for _ in range(6):
                if b.ue() > 65535:
                    _fail("H.264 bitstream restriction")
    b.end()
    return width, height


def _parse_pps(nal):
    if nal[0] != 0x68:
        _fail("H.264 PPS header byte")
    b = _Bits(_bits_to_rbsp(nal[1:]))
    if b.ue() > 255 or b.ue() != 0:
        _fail("H.264 PPS ids")
    b.u(1)
    b.u(1)
    if b.ue() != 0:
        _fail("H.264 slice groups")
    if b.ue() > 31 or b.ue() > 31:
        _fail("H.264 ref idx defaults")
    b.u(1)
    b.u(2)
    b.se()
    b.se()
    b.se()
    b.u(1)
    b.u(1)
    if b.u(1):
        _fail("H.264 redundant pictures")
    if b.more_data():
        b.u(1)                                              # transform_8x8_mode_flag
        if b.u(1):
            _fail("H.264 PPS scaling matrices")
        b.se()
    b.end()


def _check_num(v, lo=0):
    if not lo <= v < _MAX_NUM:
        _fail("MP4 numeric field out of range")
    return v


def walk_mp4(d):
    n = len(d)
    top = _children(d, 0, n)
    kinds = [t for t, _, _ in top]
    if kinds not in ([b"ftyp", b"moov", b"mdat"], [b"ftyp", b"moov", b"free", b"mdat"]):
        _fail("MP4 top-level boxes are %s" % "/".join(_tag(k) for k in kinds))
    if d[0:32] != _FTYP:
        _fail("MP4 ftyp")
    moov = top[1]
    mdat = top[-1]
    if kinds == [b"ftyp", b"moov", b"free", b"mdat"] and d[top[2][1] - 8:top[2][2]] != _FREE:
        _fail("MP4 free box is not empty")
    if _u32(d, mdat[1] - 8) != mdat[2] - mdat[1] + 8:
        _fail("MP4 mdat size")
    mdat_start, mdat_end = mdat[1], mdat[2]

    kids = _children(d, moov[1], moov[2])
    kk = [t for t, _, _ in kids]
    if kk not in ([b"mvhd", b"trak"], [b"mvhd", b"trak", b"udta"]):
        _fail("MP4 moov children are %s" % "/".join(_tag(k) for k in kk))
    if len(kids) == 3 and d[kids[2][1] - 8:kids[2][2]] != _UDTA:
        _fail("MP4 udta is not the empty muxer box")
    mv = kids[0]
    dur = _check_num(_u32(d, mv[1] + 16))
    want = _box(b"mvhd", b"\0" * 12 + struct.pack(">II", 1000, dur) + struct.pack(">IH", 0x10000, 0x100) + b"\0" * 10
                + _IDENT + b"\0" * 24 + struct.pack(">I", 2))
    if d[mv[1] - 8:mv[2]] != want:
        _fail("MP4 mvhd")

    trak = kids[1]
    tk = _children(d, trak[1], trak[2])
    if [t for t, _, _ in tk] != [b"tkhd", b"mdia"]:
        _fail("MP4 trak children")
    t0 = tk[0]
    tdur = _check_num(_u32(d, t0[1] + 20))
    tw, th = struct.unpack_from(">II", d, t0[1] + 76)
    want = _box(b"tkhd", struct.pack(">IIII", 3, 0, 0, 1) + struct.pack(">II", 0, tdur) + b"\0" * 8 + b"\0" * 8 + _IDENT
                + struct.pack(">II", tw, th))
    if d[t0[1] - 8:t0[2]] != want or tw & 0xffff or th & 0xffff:
        _fail("MP4 tkhd")
    mdia = _children(d, tk[1][1], tk[1][2])
    if [t for t, _, _ in mdia] != [b"mdhd", b"hdlr", b"minf"]:
        _fail("MP4 mdia children")
    md = mdia[0]
    ts, mdur = struct.unpack_from(">II", d, md[1] + 12)
    _check_num(ts, 1)
    _check_num(mdur)
    if d[md[1] - 8:md[2]] != _box(b"mdhd", b"\0" * 12 + struct.pack(">IIHH", ts, mdur, 0x55c4, 0)):
        _fail("MP4 mdhd")
    if d[mdia[1][1] - 8:mdia[1][2]] != _HDLR:
        _fail("MP4 hdlr (not a video track with the muxer's handler name)")
    minf = _children(d, mdia[2][1], mdia[2][2])
    if [t for t, _, _ in minf] != [b"vmhd", b"dinf", b"stbl"]:
        _fail("MP4 minf children")
    if d[minf[0][1] - 8:minf[0][2]] != _VMHD or d[minf[1][1] - 8:minf[1][2]] != _DINF:
        _fail("MP4 vmhd / dinf")
    stbl = _children(d, minf[2][1], minf[2][2])
    names = [t for t, _, _ in stbl]
    if names not in ([b"stsd", b"stts", b"stsc", b"stsz", b"stco"], [b"stsd", b"stts", b"stss", b"stsc", b"stsz", b"stco"]):
        _fail("MP4 stbl children are %s" % "/".join(_tag(k) for k in names))
    box = {t: (s, e) for t, s, e in stbl}

    # ---- stsd / avc1 / avcC
    s, e = box[b"stsd"]
    if d[s:s + 8] != b"\0\0\0\0\0\0\0\1":
        _fail("MP4 stsd entry count")
    entry = _children(d, s + 8, e)
    if len(entry) != 1 or entry[0][0] != b"avc1":
        _fail("MP4 sample entry is not a single avc1")
    a_s, a_e = entry[0][1], entry[0][2]
    aw, ah = struct.unpack_from(">HH", d, a_s + 24)
    head = d[a_s:a_s + 78]
    ok = False
    for comp in _COMPNAMES:
        if head == b"\0" * 6 + b"\0\1" + b"\0" * 16 + struct.pack(">HH", aw, ah) + struct.pack(">II", 0x480000, 0x480000) \
                + b"\0\0\0\0" + b"\0\1" + comp + struct.pack(">Hh", 0x18, -1):
            ok = True
    if not ok:
        _fail("MP4 avc1 header")
    ch = _children(d, a_s + 78, a_e)
    cn = [t for t, _, _ in ch]
    if cn != [b"avcC", b"colr", b"pasp", b"btrt"]:
        _fail("MP4 avc1 children are %s" % "/".join(_tag(k) for k in cn))
    if d[ch[1][1] - 8:ch[1][2]] != _COLR:
        _fail("MP4 colr is not BT.709 nclx")
    if d[ch[2][1] - 8:ch[2][2]] != _PASP:
        _fail("MP4 pasp")
    if ch[3][2] - ch[3][1] != 12:
        _fail("MP4 btrt size")
    for v in struct.unpack_from(">III", d, ch[3][1]):
        _check_num(v)
    c_s, c_e = ch[0][1], ch[0][2]
    if d[c_s:c_s + 6] != bytes([1]) + d[c_s + 1:c_s + 4] + b"\xff\xe1":
        _fail("MP4 avcC header")
    sl = struct.unpack_from(">H", d, c_s + 6)[0]
    sps = d[c_s + 8:c_s + 8 + sl]
    q = c_s + 8 + sl
    if len(sps) != sl or d[q] != 1:
        _fail("MP4 avcC SPS")
    pl = struct.unpack_from(">H", d, q + 1)[0]
    pps = d[q + 3:q + 3 + pl]
    q += 3 + pl
    if len(pps) != pl or d[q:c_e] != b"\xfd\xf8\xf8\x00":
        _fail("MP4 avcC PPS / High-profile tail")
    if sl < 4 or d[c_s + 1:c_s + 4] != sps[1:4]:
        _fail("MP4 avcC does not match its SPS")
    width, height = _parse_sps(sps)
    _parse_pps(pps)
    if (aw, ah, tw >> 16, th >> 16) != (width, height, width, height):
        _fail("MP4 frame size disagrees between avc1, tkhd and SPS")

    # ---- sample tables
    s, e = box[b"stsz"]
    if d[s:s + 4] != b"\0" * 4 or _u32(d, s + 4) != 0:
        _fail("MP4 stsz")
    count = _u32(d, s + 8)
    if not 0 < count <= MAX_SAMPLES or e - s != 12 + 4 * count:
        _fail("MP4 stsz count")
    sizes = struct.unpack_from(">%dI" % count, d, s + 12)
    if min(sizes) < 5 or max(sizes) >= _MAX_NUM:
        _fail("MP4 sample size")
    s, e = box[b"stts"]
    ne = _u32(d, s + 4)
    if d[s:s + 4] != b"\0" * 4 or ne < 1 or e - s != 8 + 8 * ne:
        _fail("MP4 stts")
    total = 0
    for i in range(ne):
        c, delta = struct.unpack_from(">II", d, s + 8 + 8 * i)
        if c < 1 or not 0 < delta < (1 << 20):
            _fail("MP4 stts entry")
        total += c
    if total != count:
        _fail("MP4 stts does not cover the samples")
    sync = None
    if b"stss" in box:
        s, e = box[b"stss"]
        ne = _u32(d, s + 4)
        if d[s:s + 4] != b"\0" * 4 or ne < 1 or e - s != 8 + 4 * ne:
            _fail("MP4 stss")
        sync = struct.unpack_from(">%dI" % ne, d, s + 8)
        if sync[0] != 1 or list(sync) != sorted(set(sync)) or sync[-1] > count:
            _fail("MP4 stss entries")
    s, e = box[b"stsc"]
    ne = _u32(d, s + 4)
    if d[s:s + 4] != b"\0" * 4 or ne < 1 or e - s != 8 + 12 * ne:
        _fail("MP4 stsc")
    stsc = [struct.unpack_from(">III", d, s + 8 + 12 * i) for i in range(ne)]
    s, e = box[b"stco"]
    nch = _u32(d, s + 4)
    if d[s:s + 4] != b"\0" * 4 or nch < 1 or nch > count or e - s != 8 + 4 * nch:
        _fail("MP4 stco")
    offsets = struct.unpack_from(">%dI" % nch, d, s + 8)
    if stsc[0][0] != 1 or any(x[2] != 1 or x[1] < 1 for x in stsc) or any(stsc[i][0] >= stsc[i + 1][0] for i in range(ne - 1)) \
            or stsc[-1][0] > nch:
        _fail("MP4 stsc entries")
    per_chunk, j = [], 0
    for c in range(1, nch + 1):
        while j + 1 < ne and stsc[j + 1][0] <= c:
            j += 1
        per_chunk.append(stsc[j][1])
    if sum(per_chunk) != count:
        _fail("MP4 chunks do not hold exactly the samples")

    # ---- mdat is exactly the chunks, each sample exactly its NAL units
    pos, idx = mdat_start, 0
    sync_set = set(sync) if sync is not None else None
    for c in range(nch):
        if offsets[c] != pos:
            _fail("MP4 chunk offset leaves slack in mdat")
        for _ in range(per_chunk[c]):
            sz = sizes[idx]
            end = pos + sz
            kind = None
            q = pos
            while q < end:
                if end - q < 5:
                    _fail("MP4 NAL length prefix")
                ln = _u32(d, q)
                hdr = d[q + 4]
                typ = hdr & 31
                if ln < 2 or q + 4 + ln > end or hdr & 0x80 or typ not in (1, 5) or (typ == 5 and not hdr & 0x60) \
                        or (kind is not None and kind != typ) or d[q + 3 + ln] == 0:
                    _fail("MP4 NAL unit (type %d) is not a slice of the helper's stream" % typ)
                kind = typ
                q += 4 + ln
            if q != end:
                _fail("MP4 sample is not exactly its NAL units")
            if (kind == 5) != (sync_set is None or idx + 1 in sync_set):
                _fail("MP4 sync samples disagree with IDR slices")
            pos = end
            idx += 1
    if pos != mdat_end:
        _fail("MP4 mdat has bytes the sample tables do not describe")


# ---------------------------------------------------------------- entry point

_EXT_KIND = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".mp4": "mp4", ".m4v": "mp4"}


def kind_of(data):
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == PNG_SIG:
        return "png"
    if data[4:8] == b"ftyp":
        return "mp4"
    return None


def media_problems(path, data):
    """[] if `data` is exactly what scripts/strip-image-metadata.sh emits for its type, else one message (no content)."""
    ext = ""
    low = str(path).lower()
    for e in (".jpeg", ".jpg", ".png", ".mp4", ".m4v"):
        if low.endswith(e):
            ext = e
    kind = kind_of(data)
    try:
        if kind is None:
            _fail("not a JPEG, PNG or MP4 the helper could have written (GIF, TIFF, WebP, HEIC, AVIF, BMP, JXL, MOV and unreadable files are refused)")
        if _EXT_KIND.get(ext) != kind:
            _fail("file name extension does not match its %s content" % kind.upper())
        {"jpeg": walk_jpeg, "png": walk_png, "mp4": walk_mp4}[kind](data)
    except Refused as exc:
        return [str(exc)]
    except (struct.error, IndexError, ValueError, MemoryError, OverflowError) as exc:
        return ["structure does not parse (%s)" % type(exc).__name__]
    return []
