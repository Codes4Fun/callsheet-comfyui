import difflib
import math
import re

from .config import (ITEM_DELIM, HEADER_DELIM, MAX_REFS, MAX_AUDIO_REFS,
                     MAX_VIDEO_REFS)
from .trimspec import split_label_spec, validate_spec

# value may be empty: 'key:' clears a rolling default
_HEADER_LINE = re.compile(r"^\s*[A-Za-z_][\w-]*\s*:.*$")
_LABEL_OK = re.compile(r"^[\w][\w\- ]*$")
_FLAG_OK = re.compile(r"^[\w-]+$")
_SIZE_WXH = re.compile(r"^(\d+)\s*[xX]\s*(\d+)$")
_SIZE_MP = re.compile(
    r"^([\d.]+)\s*mp\s+(\d+)\s*:\s*(\d+)(?:\s+step\s+(\d+))?$", re.I)

PIPELINE_TYPES = ("image", "video", "audio")

KNOWN_KEYS = {"label", "pipeline", "size", "width", "height", "length",
              "fps", "seed", "negative", "ref", "audio_ref", "video_ref",
              "continue_frame", "continue_video", "source", "flags"}
# properties that make no sense in a rolling defaults block
ITEM_ONLY_KEYS = {"label", "ref", "audio_ref", "video_ref", "source",
                  "continue_frame", "continue_video"}
DEFAULTABLE_KEYS = KNOWN_KEYS - ITEM_ONLY_KEYS


def _looks_like_pure_headers(raw):
    lines = [l for l in raw.splitlines() if l.strip()]
    return bool(lines) and all(_HEADER_LINE.match(l) for l in lines)


def _split_blocks(text):
    blocks, current = [], []
    for line in text.splitlines():
        if line.strip() == ITEM_DELIM:
            blocks.append(current)
            current = []
        else:
            current.append(line)
    blocks.append(current)
    return ["\n".join(b).strip() for b in blocks if "\n".join(b).strip()]


def _parse_block(raw):
    lines = raw.splitlines()
    headers, body_start = {}, 0
    sep = next((i for i, l in enumerate(lines)
                if l.strip() == HEADER_DELIM), None)
    if sep is not None:
        for l in lines[:sep]:
            if ":" in l:
                k, _, v = l.partition(":")
                headers[k.strip().lower()] = v.strip()
        body_start = sep + 1
    # 'path' accepted as a deprecated alias for 'pipeline'
    if "path" in headers and "pipeline" not in headers:
        headers["pipeline"] = headers.pop("path")
    return headers, "\n".join(lines[body_start:]).strip()


def _check_keys(headers, allowed, tag, errors):
    for k in headers:
        if k not in allowed:
            sugg = difflib.get_close_matches(k, allowed, n=1)
            errors.append(
                f"{tag}: unknown property '{k}'"
                + (f" — did you mean '{sugg[0]}'?" if sugg else ""))


def _parse_size(val, tag, errors, default_step=64):
    m = _SIZE_WXH.match(val)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _SIZE_MP.match(val)
    if m:
        mp = float(m.group(1))
        a, b = int(m.group(2)), int(m.group(3))
        step = int(m.group(4)) if m.group(4) else default_step
        w = math.sqrt(mp * 1e6 * a / b)
        w = max(step, round(w / step) * step)
        h = max(step, round(w * b / a / step) * step)
        return int(w), int(h)
    errors.append(f"{tag}: bad size '{val}' (use WxH, or 'Nmp A:B', "
                  f"optionally 'step S')")
    return None


def _parse_length(val, fps, tag, errors):
    v = str(val).strip().lower()
    if v.endswith("s"):
        try:
            sec = float(v[:-1])
        except ValueError:
            errors.append(f"{tag}: bad length '{val}'")
            return 0
        if not fps:
            errors.append(f"{tag}: length in seconds requires an 'fps' "
                          f"header or default")
            return 0
        return max(1, round(sec * fps))
    try:
        return int(v)
    except ValueError:
        errors.append(f"{tag}: 'length' must be frames (int) or seconds "
                      f"like '5s'")
        return 0


def _parse_ref_list(headers, key, tag, errors):
    """Parses 'label[:spec], label[:spec], ...' into [[label, spec]]."""
    out = []
    for entry in headers.get(key, "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        label, spec = split_label_spec(entry)
        err = validate_spec(spec)
        if err:
            errors.append(f"{tag}: {key} '{entry}': {err}")
        out.append([label, spec])
    return out


def parse_pipeline_types(spec):
    """'name:type, name:type, ...' -> {name: type-or-None}."""
    pipeline_types = {}
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        name, _, t = tok.partition(":")
        name, t = name.strip(), t.strip().lower()
        if t and t not in PIPELINE_TYPES:
            raise ValueError(
                f"allowed_pipelines: unknown type '{t}' for '{name}' "
                f"(use {', '.join(PIPELINE_TYPES)})")
        pipeline_types[name] = t or None
    return pipeline_types


def parse_and_validate(text, pipeline_types, strict, base_seed,
                       widget_defaults, variation_requests):
    """pipeline_types: dict name -> 'image'|'video'|'audio'|None.
    Strict mode requires every referenced pipeline to be declared AND
    typed; typed pipelines get property/ref-kind enforcement here. Items
    in untyped pipelines fall back to lazy validation at runtime."""
    blocks = _split_blocks(text)
    errors, defaults, items = [], {}, []
    seen_labels = set()

    index = 0
    for bi, raw in enumerate(blocks):
        headers, prompt = _parse_block(raw)

        # ---- rolling defaults block (headers, no body) --------------------
        is_defaults = (headers and not prompt) or (
            not headers and _looks_like_pure_headers(raw))
        if is_defaults:
            if not headers:   # forgiving: missing '---'
                headers, _ = _parse_block(raw + "\n" + HEADER_DELIM)
            dtag = f"defaults block {bi + 1}"
            for bad in ITEM_ONLY_KEYS:
                if bad in headers:
                    errors.append(f"{dtag}: '{bad}' cannot be defaulted")
                    headers.pop(bad)
            _check_keys(headers, DEFAULTABLE_KEYS, dtag, errors)
            for k, v in headers.items():
                if v == "":
                    defaults.pop(k, None)   # 'key:' clears the default
                else:
                    defaults[k] = v
            continue

        # ---- regular item ---------------------------------------------------
        label = headers.get("label", f"item_{index}")
        tag = f"item {index} ('{label}')"

        _check_keys(headers, KNOWN_KEYS, tag, errors)

        if (not headers and raw.splitlines()
                and _HEADER_LINE.match(raw.splitlines()[0])):
            errors.append(f"{tag}: first line looks like a header but the "
                          f"block has no '---' separator")
        if not prompt:
            errors.append(f"{tag}: empty prompt body")
        if not _LABEL_OK.match(label):
            errors.append(f"{tag}: invalid label '{label}'")
        if label in seen_labels:
            errors.append(f"{tag}: duplicate label '{label}'")
        seen_labels.add(label)

        injected = headers.get(
            "source", defaults.get("source", "")).lower() == "injected"
        refs = [r.strip() for r in headers.get("ref", "").split(",")
                if r.strip()]
        audio_refs = [r.strip() for r in
                      headers.get("audio_ref", "").split(",") if r.strip()]
        video_refs = _parse_ref_list(headers, "video_ref", tag, errors)

        cont = headers.get("continue_frame", "").strip() or None
        if cont and "," in cont:
            errors.append(f"{tag}: 'continue_frame' takes a single label")
            cont = None

        cont_video = None
        cv = headers.get("continue_video", "").strip()
        if cv:
            if "," in cv:
                errors.append(f"{tag}: 'continue_video' takes a single "
                              f"entry")
            else:
                cv_label, cv_spec = split_label_spec(cv)
                err = validate_spec(cv_spec)
                if err:
                    errors.append(f"{tag}: continue_video '{cv}': {err}")
                cont_video = [cv_label, cv_spec]
        if cont and cont_video:
            errors.append(f"{tag}: use either 'continue_frame' or "
                          f"'continue_video', not both")

        # ---- pipeline + declared type ---------------------------------------
        def resolve(key):
            if key in headers:
                return headers[key]
            if key in defaults:
                return defaults[key]
            if not strict and key in widget_defaults:
                return widget_defaults[key]
            errors.append(f"{tag}: missing '{key}' and no default provided")
            return None

        pipeline = (headers.get("pipeline", defaults.get("pipeline"))
                    if injected else resolve("pipeline"))
        ptype = pipeline_types.get(pipeline) if pipeline else None
        if pipeline is not None:
            if strict:
                if pipeline not in pipeline_types:
                    errors.append(
                        f"{tag}: pipeline '{pipeline}' is not declared in "
                        f"allowed_pipelines (strict mode requires "
                        f"'name:type' declarations)")
                elif ptype is None:
                    errors.append(
                        f"{tag}: pipeline '{pipeline}' is declared without "
                        f"a type (strict mode requires 'name:type')")
            elif pipeline_types and pipeline not in pipeline_types:
                errors.append(f"{tag}: unknown pipeline '{pipeline}' "
                              f"(declared: "
                              f"{', '.join(sorted(pipeline_types))})")

        # ---- flags ------------------------------------------------------------
        flags_raw = headers.get("flags", defaults.get("flags", ""))
        flags = [f.strip() for f in flags_raw.split(",") if f.strip()]
        for f in flags:
            if not _FLAG_OK.match(f):
                errors.append(f"{tag}: invalid flag '{f}'")

        if injected:
            if refs or audio_refs or video_refs or cont or cont_video:
                errors.append(f"{tag}: injected items cannot have refs "
                              f"or continuations")
            if label in variation_requests:
                errors.append(f"{tag}: cannot request variations for an "
                              f"injected item")
            items.append({"index": index, "label": label,
                          "pipeline": pipeline, "type": ptype,
                          "prompt": prompt, "negative": "",
                          "width": 0, "height": 0, "length": 0, "fps": 0,
                          "flags": flags, "seeds": [],
                          "injected": True, "refs": [], "audio_refs": [],
                          "video_refs": [], "continue_frame": None,
                          "continue_video": None})
            index += 1
            continue

        # ---- dimensions (required unless audio-typed) --------------------------
        def int_prop(key, required):
            if key in headers:
                val = headers[key]
            elif key in defaults:
                val = defaults[key]
            elif required:
                val = resolve(key)   # errors via resolve if truly missing
            else:
                return 0
            if val is None:
                return 0
            try:
                val = int(val)
                if val <= 0:
                    raise ValueError
                return val
            except (ValueError, TypeError):
                errors.append(f"{tag}: '{key}' must be a positive "
                              f"integer, got '{val}'")
                return 0

        dims_required = ptype != "audio"
        width = height = 0
        if "size" in headers:
            if "width" in headers or "height" in headers:
                errors.append(f"{tag}: use either 'size' or "
                              f"'width'/'height', not both")
            d = _parse_size(headers["size"], tag, errors)
            if d:
                width, height = d
        elif "width" in headers or "height" in headers:
            width = int_prop("width", dims_required)
            height = int_prop("height", dims_required)
        elif "size" in defaults:
            d = _parse_size(defaults["size"], tag, errors)
            if d:
                width, height = d
        else:
            width = int_prop("width", dims_required)
            height = int_prop("height", dims_required)

        # ---- fps and length (type-gated) ----------------------------------
        if ptype == "image":
            for k in ("length", "fps"):
                if k in headers:
                    errors.append(f"{tag}: '{k}' is not valid on an "
                                  f"image-typed pipeline")
            fps, length = 0, 0   # inherited defaults silently ignored
        else:
            fps_raw = headers.get("fps", defaults.get(
                "fps", None if strict else widget_defaults.get("fps")))
            fps = 0
            if fps_raw is not None:
                try:
                    fps = int(fps_raw)
                    if fps <= 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"{tag}: 'fps' must be a positive "
                                  f"integer")
                    fps = 0
            length = _parse_length(
                headers.get("length", defaults.get("length", "0")),
                fps, tag, errors)
            if ptype == "video":
                if length <= 0:
                    errors.append(f"{tag}: video items require a "
                                  f"positive 'length'")
                if fps <= 0:
                    errors.append(f"{tag}: video items require 'fps'")

        # ---- seed: plain base_seed, stable under reordering -----------------
        try:
            seed = int(headers.get("seed",
                                   defaults.get("seed", base_seed)))
        except ValueError:
            errors.append(f"{tag}: 'seed' must be an integer")
            seed = 0

        extra = variation_requests.get(label, [])
        if not (isinstance(extra, list)
                and all(isinstance(s, int) for s in extra)):
            errors.append(f"{tag}: variation_requests entry must be a "
                          f"list of integer seeds")
            extra = []

        items.append({"index": index, "label": label, "pipeline": pipeline,
                      "type": ptype, "prompt": prompt,
                      "negative": headers.get(
                          "negative", defaults.get("negative", "")),
                      "width": width, "height": height,
                      "length": length, "fps": fps, "flags": flags,
                      "seeds": [seed] + extra,
                      "injected": False, "refs": refs,
                      "audio_refs": audio_refs, "video_refs": video_refs,
                      "continue_frame": cont,
                      "continue_video": cont_video})
        index += 1

    # ---- reference validation --------------------------------------------------
    by_label = {it["label"]: it for it in items}

    def edges(it):
        e = it["refs"] + it["audio_refs"] + [l for l, _ in it["video_refs"]]
        if it["continue_frame"]:
            e = e + [it["continue_frame"]]
        if it["continue_video"]:
            e = e + [it["continue_video"][0]]
        return e

    for it in items:
        if len(it["refs"]) > MAX_REFS:
            errors.append(f"'{it['label']}': too many refs "
                          f"(max {MAX_REFS})")
        if len(it["audio_refs"]) > MAX_AUDIO_REFS:
            errors.append(f"'{it['label']}': too many audio refs "
                          f"(max {MAX_AUDIO_REFS})")
        if len(it["video_refs"]) > MAX_VIDEO_REFS:
            errors.append(f"'{it['label']}': too many video refs "
                          f"(max {MAX_VIDEO_REFS})")
        for r in edges(it):
            if r not in by_label:
                errors.append(f"'{it['label']}': unknown ref '{r}'")
            elif r == it["label"]:
                errors.append(f"'{it['label']}': item references itself")

        # kind checks, when both ends are typed (lazy mode defers these
        # to the pipeline node, where they are hard errors)
        def target_type(r):
            return by_label.get(r, {}).get("type")

        for r in it["refs"]:
            t = target_type(r)
            if t and t != "image":
                errors.append(f"'{it['label']}': ref '{r}' must be an "
                              f"image item, but its pipeline is "
                              f"typed '{t}'")
        for r in it["audio_refs"]:
            t = target_type(r)
            if t and t not in ("audio", "video"):
                errors.append(f"'{it['label']}': audio_ref '{r}' must be "
                              f"an audio or video item, but its pipeline "
                              f"is typed '{t}'")
        for r, _ in it["video_refs"]:
            t = target_type(r)
            if t and t != "video":
                errors.append(f"'{it['label']}': video_ref '{r}' must be "
                              f"a video item, but its pipeline is "
                              f"typed '{t}'")
        if it["continue_frame"]:
            t = target_type(it["continue_frame"])
            if t and t not in ("video", "image"):
                errors.append(f"'{it['label']}': continue_frame "
                              f"'{it['continue_frame']}' must be a video "
                              f"or image item, but its pipeline is "
                              f"typed '{t}'")
        if it["continue_video"]:
            t = target_type(it["continue_video"][0])
            if t and t != "video":
                errors.append(f"'{it['label']}': continue_video "
                              f"'{it['continue_video'][0]}' must be a "
                              f"video item, but its pipeline is "
                              f"typed '{t}'")

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {l: WHITE for l in by_label}

    def visit(l, stack):
        color[l] = GRAY
        for r in edges(by_label[l]):
            if r not in by_label:
                continue
            if color[r] == GRAY:
                errors.append("reference cycle: "
                              + " -> ".join(stack + [l, r]))
            elif color[r] == WHITE:
                visit(r, stack + [l])
        color[l] = BLACK

    for l in list(by_label):
        if color[l] == WHITE:
            visit(l, [])

    if not items:
        errors.append("no items found in callsheet text")
    if errors:
        raise ValueError("Callsheet validation failed:\n  - "
                         + "\n  - ".join(errors))
    return items