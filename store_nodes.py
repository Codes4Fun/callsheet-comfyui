import os
import subprocess

import numpy as np
from PIL import Image

from .media import (FORMAT_PRESETS, AUDIO_CODEC_ARGS, AUDIO_FORMAT_PRESETS,
                    encode_video_master, encode_audio_master)
from .store import STORE_DIR, register, unresolved
from .config import STORE_SUBFOLDER
from .binaries import FFMPEG


def _check_kinds(job, expected, node_name):
    """Typed jobs must land in the matching store node. Untyped (lazy)
    jobs pass through — the store stamps the kind on registration."""
    for j in job:
        k = j.get("kind")
        if k and k != expected:
            raise ValueError(
                f"{node_name}: job '{j['label']}' is typed '{k}' but is "
                f"wired into the {expected} store — check pipeline "
                f"wiring")


class CallsheetStoreImages:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE", {"lazy": True}),
            "job": ("CS_JOB",),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "store"
    CATEGORY = "callsheet"

    def check_lazy_status(self, images, job):
        if not job:
            return []
        return ["images"] if unresolved(images) else []

    def store(self, images, job):
        if not job:
            return ([],)
        _check_kinds(job, "image", "CallsheetStoreImages")
        if unresolved(images):
            raise RuntimeError("CallsheetStoreImages: images never "
                               "resolved despite non-empty job list")
        if len(images) != len(job):
            raise ValueError(f"CallsheetStoreImages: {len(images)} images "
                             f"vs {len(job)} jobs — wire both from the "
                             f"same pipeline")
        os.makedirs(STORE_DIR, exist_ok=True)
        acks = []
        for image, j in zip(images, job):
            arr = np.clip(image[0].cpu().numpy() * 255.0,
                          0, 255).astype(np.uint8)
            filename = f"cs_{j['label']}_{j['key']}.png"
            Image.fromarray(arr).save(os.path.join(STORE_DIR, filename),
                                      compress_level=4)
            register(j["key"], {
                "label": j["label"], "index": j["index"],
                "seed": j["seed"], "kind": "image", "filename": filename,
                "subfolder": STORE_SUBFOLDER, "type": "output"})
            acks.append(j["key"])
        return (acks,)


class CallsheetStoreVideos:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "frames": ("IMAGE", {"lazy": True}),
            "fps": ("INT", {"default": 24, "min": 1, "max": 120,
                            "forceInput": True}),
            "job": ("CS_JOB",),
            "format": (list(FORMAT_PRESETS.keys()),),
            # 'flac'/'pcm_s16le' avoid lossy audio compounding across
            # continuation chains; aac is the browser-safe default
            "audio_format": (list(AUDIO_CODEC_ARGS.keys()),),
        }, "optional": {
            "audio": ("AUDIO", {"lazy": True}),
            "custom_ffmpeg_args": ("STRING", {"default": "",
                                              "advanced": True}),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "store"
    CATEGORY = "callsheet"

    def check_lazy_status(self, frames, fps, job, format, audio_format,
                          audio=None, custom_ffmpeg_args=None, **kw):
        if not job:
            return []
        needed = []
        if unresolved(frames):
            needed.append("frames")
        if audio is not None and unresolved(audio):
            needed.append("audio")
        return needed

    def store(self, frames, fps, job, format, audio_format,
              audio=None, custom_ffmpeg_args=None):
        if not job:
            return ([],)
        _check_kinds(job, "video", "CallsheetStoreVideos")
        if unresolved(frames):
            raise RuntimeError("CallsheetStoreVideos: frames never "
                               "resolved despite non-empty job list")
        if len(frames) != len(job):
            raise ValueError(f"CallsheetStoreVideos: {len(frames)} clips "
                             f"vs {len(job)} jobs")

        format = format[0]
        audio_format = audio_format[0]
        custom = (custom_ffmpeg_args[0].strip()
                  if custom_ffmpeg_args else "")
        ext, vargs, browser_ok = FORMAT_PRESETS[format]
        if custom:
            vargs = custom.split()
            browser_ok = False

        audio_args = list(AUDIO_CODEC_ARGS[audio_format])
        if ext == ".mp4":
            if audio_format == "pcm_s16le":
                raise ValueError(
                    "CallsheetStoreVideos: pcm_s16le is not supported in "
                    "mp4 — use prores/ffv1_lossless, or flac")
            if audio_format == "flac":
                audio_args += ["-strict", "-2"]   # flac-in-mp4

        audio = audio if audio and not unresolved(audio) else None
        if audio is not None and len(audio) not in (1, len(job)):
            raise ValueError(f"CallsheetStoreVideos: {len(audio)} audio "
                             f"clips vs {len(job)} jobs (need 1 or equal)")

        os.makedirs(STORE_DIR, exist_ok=True)
        acks = []
        for n, (batch, j) in enumerate(zip(frames, job)):
            arr = np.clip(batch.cpu().numpy() * 255.0,
                          0, 255).astype(np.uint8)
            clip_fps = fps[n if len(fps) > 1 else 0] or 24
            aud = None
            if audio is not None:
                aud = audio[n if len(audio) > 1 else 0]

            base = f"cs_{j['label']}_{j['key']}"
            master, poster = encode_video_master(
                arr, clip_fps, aud, base, ext, vargs, audio_args)

            preview = None
            if not browser_ok:
                preview = base + "_preview.mp4"
                pcmd = [FFMPEG, "-y",
                        "-i", os.path.join(STORE_DIR, master),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-crf", "26", "-vf", "scale=-2:'min(720,ih)'",
                        "-c:a", "aac", "-b:a", "128k",
                        os.path.join(STORE_DIR, preview)]
                if subprocess.run(pcmd,
                                  stderr=subprocess.DEVNULL).returncode:
                    raise RuntimeError(f"preview encode failed for "
                                       f"{j['label']}")

            register(j["key"], {
                "label": j["label"], "index": j["index"],
                "seed": j["seed"], "kind": "video", "format": format,
                "has_audio": aud is not None,
                "poster": poster, "preview": preview, "filename": master,
                "subfolder": STORE_SUBFOLDER, "type": "output"})
            acks.append(j["key"])
        return (acks,)


class CallsheetStoreAudio:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO", {"lazy": True}),
            "job": ("CS_JOB",),
            "format": (list(AUDIO_FORMAT_PRESETS.keys()),),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "store"
    CATEGORY = "callsheet"

    def check_lazy_status(self, audio, job, format):
        if not job:
            return []
        return ["audio"] if unresolved(audio) else []

    def store(self, audio, job, format):
        if not job:
            return ([],)
        _check_kinds(job, "audio", "CallsheetStoreAudio")
        if unresolved(audio):
            raise RuntimeError("CallsheetStoreAudio: audio never resolved "
                               "despite non-empty job list")
        if len(audio) != len(job):
            raise ValueError(f"CallsheetStoreAudio: {len(audio)} clips vs "
                             f"{len(job)} jobs")
        format = format[0]
        ext, aargs = AUDIO_FORMAT_PRESETS[format]
        os.makedirs(STORE_DIR, exist_ok=True)
        acks = []
        for aud, j in zip(audio, job):
            base = f"cs_{j['label']}_{j['key']}"
            master, poster = encode_audio_master(aud, base, ext, aargs)
            register(j["key"], {
                "label": j["label"], "index": j["index"],
                "seed": j["seed"], "kind": "audio", "format": format,
                "has_audio": True, "poster": poster, "filename": master,
                "subfolder": STORE_SUBFOLDER, "type": "output"})
            acks.append(j["key"])
        return (acks,)