import os
import subprocess

import numpy as np
from PIL import Image

from .media import (FORMAT_PRESETS, AUDIO_CODEC_ARGS, AUDIO_FORMAT_PRESETS,
                    encode_video_master, encode_audio_master)
from .store import STORE_DIR, register, unresolved
from .config import STORE_SUBFOLDER
from .binaries import FFMPEG
from . import runstate


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
            "job": ("CS_JOB",),
            "images": ("IMAGE", {"lazy": True}),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "store"
    CATEGORY = "callsheet"

    def check_lazy_status(self, images, job):
        if not job:
            return []
        return ["images"] if unresolved(images) else []

    def store(self, job, images):
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
        runstate.report_received(len(job))
        return (acks,)


class CallsheetStoreVideos:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "job": ("CS_JOB",),
            "fps": ("INT", {"default": 24, "min": 1, "max": 120,
                            "forceInput": True}),
            "frames": ("IMAGE", {"lazy": True}),
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

    def check_lazy_status(self, job, fps, frames, format, audio_format,
                          audio=None, custom_ffmpeg_args=None, **kw):
        if not job:
            return []
        needed = []
        if unresolved(frames):
            needed.append("frames")
        if audio is not None and unresolved(audio):
            needed.append("audio")
        return needed

    def store(self, job, fps, frames, format, audio_format,
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
        runstate.report_received(len(job))
        return (acks,)


class CallsheetStoreAudio:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "job": ("CS_JOB",),
            "audio": ("AUDIO", {"lazy": True}),
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

    def store(self, job, audio, format):
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
        runstate.report_received(len(job))
        return (acks,)


class CallsheetStore:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "job": ("CS_JOB",),
            "fps": ("INT", {"default": 24, "min": 1, "max": 120,
                            "forceInput": True}),
            "video_format": (list(FORMAT_PRESETS.keys()),),
            "video_audio": (list(AUDIO_CODEC_ARGS.keys()),),
            "audio_format": (list(AUDIO_FORMAT_PRESETS.keys()),),
            "image_index": ("INT", {"default": -1, "lazy": True}),
        }, "optional": {
            "images": ("IMAGE", {"lazy": True}),
            "audio": ("AUDIO", {"lazy": True}),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "store"
    CATEGORY = "callsheet"

    def check_lazy_status(self, job, fps, video_format, video_audio,
                          audio_format, image_index, images=None, audio=None):
        if not job:
            return []
        needed = []
        if images is not None and unresolved(images):
            needed.append("images")
            needed.append("image_index")
        if audio is not None and unresolved(audio):
            needed.append("audio")
        return needed

    def store(self, job, fps, video_format, video_audio, audio_format,
              image_index, images=None, audio=None):
        if not job:
            return ([],)

        # validate jobs
        kinds = set()
        for j in job:
            k = j.get("kind")
            if k is None:
                raise ValueError(
                    f"CallsheetStore: job '{j['label']}' is untyped!")
            kinds.add(k)

        # validate images/audio inputs
        if 'video' in kinds or 'image' in kinds:
            if unresolved(images):
                raise RuntimeError("CallsheetStore: images never "
                                "resolved despite non-empty job list")
            if len(images) != len(job):
                raise ValueError(f"CallsheetStore: {len(images)} images "
                                f"vs {len(job)} jobs")

        if ('video' in kinds and audio) or 'audio' in kinds:
            if unresolved(audio):
                raise RuntimeError("CallsheetStore: audio never resolved "
                                "despite non-empty job list")
            if len(audio) != len(job):
                raise ValueError(f"CallsheetStore: {len(audio)} audio "
                                f"vs {len(job)} jobs")

        os.makedirs(STORE_DIR, exist_ok=True)

        video_format = video_format[0]
        video_audio = video_audio[0]
        audio_format = audio_format[0]

        video_ext, video_args, browser_ok = FORMAT_PRESETS[video_format]
        video_aargs = list(AUDIO_CODEC_ARGS[video_audio])
        audio_ext, audio_args = AUDIO_FORMAT_PRESETS[audio_format]

        if video_ext == ".mp4":
            if video_audio == "pcm_s16le":
                raise ValueError(
                    "CallsheetStore: pcm_s16le is not supported in "
                    "mp4 — use prores/ffv1_lossless, or flac")
            if video_audio == "flac":
                video_aargs += ["-strict", "-2"]   # flac-in-mp4

        # store media
        acks = []
        for n, j in enumerate(job):
            kind = j["kind"]
            match kind:
                case "image":
                    batch = images[n]
                    index = image_index[n if len(image_index) > 1 else 0]
                    if abs(index) >= len(batch): # clamp
                        index = 0 if index < 0 else -1
                    image = batch[index]
                    arr = np.clip(image.cpu().numpy() * 255.0,
                                0, 255).astype(np.uint8)
                    filename = f"cs_{j['label']}_{j['key']}.png"
                    Image.fromarray(arr).save(os.path.join(STORE_DIR, filename),
                                            compress_level=4)
                    register(j["key"], {
                        "label": j["label"], "index": j["index"],
                        "seed": j["seed"], "kind": "image", "filename": filename,
                        "subfolder": STORE_SUBFOLDER, "type": "output"})
                    acks.append(j["key"])
                case "video":
                    batch = images[n]
                    arr = np.clip(batch.cpu().numpy() * 255.0,
                                0, 255).astype(np.uint8)
                    clip_fps = fps[n if len(fps) > 1 else 0] or 24
                    aud = None
                    if audio is not None:
                        aud = audio[n]

                    base = f"cs_{j['label']}_{j['key']}"
                    master, poster = encode_video_master( arr, clip_fps, aud,
                        base, video_ext, video_args, video_aargs)

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
                        "seed": j["seed"], "kind": "video",
                        "format": video_format, "has_audio": aud is not None,
                        "poster": poster, "preview": preview, "filename": master,
                        "subfolder": STORE_SUBFOLDER, "type": "output"})
                    acks.append(j["key"])
                case "audio":
                    aud = audio[n]
                    base = f"cs_{j['label']}_{j['key']}"
                    master, poster = encode_audio_master(aud, base, audio_ext,
                        audio_args)
                    register(j["key"], {
                        "label": j["label"], "index": j["index"],
                        "seed": j["seed"], "kind": "audio",
                        "format": audio_format, "has_audio": True,
                        "poster": poster, "filename": master,
                        "subfolder": STORE_SUBFOLDER, "type": "output"})
                    acks.append(j["key"])
        runstate.report_received(len(job))
        return (acks,)
