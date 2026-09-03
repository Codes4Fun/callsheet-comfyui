import hashlib
import json
import os
import subprocess

import numpy as np
import torch
from PIL import Image

import folder_paths

from .config import STORE_SUBFOLDER, OLD_STORE_SUBFOLDER

STORE_DIR = os.path.join(folder_paths.get_output_directory(), STORE_SUBFOLDER)
MANIFEST = os.path.join(STORE_DIR, "manifest.json")

# ---- one-time migration from the pre-rename store --------------------------
_old_dir = os.path.join(folder_paths.get_output_directory(),
                        OLD_STORE_SUBFOLDER)
if os.path.isdir(_old_dir) and not os.path.isdir(STORE_DIR):
    os.rename(_old_dir, STORE_DIR)
    print(f"[callsheet] migrated store folder "
          f"'{OLD_STORE_SUBFOLDER}' -> '{STORE_SUBFOLDER}'")


def _load_store():
    try:
        with open(MANIFEST, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    kept = {}
    for k, v in data.items():
        if os.path.exists(os.path.join(STORE_DIR, v["filename"])):
            v["subfolder"] = STORE_SUBFOLDER
            kept[k] = v
    return kept


VARIATION_STORE = _load_store()


def save_manifest():
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(MANIFEST, "w") as f:
        json.dump(VARIATION_STORE, f, indent=1)


def register(key, rec):
    VARIATION_STORE[key] = rec
    save_manifest()


def variation_key(item, seed, ref_keys=(), audio_ref_keys=(),
                  cont_key=None, video_refs=(), cont_video=None):
    """Content-addressed identity of one variation: a hash of the FULL
    canonical item definition (everything except index) plus resolved
    dependency keys. video_refs is a sequence of [key, trim_spec] pairs
    and cont_video a [key, trim_spec] pair — trims change content, so
    they participate. New fields are included only when present, so keys
    for items not using them are unchanged."""
    definition = {
        "label": item["label"],
        "pipeline": item["pipeline"],
        "type": item.get("type"),
        "prompt": item["prompt"],
        "negative": item["negative"],
        "width": item["width"],
        "height": item["height"],
        "length": item["length"],
        "fps": item["fps"],
        "flags": sorted(item.get("flags", [])),
        "seed": seed,
        "refs": list(ref_keys),
        "audio_refs": list(audio_ref_keys),
        "continue": cont_key,
    }
    if video_refs:
        definition["video_refs"] = [list(p) for p in video_refs]
    if cont_video:
        definition["continue_video"] = list(cont_video)
    payload = json.dumps(definition, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def effective_selection(label, selections):
    """Display-side resolution only (collector browsing): explicit
    selection if valid, else newest stored variation of the label.
    Generation-side consumers use resolve.build_resolver instead."""
    key = selections.get(label)
    if (key and key in VARIATION_STORE
            and VARIATION_STORE[key]["label"] == label):
        return key
    recs = [k for k, r in VARIATION_STORE.items() if r["label"] == label]
    return recs[-1] if recs else None


def load_store_image(rec):
    img = Image.open(os.path.join(STORE_DIR, rec["filename"])).convert("RGB")
    a = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(a)[None,]


def load_store_audio(rec, sample_rate=None):
    """Decode a stored record's audio track. Preserves the NATIVE sample
    rate and channel count by default (no silent resampling); pass
    sample_rate to force a rate explicitly."""
    path = os.path.join(STORE_DIR, rec["filename"])
    info = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-print_format", "json", path],
        capture_output=True)
    try:
        stream = json.loads(info.stdout)["streams"][0]
        native_sr = int(stream["sample_rate"])
        channels = int(stream["channels"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        raise RuntimeError(f"could not probe audio stream of "
                           f"{rec['filename']}")

    sr = sample_rate or native_sr
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-f", "f32le",
           "-ac", str(channels)]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    cmd += ["-"]
    out = subprocess.run(cmd, capture_output=True)
    data = np.frombuffer(out.stdout, np.float32).reshape(-1, channels).T
    return {"waveform": torch.from_numpy(data.copy())[None,],
            "sample_rate": sr}


def unresolved(value):
    """True if a lazy input hasn't been evaluated yet. ComfyUI passes
    unresolved lazy links as a placeholder sequence containing None."""
    if value is None:
        return True
    try:
        return any(v is None for v in value)
    except TypeError:
        return False
