import os
import re
import shutil
import subprocess


def _resolve():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg = None
    if ffmpeg and not ffprobe:
        # system installs keep them side by side; imageio's does not
        exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        cand = os.path.join(os.path.dirname(ffmpeg), exe)
        if os.path.isfile(cand):
            ffprobe = cand
    return ffmpeg, ffprobe


FFMPEG, FFPROBE = _resolve()

if not FFMPEG:
    print("\033[93m[Callsheet] WARNING: ffmpeg not found. Install ffmpeg "
          "on your PATH, or `pip install imageio-ffmpeg`, then restart "
          "ComfyUI.\033[0m")
elif not FFPROBE:
    print("[Callsheet] ffprobe not found; using ffmpeg-based probing "
          "fallback (bundled imageio-ffmpeg has no ffprobe).")


import json  # noqa: E402


def probe(path):
    """ffprobe JSON when available; otherwise a fallback that parses
    `ffmpeg -i` stderr into the same shape (the subset Callsheet uses:
    format.duration, and per-stream codec_type, width/height,
    r_frame_rate, sample_rate, channels)."""
    if FFPROBE:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True)
        return json.loads(out.stdout)
    return _probe_fallback(path)


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #\d+:\d+.*?: Video:.*?"
                    r"(\d{2,5})x(\d{2,5}).*?"
                    r"([\d.]+)\s*fps", re.S)
_AUDIO = re.compile(r"Stream #\d+:\d+.*?: Audio:.*?"
                    r"(\d+)\s*Hz,\s*([\w.]+)")

_CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "5.1": 6, "7.1": 8}


def _probe_fallback(path):
    err = subprocess.run([FFMPEG, "-hide_banner", "-i", path],
                         capture_output=True, text=True).stderr
    result = {"format": {}, "streams": []}
    m = _DURATION.search(err)
    if m:
        h, mn, s = m.groups()
        result["format"]["duration"] = str(
            int(h) * 3600 + int(mn) * 60 + float(s))
    m = _VIDEO.search(err)
    if m:
        w, h, fps = m.groups()
        result["streams"].append({
            "codec_type": "video", "width": int(w), "height": int(h),
            "r_frame_rate": f"{fps}/1"})   # nb_frames absent: callers
                                           # already fall back to dur*fps
    m = _AUDIO.search(err)
    if m:
        hz, layout = m.groups()
        ch = _CHANNELS.get(layout)
        if ch is None:
            cm = re.match(r"(\d+)", layout)
            ch = int(cm.group(1)) if cm else 2
        result["streams"].append({
            "codec_type": "audio", "sample_rate": hz, "channels": ch})
    return result