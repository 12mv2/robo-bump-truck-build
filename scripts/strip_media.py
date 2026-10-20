#!/usr/bin/env python3
"""strip_media.py - the image half of scripts/strip-image-metadata.sh (pixels only).

    python3 -I strip_media.py FORMAT INPUT OUTPUT       FORMAT is jpeg or png

Decodes INPUT to pixels, applies the EXIF / PNG orientation to them, converts them to sRGB (any embedded ICC profile
is used for the conversion and then dropped; no profile is written), flattens transparency onto white, builds a NEW
8-bit RGB image from the raw pixel bytes (nothing from the input - no info dict, no EXIF, XMP, text, ICC, thumbnail
or ancillary chunk - can reach the encoder), and encodes it with the one fixed encoder setting scripts/media_walk.py
admits:
  jpeg   Pillow / libjpeg quality 90, 4:2:0, baseline, standard Huffman tables, JFIF APP0 only
  png    Pillow / zlib, 8-bit RGB, IHDR + IDAT + IEND only
Only the first frame of an animated or multi-page input is used. HDR and wide colour are not preserved (clipped to sRGB).
The output is written to OUTPUT, which must not exist."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))     # python3 -I does not add the script's directory

from PIL import Image, ImageCms, ImageOps  # noqa: E402

import media_walk  # noqa: E402

Image.MAX_IMAGE_PIXELS = media_walk.MAX_PIXELS


def to_srgb_pixels(path):
    im = Image.open(path)
    im.load()
    im = ImageOps.exif_transpose(im)            # bake the rotation into the pixels; the tag is not written back
    icc = im.info.get("icc_profile")
    if im.mode.startswith("I"):                 # 16-bit grey: high byte
        im = im.convert("I").point(lambda v: v * (1 / 256.0)).convert("L")
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info) or "transparency" in im.info:
        rgba = im.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, rgba).convert("RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            srgb = ImageCms.createProfile("sRGB")
            if im.mode not in ("RGB", "CMYK", "L"):
                im = im.convert("RGB")
            im = ImageCms.profileToProfile(im, src, srgb, renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                                           outputMode="RGB", flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        except Exception as exc:
            print("note: could not apply the embedded ICC profile (%s); pixels taken as sRGB" % type(exc).__name__, file=sys.stderr)
    im = im.convert("RGB")
    return Image.frombytes("RGB", im.size, im.tobytes())     # a fresh image: no info, no profile, no metadata of any kind


def main(argv):
    if len(argv) != 4 or argv[1] not in ("jpeg", "png"):
        print("usage: strip_media.py jpeg|png INPUT OUTPUT", file=sys.stderr)
        return 2
    fmt, src, dst = argv[1:]
    if os.path.lexists(dst):
        print("output exists, refusing to overwrite: " + dst, file=sys.stderr)
        return 2
    try:
        clean = to_srgb_pixels(src)
        with open(dst, "xb") as fh:
            if fmt == "jpeg":
                clean.save(fh, "JPEG", quality=media_walk.QUALITY, subsampling=2, optimize=False, progressive=False)
            else:
                clean.save(fh, "PNG", optimize=False, compress_level=6)
    except Exception as exc:
        if os.path.lexists(dst):
            os.unlink(dst)
        print("cannot decode / re-encode %s: %s" % (src, type(exc).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
