import hashlib
import os

import numpy as np
from PIL import Image

from .media import (FORMAT_PRESETS, AUDIO_FORMAT_PRESETS,
                    encode_video_master, encode_audio_master)
from .store import STORE_DIR, VARIATION_STORE, register
from .config import STORE_SUBFOLDER
from . import runstate


def _find_injected(parsed, label, node_name, kind):
    match = next((it for it in parsed["items"] if it["label"] == label),
                 None)
    if match is None:
        raise ValueError(f"{node_name}: label '{label}' not in callsheet")
    if not match["injected"]:
        raise ValueError(f"{node_name}: item '{label}' is not marked "
                         f"'source: injected'")
    t = match.get("type")
    if t and t != kind:
        raise ValueError(f"{node_name}: item '{label}' is in a pipeline "
                         f"typed '{t}', but this node injects {kind}")
    return match


class CallsheetInjectImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "image": ("IMAGE",),
            "label": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "inject"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def inject(self, parsed, image, label):
        match = _find_injected(parsed, label, "CallsheetInjectImage",
                               "image")
        arr = np.clip(image[0].cpu().numpy() * 255.0,
                      0, 255).astype(np.uint8)
        key = hashlib.sha1(b"injected" + label.encode()
                           + arr.tobytes()).hexdigest()[:16]
        if key not in VARIATION_STORE:
            os.makedirs(STORE_DIR, exist_ok=True)
            filename = f"cs_{label}_{key}.png"
            Image.fromarray(arr).save(os.path.join(STORE_DIR, filename),
                                      compress_level=4)
            register(key, {"label": label, "index": match["index"],
                           "seed": 0, "kind": "image", "injected": True,
                           "filename": filename,
                           "subfolder": STORE_SUBFOLDER, "type": "output"})
            runstate.report(1, 0)   # new content counts as progress
        return ([key],)


class CallsheetInjectVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "frames": ("IMAGE",),
            "fps": ("INT", {"default": 24, "min": 1, "max": 120}),
            "label": ("STRING", {"default": ""}),
        }, "optional": {"audio": ("AUDIO",)}}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "inject"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def inject(self, parsed, frames, fps, label, audio=None):
        match = _find_injected(parsed, label, "CallsheetInjectVideo",
                               "video")
        arr = np.clip(frames.cpu().numpy() * 255.0,
                      0, 255).astype(np.uint8)
        hasher = hashlib.sha1(b"injvid" + label.encode() + bytes([fps]))
        hasher.update(arr.tobytes())
        if audio is not None:
            hasher.update(audio["waveform"].cpu().numpy().tobytes())
        key = hasher.hexdigest()[:16]
        if key not in VARIATION_STORE:
            os.makedirs(STORE_DIR, exist_ok=True)
            ext, vargs, _ = FORMAT_PRESETS["h264"]
            master, poster = encode_video_master(
                arr, fps, audio, f"cs_{label}_{key}", ext, vargs)
            register(key, {"label": label, "index": match["index"],
                           "seed": 0, "kind": "video", "injected": True,
                           "has_audio": audio is not None,
                           "poster": poster, "preview": None,
                           "filename": master,
                           "subfolder": STORE_SUBFOLDER, "type": "output"})
            runstate.report(1, 0)
        return ([key],)


class CallsheetInjectAudio:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "audio": ("AUDIO",),
            "label": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("CS_ITEMS",)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "inject"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def inject(self, parsed, audio, label):
        match = _find_injected(parsed, label, "CallsheetInjectAudio",
                               "audio")
        wf = audio["waveform"].cpu().numpy()
        key = hashlib.sha1(b"injaud" + label.encode()
                           + wf.tobytes()).hexdigest()[:16]
        if key not in VARIATION_STORE:
            os.makedirs(STORE_DIR, exist_ok=True)
            ext, aargs = AUDIO_FORMAT_PRESETS["flac"]
            master, poster = encode_audio_master(
                audio, f"cs_{label}_{key}", ext, aargs)
            register(key, {"label": label, "index": match["index"],
                           "seed": 0, "kind": "audio", "injected": True,
                           "has_audio": True, "poster": poster,
                           "filename": master,
                           "subfolder": STORE_SUBFOLDER, "type": "output"})
            runstate.report(1, 0)
        return ([key],)