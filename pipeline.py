import os

from .config import MAX_REFS, MAX_AUDIO_REFS, MAX_VIDEO_REFS
from .media import (load_video_last_frame, load_video_frames,
                    load_store_audio_window, video_frame_info)
from .resolve import build_resolver, stale_hint
from .store import (STORE_DIR, VARIATION_STORE, variation_key,
                    load_store_image, load_store_audio)
from .trimspec import parse_spec, resolve_window
from . import runstate


def focus_closure(items, focus):
    """Focused labels plus their transitive dependencies."""
    by_label = {it["label"]: it for it in items}
    closure, stack = set(), [l for l in focus if l in by_label]
    while stack:
        l = stack.pop()
        if l in closure:
            continue
        closure.add(l)
        it = by_label[l]
        deps = list(it["refs"]) + list(it["audio_refs"])
        deps += [x for x, _ in it.get("video_refs", [])]
        if it.get("continue_frame"):
            deps.append(it["continue_frame"])
        if it.get("continue_video"):
            deps.append(it["continue_video"][0])
        stack.extend(d for d in deps if d in by_label)
    return closure


class CallsheetPipeline:
    """Selects this pipeline's items, resolves refs / continuations
    against current selections, and emits only jobs not already stored.
    Unresolvable dependencies defer to a later convergence pass; resolved
    dependencies of the WRONG media kind (or with trims that leave no
    frames) are hard errors.

    When the callsheet's focus list is non-empty, only focused labels and
    their transitive dependencies are emitted; other items are ON HOLD —
    intentionally skipped and NOT counted as deferred, so the convergence
    loop converges over the focused closure only.

    Unused ref slots and continuation outputs emit None — consuming a
    missing slot downstream fails loudly. Gate optional consumers with a
    CallsheetFlag node driving a lazy switch.

    Auto flags per item: the pipeline NAME, ref_1..ref_4,
    audio_ref_1..audio_ref_3, video_ref_1..video_ref_3,
    video_audio_ref_N (when that video ref has audio), continue_frame,
    continue_video, and continue_video_audio.

    allowed_flags (empty = no enforcement): flags this pipeline's graph
    supports; pipeline-name auto flags are exempt. uniform_flags: flags
    whose presence must be uniform within a pass. max_jobs_per_pass
    (0 = unlimited) bounds host-RAM accumulation per pass. after_N
    inputs force same-pass ordering (an optimization, not required)."""

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "pipeline_filter": ("STRING", {"default": ""}),
            "allowed_flags": ("STRING", {"default": "",
                                         "advanced": True}),
            "uniform_flags": ("STRING", {"default": "",
                                         "advanced": True}),
            "max_jobs_per_pass": ("INT", {"default": 0, "min": 0,
                                          "max": 256, "advanced": True}),
        }, "optional": {
            "after_1": ("CS_ITEMS",),
            "after_2": ("CS_ITEMS",),
            "after_3": ("CS_ITEMS",),
        }}

    RETURN_TYPES = ("STRING", "STRING", "INT", "INT", "INT", "INT", "INT",
                    "CS_JOB", "CS_FLAGS",
                    "IMAGE", "IMAGE", "IMAGE", "IMAGE",
                    "AUDIO", "AUDIO", "AUDIO",
                    "IMAGE",
                    "IMAGE", "IMAGE", "IMAGE",
                    "AUDIO", "AUDIO", "AUDIO",
                    "IMAGE", "AUDIO", "CS_REFS")
    RETURN_NAMES = ("prompt", "negative", "width", "height", "length",
                    "fps", "seed", "job", "flags",
                    "ref_1", "ref_2", "ref_3", "ref_4",
                    "audio_ref_1", "audio_ref_2", "audio_ref_3",
                    "continue_frame",
                    "video_ref_1", "video_ref_2", "video_ref_3",
                    "video_audio_ref_1", "video_audio_ref_2",
                    "video_audio_ref_3",
                    "continue_video", "continue_video_audio", "refs")
    OUTPUT_IS_LIST = tuple([True] * 26)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # store contents change between passes

    def run(self, parsed, pipeline_filter, allowed_flags, uniform_flags,
            max_jobs_per_pass, **after):
        parsed = parsed[0]
        pipeline_filter = pipeline_filter[0]
        allowed = {f.strip() for f in allowed_flags[0].split(",")
                   if f.strip()}
        uniform = [f.strip() for f in uniform_flags[0].split(",")
                   if f.strip()]
        budget = max_jobs_per_pass[0]   # 0 = unlimited
        items, selections = parsed["items"], parsed["selections"]
        focus = parsed.get("focus") or []
        closure = focus_closure(items, focus) if focus else None
        wanted = {p.strip() for p in pipeline_filter.split(",")
                  if p.strip()}
        deferred = 0
        capped = 0
        on_hold = 0
        hard_errors = []
        passno = runstate.current_pass()
        resolver = build_resolver(items, selections)

        def why(dep):
            return ("stale — its definition changed and it has not "
                    "been regenerated yet" if stale_hint(dep)
                    else "not generated yet")

        def rec_path(key):
            return os.path.join(STORE_DIR,
                                VARIATION_STORE[key]["filename"])

        # ------------------------------------------------------------------
        # Phase 1: resolve, validate, collect candidates (no media decode)
        # ------------------------------------------------------------------
        candidates = []
        for item in items:
            if item["injected"] or item["pipeline"] not in wanted:
                continue
            if closure is not None and item["label"] not in closure:
                on_hold += 1
                continue

            ref_keys = [resolver(r) for r in item["refs"]]
            aref_keys = [resolver(r) for r in item["audio_refs"]]
            cont_label = item.get("continue_frame")
            cont_key = resolver(cont_label) if cont_label else None

            vref_pairs, vref_unresolved = [], []
            for l, spec in item.get("video_refs", []):
                k = resolver(l)
                if k is None:
                    vref_unresolved.append(l)
                else:
                    vref_pairs.append([k, spec])

            cv = item.get("continue_video")
            cv_pair, cv_unresolved = None, None
            if cv:
                k = resolver(cv[0])
                if k is None:
                    cv_unresolved = cv[0]
                else:
                    cv_pair = [k, cv[1]]

            unresolved_deps = [d for d, k in
                               zip(item["refs"], ref_keys) if k is None]
            unresolved_deps += [d for d, k in
                                zip(item["audio_refs"], aref_keys)
                                if k is None]
            if cont_label and cont_key is None:
                unresolved_deps.append(cont_label)
            unresolved_deps += vref_unresolved
            if cv_unresolved:
                unresolved_deps.append(cv_unresolved)

            if unresolved_deps:
                detail = "; ".join(f"'{d}' {why(d)}"
                                   for d in unresolved_deps)
                print(f"[CallsheetPipeline pass {passno}] "
                      f"'{item['label']}': deferred ({detail})")
                deferred += 1
                continue

            # ---- kind checks: resolved-but-wrong is a HARD error ---------
            item_errors = []
            for d, k in zip(item["refs"], ref_keys):
                if VARIATION_STORE[k].get("kind", "image") != "image":
                    item_errors.append(
                        f"'{item['label']}': ref '{d}' resolved to a "
                        f"{VARIATION_STORE[k].get('kind')} variation — "
                        f"refs must be images")
            for d, k in zip(item["audio_refs"], aref_keys):
                if not VARIATION_STORE[k].get("has_audio"):
                    item_errors.append(
                        f"'{item['label']}': audio_ref '{d}' resolved to "
                        f"a variation with no audio track")
            cont_rec = VARIATION_STORE.get(cont_key) if cont_key else None
            if cont_rec and cont_rec.get("kind") not in ("video", "image"):
                item_errors.append(
                    f"'{item['label']}': continue_frame '{cont_label}' "
                    f"resolved to a {cont_rec.get('kind')} variation — "
                    f"must be video or image")

            vref_windows = []
            for (l, spec), (k, _) in zip(item.get("video_refs", []),
                                         vref_pairs):
                rec = VARIATION_STORE[k]
                if rec.get("kind") != "video":
                    item_errors.append(
                        f"'{item['label']}': video_ref '{l}' resolved to "
                        f"a {rec.get('kind')} variation — must be video")
                    continue
                n_frames, src_fps, _, _ = video_frame_info(rec_path(k))
                try:
                    win = resolve_window(parse_spec(spec),
                                         n_frames, src_fps)
                except ValueError as e:
                    item_errors.append(
                        f"'{item['label']}': video_ref '{l}': {e}")
                    continue
                vref_windows.append((k, win, src_fps))

            cv_window = None
            if cv_pair:
                rec = VARIATION_STORE[cv_pair[0]]
                if rec.get("kind") != "video":
                    item_errors.append(
                        f"'{item['label']}': continue_video "
                        f"'{cv[0]}' resolved to a {rec.get('kind')} "
                        f"variation — must be video")
                else:
                    n_frames, src_fps, _, _ = video_frame_info(
                        rec_path(cv_pair[0]))
                    try:
                        win = resolve_window(parse_spec(cv_pair[1]),
                                             n_frames, src_fps)
                        cv_window = (cv_pair[0], win, src_fps)
                    except ValueError as e:
                        item_errors.append(
                            f"'{item['label']}': continue_video: {e}")

            # ---- flags: pipeline name + user + auto ------------------------
            auto = [item["pipeline"]] if item["pipeline"] else []
            auto += [f"ref_{i + 1}" for i, k in enumerate(ref_keys) if k]
            auto += [f"audio_ref_{i + 1}"
                     for i, k in enumerate(aref_keys) if k]
            if cont_key:
                auto.append("continue_frame")
            auto += [f"video_ref_{i + 1}"
                     for i in range(len(vref_pairs))]
            auto += [f"video_audio_ref_{i + 1}"
                     for i, (k, _) in enumerate(vref_pairs)
                     if VARIATION_STORE[k].get("has_audio")]
            if cv_pair:
                auto.append("continue_video")
                if VARIATION_STORE[cv_pair[0]].get("has_audio"):
                    auto.append("continue_video_audio")
            eff_flags = list(dict.fromkeys(item["flags"] + auto))
            if allowed:
                bad = [f for f in eff_flags
                       if f not in allowed and f != item["pipeline"]]
                if bad:
                    item_errors.append(
                        f"'{item['label']}': flag(s) not supported by "
                        f"this pipeline: {', '.join(bad)} "
                        f"(allowed: {', '.join(sorted(allowed))})")

            if item_errors:
                hard_errors.extend(item_errors)
                continue

            pending = []
            for seed in item["seeds"]:
                key = variation_key(item, seed, ref_keys, aref_keys,
                                    cont_key, vref_pairs, cv_pair)
                if key not in VARIATION_STORE:
                    pending.append((seed, key))
            if not pending:
                continue

            signature = tuple(f in eff_flags for f in uniform)
            candidates.append({
                "item": item, "pending": pending, "flags": eff_flags,
                "ref_keys": ref_keys, "aref_keys": aref_keys,
                "cont_key": cont_key, "cont_rec": cont_rec,
                "vref_pairs": vref_pairs, "vref_windows": vref_windows,
                "cv_window": cv_window, "signature": signature})

        if hard_errors:
            # raised BEFORE report(): this node never triggers a requeue
            raise ValueError(
                "CallsheetPipeline configuration errors:\n  - "
                + "\n  - ".join(hard_errors))

        # ------------------------------------------------------------------
        # Phase 2: uniform_flags partitioning
        # ------------------------------------------------------------------
        grouped = 0
        if uniform and candidates:
            active = candidates[0]["signature"]
            kept = []
            for c in candidates:
                if c["signature"] == active:
                    kept.append(c)
                else:
                    n = len(c["pending"])
                    grouped += n
                    deferred += n
            if grouped:
                desc = ", ".join(
                    f"{f}={'on' if v else 'off'}"
                    for f, v in zip(uniform, active))
                print(f"[CallsheetPipeline pass {passno}] uniform_flags: "
                      f"emitting group ({desc}); {grouped} job(s) from "
                      f"other groups deferred to a later pass")
            candidates = kept

        # ------------------------------------------------------------------
        # Phase 3: emit (budget check before decoding heavy inputs)
        # ------------------------------------------------------------------
        cols = {n: [] for n in self.RETURN_NAMES}
        for c in candidates:
            item = c["item"]
            pending = c["pending"]

            if budget:
                room = budget - len(cols["job"])
                if room <= 0:
                    capped += len(pending)
                    deferred += len(pending)
                    continue
                if len(pending) > room:
                    capped += len(pending) - room
                    deferred += len(pending) - room
                    pending = pending[:room]

            cont_rec = c["cont_rec"]
            if cont_rec is not None:
                cont_frame = (load_video_last_frame(cont_rec)
                              if cont_rec.get("kind") == "video"
                              else load_store_image(cont_rec))
            else:
                cont_frame = None

            ref_imgs = [load_store_image(VARIATION_STORE[k])
                        for k in c["ref_keys"]]
            while len(ref_imgs) < MAX_REFS:
                ref_imgs.append(None)    # loud failure if consumed
            aref_audio = [load_store_audio(VARIATION_STORE[k])
                          for k in c["aref_keys"]]
            while len(aref_audio) < MAX_AUDIO_REFS:
                aref_audio.append(None)

            vref_frames, vref_audio = [], []
            for k, (sf, ef), src_fps in c["vref_windows"]:
                rec = VARIATION_STORE[k]
                vref_frames.append(load_video_frames(rec, (sf, ef)))
                vref_audio.append(
                    load_store_audio_window(rec, sf / src_fps,
                                            ef / src_fps)
                    if rec.get("has_audio") else None)
            while len(vref_frames) < MAX_VIDEO_REFS:
                vref_frames.append(None)
                vref_audio.append(None)

            cv_frames = cv_audio = None
            if c["cv_window"]:
                k, (sf, ef), src_fps = c["cv_window"]
                rec = VARIATION_STORE[k]
                cv_frames = load_video_frames(rec, (sf, ef))
                if rec.get("has_audio"):
                    cv_audio = load_store_audio_window(
                        rec, sf / src_fps, ef / src_fps)

            bundle = {"label": item["label"],
                      "image_keys": list(c["ref_keys"]),
                      "image_names": list(item["refs"]),
                      "audio_keys": list(c["aref_keys"]),
                      "audio_names": list(item["audio_refs"]),
                      "video_keys": [p[0] for p in c["vref_pairs"]],
                      "video_names": [l for l, _
                                      in item.get("video_refs", [])],
                      "continue_key": c["cont_key"],
                      "continue_video_key":
                          c["cv_window"][0] if c["cv_window"] else None}

            for seed, key in pending:
                cols["prompt"].append(item["prompt"])
                cols["negative"].append(item["negative"])
                cols["width"].append(item["width"])
                cols["height"].append(item["height"])
                cols["length"].append(item["length"])
                cols["fps"].append(item["fps"])
                cols["seed"].append(seed)
                cols["job"].append({"key": key, "label": item["label"],
                                    "index": item["index"], "seed": seed,
                                    "kind": item.get("type")})
                cols["flags"].append(c["flags"])
                for i in range(MAX_REFS):
                    cols[f"ref_{i + 1}"].append(ref_imgs[i])
                for i in range(MAX_AUDIO_REFS):
                    cols[f"audio_ref_{i + 1}"].append(aref_audio[i])
                cols["continue_frame"].append(cont_frame)
                for i in range(MAX_VIDEO_REFS):
                    cols[f"video_ref_{i + 1}"].append(vref_frames[i])
                    cols[f"video_audio_ref_{i + 1}"].append(vref_audio[i])
                cols["continue_video"].append(cv_frames)
                cols["continue_video_audio"].append(cv_audio)
                cols["refs"].append(bundle)

        msg = (f"[CallsheetPipeline pass {passno}] '{pipeline_filter}': "
               f"{len(cols['job'])} job(s), {deferred} deferred")
        details = []
        if on_hold:
            details.append(f"{on_hold} on hold by focus")
        if grouped:
            details.append(f"{grouped} by uniform_flags")
        if capped:
            details.append(f"{capped} by max_jobs_per_pass")
        if details:
            msg += f" ({', '.join(details)})"
        print(msg)
        runstate.report(len(cols["job"]), deferred)
        return tuple(cols[n] for n in self.RETURN_NAMES)