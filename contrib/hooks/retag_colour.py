#!/usr/bin/env python3
"""
retag_colour.py -- write a COMPLETE, CONSISTENT colour description onto Shopcam
recordings, losslessly. Nothing is ever re-encoded.

WHY THIS EXISTS
---------------
Blue Iris records each camera with whatever colour description the camera itself
supplies -- and most of the fleet supplies NOTHING. Measured on the wire 2026-08-24:
CAM7 and CAM2 (two Amcrest IP cameras) both emit `yuv420p` with no range, no
matrix, no primaries, no transfer. BI is not dropping a flag; there never was one.

So one take arrives in the NLE under three different colour regimes:

    CAM3, CAM4          pc + bt709 x3          correct
    CAM1, CAM5, CAM8    pc + bt470bg + bt709   self-contradictory (601 matrix, 709 primaries)
    CAM2, CAM7, CAM9    nothing at all         app falls back to limited 16-235
    TIMMYCAM            tv + nothing else      declares limited
    MIC1                nothing at all

An app reading an untagged file assumes limited range, expands 16-235 to 0-255, and
clamps everything outside. Measured on CAM7 (INTRO take, one frame):

    as-is (read as limited)   luma 77.69   flat black 2.89%   flat white 3.74%
    tagged full-range         luma 85.79   flat black 0.00%   flat white 0.34%

That is ~10x the crush CAM7 was deliberately pinned at on 2026-08-22 (crush 0.27% /
blown-white 1.09%). The NLE was quietly undoing the calibration.

THE RANGE CALL IS A JUDGEMENT CALL, NOT A MEASUREMENT -- READ BEFORE RE-RUNNING
------------------------------------------------------------------------------
Pixel statistics CANNOT separate full-range data from limited-range data. This was
tested and the control FAILED: licensed Pexels clips correctly tagged `tv` show the
same below-16 content as the untagged Shopcam files (sun-plasma-cg 84.6% low;
molten-metal 20.3% low / 11.7% high). "Has content outside 16-235" proves nothing --
it can just be dark or crushed footage.

`pc` is written on the USER'S RULING (2026-08-24), on this argument:

  * full-range is the NON-DESTRUCTIVE direction -- it never clips.
  * if it is wrong the picture merely looks flat; a contrast move recovers it.
  * if we leave it undeclared and the data IS full-range, shadow and highlight
    detail is clamped away and cannot be recovered.
  * every change here is metadata-only and reversible via --revert.

To settle it properly: put the SpyderCheckr where CAM7 and CAM1 both see it, one
press, then check which interpretation puts the black patch near 3% of white without
clipping. Until that is done, `pc` is a defensible default and NOT a measured fact.

GUARANTEES
----------
Each file must pass three gates before it replaces the original:
  1. the DECODED picture (framemd5, head + middle samples) is identical
  2. the tags read back exactly as the target
  3. the packet count is unchanged
Any failure leaves the original untouched and removes the temp file.

DO NOT "verify" by hashing the bitstream -- that check is wrong here and looks right.
Blue Iris writes SPS/PPS IN-BAND, repeated on every IDR, so editing the SPS legitimately
changes packet payloads (measured on CAM7: packet count identical at 2117, total bytes
+355 = ~5 bytes x 71 keyframes, exactly the VUI being added). The NVENC output of
cfr_conform.py carries SPS out-of-band in extradata and DOES hash identically -- so a
bitstream check passes on conformed clips and fails on every raw original in the archive.
That is how the first version of this tool rejected 43 of its first 50 NAS files.

USAGE
-----
    python tools/retag_colour.py <root>                     # walk and fix
    python tools/retag_colour.py <root> --dry-run           # report only, write nothing
    python tools/retag_colour.py <root> -m manifest.json    # choose manifest location
    python tools/retag_colour.py --revert manifest.json     # put the old tags back

Only files named CAM<n>.*, TIMMYCAM.* are touched. MIC1 is excluded on purpose (see below). Licensed stock, phone
footage (IMG_*.MOV), DJI, OBS captures and screen recordings are skipped by design --
they are already correctly tagged and are NOT ours to relabel.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# ffmpeg is NOT on the rig's PATH -- the bridge calls it by absolute path. Same
# candidate list and same order as tools/cfr_conform.py; keep them in step.
FFMPEG_DIR_CANDIDATES = [
    r"C:\shopcam2000-bridge\ffmpeg",
    r"C:\ffmpeg\bin",   # wherever your ffmpeg lives; PATH is searched first
]


def _find_ffmpeg():
    for d in FFMPEG_DIR_CANDIDATES:
        ff = os.path.join(d, "ffmpeg.exe")
        fp = os.path.join(d, "ffprobe.exe")
        if os.path.isfile(ff) and os.path.isfile(fp):
            return ff, fp
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    if ff and fp:
        return ff, fp
    sys.exit("ERROR: could not locate ffmpeg/ffprobe. Checked: %s"
             % ", ".join(FFMPEG_DIR_CANDIDATES))


FFMPEG, FFPROBE = _find_ffmpeg()

TARGET = ("pc", "bt709", "bt709", "bt709")
BSF_ARGS = ("video_full_range_flag=1:matrix_coefficients=1"
            ":colour_primaries=1:transfer_characteristics=1")
BSF_FOR = {"h264": "h264_metadata", "hevc": "hevc_metadata"}
SHOPCAM = re.compile(r"^(CAM[0-9]+|TIMMYCAM)\.", re.I)

# MIC1 IS DELIBERATELY EXCLUDED. Two reasons, the second is the hard one:
#   1. it is a microphone. Its "video" is a scope image nobody grades, so a colour
#      description on it buys nothing.
#   2. the raw MIC1 originals carry the known `blue-iris-counts-x264-slices-as-frames`
#      condition -- 2026-08-01 sample reports nb_frames 5274 over 70.294 s = 75 fps
#      against a real r_frame_rate of 30, i.e. ~2.5 slices per picture. Rewriting the
#      SPS on a sliced stream GENUINELY CHANGES THE DECODE: full-file framemd5 differs
#      before vs after, and the verification gate correctly refuses to install it.
# If MIC1 ever needs tagging, fix the slice condition first -- do not loosen the gate.
MEDIA_EXT = (".mp4", ".mov", ".mkv")

# H.273 code points, for rebuilding an original tag set on the revert path.
CODE = {"bt709": 1, "unknown": 2, "bt470bg": 5, "bt601": 5, "smpte170m": 6,
        "smpte240m": 7, "bt2020nc": 9, "bt2020": 9}

# NOTE: MP4 silently DROPS color_range when it is the ONLY colour tag -- the `colr`
# atom is skipped with no primaries/transfer/matrix to write beside it. That is why
# all four fields are always written together, never just the range.


def probe(path):
    """Return [codec, range, space, primaries, transfer, nb_frames] or None."""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,color_range,color_space,color_primaries,color_transfer,nb_frames",
         "-of", "csv=p=0", path], capture_output=True, text=True)
    fields = r.stdout.strip().split(",")
    return fields if len(fields) >= 6 else None


def decoded_md5(path, sample_frames=40):
    """MD5 of DECODED frames -- head and middle. This is the property that matters.

    Do NOT verify by hashing the bitstream instead. Blue Iris writes SPS/PPS IN-BAND
    (repeated on every IDR), so editing the SPS legitimately changes packet payloads:
    measured on CAM7, packet count identical at 2117 but total bytes +355, which is
    ~5 bytes x 71 keyframes -- exactly the VUI colour_description being added. The
    NVENC re-encodes from cfr_conform.py carry SPS out-of-band in extradata, so THEY
    hash identically and a bitstream check appears to work. It silently fails on the
    raw originals, which is most of the archive.
    """
    parts = []
    for ss in ("0", "20"):
        r = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", ss, "-i", path, "-map", "0:v",
             "-frames:v", str(sample_frames), "-f", "framemd5", "-"],
            capture_output=True, text=True)
        parts.append("\n".join(l for l in r.stdout.splitlines() if not l.startswith("#")))
    joined = "\n".join(parts)
    return joined if joined.strip() else ""


def packet_count(path):
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    return r.stdout.strip()


def bsf_args_for(values):
    """Build the metadata-bsf argument string for an arbitrary tag tuple."""
    rng, spc, pri, trc = values
    return (f"video_full_range_flag={1 if rng == 'pc' else 0}"
            f":matrix_coefficients={CODE.get(spc, 2)}"
            f":colour_primaries={CODE.get(pri, 2)}"
            f":transfer_characteristics={CODE.get(trc, 2)}")


def write_tags(path, codec, values, dry_run=False):
    """Write `values` == (range, space, primaries, transfer) onto path, losslessly."""
    bsf = BSF_FOR.get(codec)
    if not bsf:
        return f"SKIP-codec:{codec}"
    if dry_run:
        return "would-write"

    args = BSF_ARGS if tuple(values) == TARGET else bsf_args_for(values)
    base, ext = os.path.splitext(path)
    tmp = base + ".retagtmp" + ext          # keep the extension: ffmpeg picks the muxer from it

    pix_before = decoded_md5(path)
    pkts_before = packet_count(path)

    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", path, "-c", "copy",
                        "-bsf:v", f"{bsf}={args}", tmp], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(tmp):
        if os.path.exists(tmp):
            os.remove(tmp)
        return "FFMPEG-FAIL:" + r.stderr.strip()[:120]

    info_after = probe(tmp)
    # 1. the decoded picture must be unchanged -- the only thing we actually care about
    ok_pixels = bool(pix_before) and pix_before == decoded_md5(tmp)
    # 2. the tags must have landed exactly
    ok_tags = bool(info_after) and tuple(info_after[1:5]) == tuple(values)
    # 3. no packet may be added, dropped or reordered
    ok_pkts = bool(pkts_before) and pkts_before == packet_count(tmp)
    if not (ok_pixels and ok_tags and ok_pkts):
        os.remove(tmp)
        return f"VERIFY-FAIL pixels={ok_pixels} tags={ok_tags} packets={ok_pkts}"

    os.replace(tmp, path)
    return "written"


TMP_MARK = ".retagtmp"


def walk(root):
    """Collect Shopcam media under root.

    TMP_MARK is excluded explicitly: our own temp files are named
    `<base>.retagtmp<ext>`, which still starts with `CAM<n>.` and therefore still
    matches SHOPCAM. A killed run leaves one behind, and the next run would otherwise
    treat that half-written file as INPUT. Seen for real: a TaskStop mid-ffmpeg left
    `CAM8.20260816_203432.retagtmp.mp4` on the NAS and the following run tried to
    process it.
    """
    found = []
    for dirpath, _, names in os.walk(root):
        for name in names:
            if TMP_MARK in name.lower():
                continue
            if name.lower().endswith(MEDIA_EXT) and SHOPCAM.match(name):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def sweep_temps(root, delete=False):
    """Find (and optionally remove) temp files orphaned by an interrupted run."""
    strays = []
    for dirpath, _, names in os.walk(root):
        for name in names:
            if TMP_MARK in name.lower():
                strays.append(os.path.join(dirpath, name))
    if delete:
        for s in strays:
            try:
                os.remove(s)
            except OSError:
                pass
    return sorted(strays)


def _bump(tally, status):
    key = status.split(":")[0].split(" ")[0]
    tally[key] = tally.get(key, 0) + 1


def cmd_fix(root, manifest_path, dry_run):
    strays = sweep_temps(root, delete=not dry_run)
    if strays:
        verb = "found (dry run, left in place)" if dry_run else "removed"
        print(f"orphaned temp file(s) {verb} from an interrupted run:")
        for s in strays:
            print(f"  {s}")
        print()
    files = walk(root)
    banner = "  [DRY RUN -- nothing will be written]" if dry_run else ""
    print(f"{len(files)} Shopcam file(s) under {root}{banner}\n")
    tally, manifest = {}, []
    for i, path in enumerate(files, 1):
        info = probe(path)
        if not info:
            status = "PROBE-FAIL"
        elif tuple(info[1:5]) == TARGET:
            status = "already-correct"
        else:
            status = write_tags(path, info[0], TARGET, dry_run)
            if status == "written":
                manifest.append({"path": path, "codec": info[0], "was": list(info[1:5])})
        _bump(tally, status)
        if status not in ("written", "already-correct", "would-write"):
            print(f"  !! {os.path.basename(path)}: {status}", flush=True)
        if i % 25 == 0 or i == len(files):
            print(f"  ...{i}/{len(files)}  {tally}", flush=True)

    if manifest and not dry_run:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1)
        print(f"\nrevert manifest -> {manifest_path}  ({len(manifest)} entries)")
    print(f"\nDONE  {tally}")
    return 0


def cmd_revert(manifest_path):
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    print(f"reverting {len(manifest)} file(s) from {manifest_path}\n")
    tally = {}
    for i, rec in enumerate(manifest, 1):
        if not os.path.exists(rec["path"]):
            status = "MISSING"
        else:
            status = write_tags(rec["path"], rec["codec"], tuple(rec["was"]))
        _bump(tally, status)
        if status != "written":
            print(f"  !! {os.path.basename(rec['path'])}: {status}", flush=True)
        if i % 25 == 0 or i == len(manifest):
            print(f"  ...{i}/{len(manifest)}  {tally}", flush=True)
    print(f"\nDONE  {tally}")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", help="folder to walk (recursive)")
    ap.add_argument("-m", "--manifest", default="retag_manifest.json",
                    help="where to write the revert manifest")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--revert", metavar="MANIFEST",
                    help="restore the tags recorded in MANIFEST")
    args = ap.parse_args()

    if args.revert:
        return cmd_revert(args.revert)
    if not args.root:
        ap.error("give a root folder, or --revert MANIFEST")
    return cmd_fix(args.root, args.manifest, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
