#!/bin/sh
# Write a pixels-only COPY of an image or video (B50c). The original is never touched and an existing output is
# never overwritten. Nothing is copied from the input except decoded pixels (and, for video, decoded frames).
#   usage: strip-image-metadata.sh INPUT OUTPUT
#
#   photos   jpg jpeg tif tiff webp  -> JPEG   OUTPUT must end .jpg or .jpeg
#            heic heif               -> sips decodes to a temporary PNG, then the same path
#   graphics png bmp                 -> PNG    OUTPUT must end .png
#            scripts/strip_media.py (Pillow) decodes to pixels, applies the orientation, converts to sRGB with the
#            embedded ICC profile and DROPS the profile, flattens transparency onto white, builds a new RGB image from
#            the raw pixels and encodes it with one fixed setting: JPEG quality 90 4:2:0 baseline (JFIF APP0, 2 DQT,
#            SOF0, 4 DHT, SOS, EOI) or PNG 8-bit RGB (IHDR, IDAT, IEND). No EXIF, XMP, text, ICC, thumbnail, trailer.
#            Only the first frame of an animated or multi-page file is used.
#   video    mov mp4 m4v             -> MP4    OUTPUT must end .mp4 or .m4v
#            ALWAYS fully re-encoded (never stream copy): first video stream only, H.264 High 8-bit yuv420p (libx264,
#            crf 20, no B-frames), BT.709 limited range, upright (rotation baked in), square pixels, NO AUDIO,
#            `-map_metadata -1 -map_chapters -1`, bitexact, SEI and every other non-slice NAL unit removed
#            (filter_units=pass_types=1|5), no edit list, faststart.
#   gif      refused (exit 2): a GIF has a colour table, extensions and frames the helper does not emit; export one
#            frame as PNG yourself.
# HDR and wide colour are NOT preserved (clipped / mapped to SDR sRGB-BT.709). The result is checked with the gate's
# own container walk (scripts/media_walk.py); output that does not pass is deleted and the script exits 1.
set -eu
[ "$#" -eq 2 ] || { echo "usage: strip-image-metadata.sh INPUT OUTPUT" >&2; exit 2; }
in=$1
out=$2
[ -f "$in" ] || { echo "input is not a file: $in" >&2; exit 2; }
[ ! -e "$out" ] || { echo "output exists, refusing to overwrite: $out" >&2; exit 2; }
[ "$in" -ef "$out" ] 2>/dev/null && { echo "input and output are the same file" >&2; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
in_l=$(lower "$in")
out_l=$(lower "$out")

find_tool() {   # find_tool NAME ENVVALUE -> path on stdout, empty if none
    t=$2
    [ -n "$t" ] || t=$(command -v "$1" 2>/dev/null || true)
    if [ -z "$t" ]; then
        for c in "/opt/homebrew/bin/$1" "/usr/local/bin/$1"; do
            [ -x "$c" ] && { t=$c; break; }
        done
    fi
    [ -n "$t" ] && [ -x "$t" ] && printf '%s' "$t"
    return 0
}
py=$(command -v python3 2>/dev/null || true)
[ -n "$py" ] || { echo "python3 required" >&2; exit 1; }

work=$(mktemp -d "${TMPDIR:-/tmp}/strip-meta.XXXXXX")
trap 'rm -rf "$work"' EXIT INT TERM

case "$in_l" in
*.gif)
    echo "GIF is refused: export one frame as PNG and strip that" >&2; exit 2 ;;
*.jpg|*.jpeg|*.tif|*.tiff|*.webp|*.heic|*.heif) fmt=jpeg ;;
*.png|*.bmp) fmt=png ;;
*.mov|*.mp4|*.m4v) fmt=mp4 ;;
*) echo "unsupported input type (jpg jpeg tif tiff webp heic heif png bmp mov mp4 m4v): $in" >&2; exit 2 ;;
esac
case "$fmt:$out_l" in
jpeg:*.jpg|jpeg:*.jpeg|png:*.png|mp4:*.mp4|mp4:*.m4v) ;;
jpeg:*) echo "a photo is written as JPEG: OUTPUT must end .jpg or .jpeg" >&2; exit 2 ;;
png:*) echo "a PNG / BMP is written as PNG: OUTPUT must end .png" >&2; exit 2 ;;
*) echo "a video is written as MP4: OUTPUT must end .mp4 or .m4v" >&2; exit 2 ;;
esac

case "$fmt" in
jpeg|png)
    "$py" -I -c 'import PIL' 2>/dev/null || { echo "python3 with Pillow required to strip images" >&2; exit 1; }
    src=$in
    case "$in_l" in
    *.heic|*.heif)
        sips=$(command -v sips 2>/dev/null || true)
        [ -n "$sips" ] || { echo "sips required to decode HEIC (macOS)" >&2; exit 1; }
        "$sips" -s format png "$in" --out "$work/decoded.png" >/dev/null
        [ -f "$work/decoded.png" ] || { echo "sips wrote no output" >&2; exit 1; }
        src=$work/decoded.png ;;
    esac
    "$py" -I "$here/strip_media.py" "$fmt" "$src" "$work/result" || { echo "stripping failed; nothing written: $out" >&2; exit 1; }
    ;;
mp4)
    ffmpeg=$(find_tool ffmpeg "${FFMPEG:-}")
    [ -n "$ffmpeg" ] || { echo "ffmpeg required to strip video" >&2; exit 1; }
    # `-n`: never overwrite. -an: no audio (an AAC frame can carry data elements the gate cannot check). One video
    # stream (`0:V:0` skips attached pictures). Frames are decoded (autorotate bakes the rotation in) and re-encoded.
    "$ffmpeg" -nostdin -hide_banner -loglevel error -n -i "$in" \
        -map 0:V:0 -an -sn -dn -map_metadata -1 -map_metadata:s:v -1 -map_chapters -1 \
        -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2:out_color_matrix=bt709:out_range=tv,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,setsar=1,format=yuv420p" \
        -c:v libx264 -preset medium -crf 20 -profile:v high -bf 0 -pix_fmt yuv420p \
        -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv \
        -x264-params info=0 -bsf:v "filter_units=pass_types=1|5" \
        -fflags +bitexact -flags:v +bitexact -movflags +faststart -use_editlist 0 -brand mp42 \
        -f mp4 "$work/result" || { echo "ffmpeg failed; nothing written: $out" >&2; exit 1; }
    echo "video re-encoded (H.264, no audio): $out" >&2
    ;;
esac

# The gate's own container walk must admit the result.
"$py" -I -c '
import sys
sys.path.insert(0, sys.argv[1])
import media_walk
data = open(sys.argv[2], "rb").read()
probs = media_walk.media_problems(sys.argv[3], data)
if probs:
    print("stripped file is not the structure the gate admits (%s); nothing written" % probs[0], file=sys.stderr)
sys.exit(1 if probs else 0)' "$here" "$work/result" "$out" || exit 1

# Publish the finished file; -n so a file that appeared meanwhile is never overwritten.
[ ! -e "$out" ] || { echo "output exists, refusing to overwrite: $out" >&2; exit 2; }
mv -n "$work/result" "$out"
[ -f "$out" ] || { echo "could not write: $out" >&2; exit 1; }
