import json
import os
import subprocess
import wave

import numpy as np
import torch
from PIL import Image

from .store import STORE_DIR

FORMAT_PRESETS = {
    # name: (extension, video args, browser_playable)
    "h264": (".mp4",
             ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19"],
             True),
    "h264_allintra": (".mp4",
                      ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17",
                       "-g", "1", "-tune", "fastdecode"],
                      True),
    "prores": (".mov",
               ["-c:v", "prores_ks", "-profile:v", "3",
                "-pix_fmt", "yuv422p10le"],
               False),
    "ffv1_lossless": (".mkv",
                      ["-c:v", "ffv1", "-level", "3"],
                      False),
}

AUDIO_CODEC_ARGS = {
    "aac": ["-c:a", "aac", "-b:a", "192k"],
    "flac": ["-c:a", "flac"],
    "pcm_s16le": ["-c:a", "pcm_s16le"],
}

AUDIO_FORMAT_PRESETS = {
    "flac": (".flac", ["-c:a", "flac"]),
    "wav": (".wav", ["-c:a", "pcm_s16le"]),
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-b:a", "256k"]),
}


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", path],
        capture_output=True)
    return json.loads(out.stdout)


def video_frame_info(path):
    """Returns (n_frames, src_fps, width, height)."""
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = v["r_frame_rate"].split("/")
    src_fps = float(num) / float(den)
    dur = float(info["format"]["duration"])
    n_frames = int(v.get("nb_frames") or round(dur * src_fps))
    return n_frames, src_fps, v["width"], v["height"]


def write_wav(path, waveform, sample_rate):
    """waveform: tensor [B, C, T] or [C, T], float -1..1"""
    wf = waveform
    if wf.dim() == 3:
        wf = wf[0]
    data = np.clip(wf.cpu().numpy(), -1.0, 1.0)
    data = (data * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(data.shape[0])
        f.setsampwidth(2)
        f.setframerate(int(sample_rate))
        f.writeframes(data.T.tobytes())


def load_video_frames(rec, window):
    """Decode frames [sf, ef) of a stored video into an IMAGE batch
    tensor [F, H, W, 3] float 0..1."""
    path = os.path.join(STORE_DIR, rec["filename"])
    sf, ef = window
    _, _, w, h = video_frame_info(path)
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"trim=start_frame={sf}:end_frame={ef},"
                f"setpts=PTS-STARTPTS",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    frame_size = w * h * 3
    n = len(out) // frame_size
    if n == 0:
        raise RuntimeError(f"could not decode frames {sf}-{ef} of "
                           f"{rec['filename']}")
    arr = np.frombuffer(out[:n * frame_size], np.uint8).reshape(n, h, w, 3)
    return torch.from_numpy(arr.astype(np.float32) / 255.0)


def load_video_last_frame(rec):
    """Decode the final frame of a stored video as [1, H, W, 3]."""
    path = os.path.join(STORE_DIR, rec["filename"])
    _, _, w, h = video_frame_info(path)
    frame_size = w * h * 3
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-sseof", "-1", "-i", path,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True).stdout
    if len(out) < frame_size:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True).stdout
    if len(out) < frame_size:
        raise RuntimeError(f"could not decode last frame of "
                           f"{rec['filename']}")
    n = len(out) // frame_size
    raw = out[(n - 1) * frame_size: n * frame_size]
    arr = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
    return torch.from_numpy(arr.astype(np.float32) / 255.0)[None,]


def load_store_audio_window(rec, start_t, end_t, sample_rate=None):
    """Extract [start_t, end_t) seconds of a stored record's audio track.
    Preserves the NATIVE sample rate and channel count by default; pass
    sample_rate to force a rate explicitly."""
    path = os.path.join(STORE_DIR, rec["filename"])
    stream = next((s for s in probe(path)["streams"]
                   if s["codec_type"] == "audio"), None)
    if stream is None:
        raise RuntimeError(f"no audio stream in {rec['filename']}")
    native_sr = int(stream["sample_rate"])
    channels = int(stream["channels"])

    sr = sample_rate or native_sr
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn",
           "-af", f"atrim=start={start_t:.4f}:end={end_t:.4f},"
                  f"asetpts=PTS-STARTPTS",
           "-f", "f32le", "-ac", str(channels)]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    cmd += ["-"]
    out = subprocess.run(cmd, capture_output=True)
    data = np.frombuffer(out.stdout, np.float32)
    if data.size == 0:
        raise RuntimeError(f"could not extract audio "
                           f"{start_t:.2f}-{end_t:.2f}s of "
                           f"{rec['filename']}")
    data = data.reshape(-1, channels).T
    return {"waveform": torch.from_numpy(data.copy())[None,],
            "sample_rate": sr}


def encode_video_master(arr, fps, aud, base, ext, vargs, audio_args=None):
    """arr: uint8 [F,H,W,3]. Returns (master_filename, poster_filename).
    audio_args overrides the audio codec (default AAC 192k)."""
    master, poster = base + ext, base + "_poster.jpg"
    f, h, w, _ = arr.shape
    wav_path = None
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if aud is not None:
        wav_path = os.path.join(STORE_DIR, base + "_tmp.wav")
        write_wav(wav_path, aud["waveform"], aud["sample_rate"])
        cmd += ["-i", wav_path]
    cmd += vargs
    if aud is not None:
        cmd += list(audio_args or AUDIO_CODEC_ARGS["aac"]) + ["-shortest"]
    cmd += [os.path.join(STORE_DIR, master)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    proc.stdin.write(arr.tobytes())
    proc.stdin.close()
    ok = proc.wait() == 0
    if wav_path:
        os.remove(wav_path)
    if not ok:
        raise RuntimeError(f"ffmpeg encode failed ({' '.join(cmd)})")
    Image.fromarray(arr[0]).save(os.path.join(STORE_DIR, poster), quality=85)
    return master, poster


def encode_audio_master(aud, base, ext, aargs):
    """Returns (master_filename, waveform_poster_filename)."""
    master, poster = base + ext, base + "_wave.png"
    wav_path = os.path.join(STORE_DIR, base + "_tmp.wav")
    write_wav(wav_path, aud["waveform"], aud["sample_rate"])
    ok = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path] + aargs
        + [os.path.join(STORE_DIR, master)],
        stderr=subprocess.DEVNULL).returncode == 0
    ok = ok and subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-filter_complex",
         "showwavespic=s=480x120:colors=0x66cc66", "-frames:v", "1",
         os.path.join(STORE_DIR, poster)],
        stderr=subprocess.DEVNULL).returncode == 0
    os.remove(wav_path)
    if not ok:
        raise RuntimeError(f"audio encode failed for {base}")
    return master, poster