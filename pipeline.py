import os
from collections import defaultdict

from .config import MAX_REFS, MAX_AUDIO_REFS, MAX_VIDEO_REFS
from .media import (load_video_last_frame, load_video_frames,
                    load_store_audio_window, video_frame_info)
from .resolve import collect_candidates
from .store import (VARIATION_STORE, load_store_image, load_store_audio)
from .trimspec import parse_spec, resolve_window
from . import runstate


def _pipeline_build_jobs(candidates):
    jobs = []
    for c in candidates:
        item = c["item"]
        pending = c["pending"]

        cont_rec = c["cont_rec"]
        if cont_rec is not None:
            cont_frame = (load_video_last_frame(cont_rec)
                            if cont_rec.get("kind") == "video"
                            else load_store_image(cont_rec))
        else:
            cont_frame = None

        tgt_rec = c["tgt_rec"]
        if tgt_rec is not None:
            tgt_frame = (load_video_first_frame(tgt_rec)
                            if tgt_rec.get("kind") == "video"
                            else load_store_image(tgt_rec))
        else:
            tgt_frame = None

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

        tv_frames = tv_audio = None
        if c["tv_window"]:
            k, (sf, ef), src_fps = c["tv_window"]
            rec = VARIATION_STORE[k]
            tv_frames = load_video_frames(rec, (sf, ef))
            if rec.get("has_audio"):
                tv_audio = load_store_audio_window(
                    rec, sf / src_fps, ef / src_fps)

        refs = {}
        for i in range(MAX_REFS):
            refs[f"ref_{i}"] = ref_imgs[i]
        for i in range(MAX_AUDIO_REFS):
            refs[f"audio_ref_{i}"] = aref_audio[i]
        for i in range(MAX_VIDEO_REFS):
            refs[f"video_ref_{i}"] = vref_frames[i]
            refs[f"video_audio_ref_{i}"] = vref_audio[i]
        refs["continue_frame"] = cont_frame
        refs["continue_video"] = cv_frames
        refs["continue_video_audio"] = cv_audio
        refs["target_frame"] = tgt_frame
        refs["target_video"] = tv_frames
        refs["target_video_audio"] = tv_audio

        for seed, key in pending:
            job = {}
            job["prompt"] = item["prompt"]
            job["negative"] = item["negative"]
            job["width"] = item["width"]
            job["height"] = item["height"]
            job["length"] = item["length"]
            job["fps"] = item["fps"]
            job["seed"] = seed
            job["job"] = {"key": key, "label": item["label"],
                                "index": item["index"], "seed": seed,
                                "kind": item.get("type")}
            job["flags"] = c["flags"]
            job["refs"] = refs
            jobs.append(job)

    return jobs


def _pipeline_build_jobs_for_slots(candidates, slot_names):
    jobs = _pipeline_build_jobs(candidates)
    out = {n: [] for n in slot_names}
    for job in jobs:
        for s in slot_names:
            if s in job:
                out[s].append(job[s])
            else:
                out[s].append(job['refs'][s])
    return out


class CallsheetPipelineBasic:
    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "pipeline": ("STRING", {"default": ""}),
        }, "optional": {
            "after_1": ("CS_ITEMS",),
            "after_2": ("CS_ITEMS",),
            "after_3": ("CS_ITEMS",),
        }}

    RETURN_TYPES = ("CS_JOB", "STRING", "STRING", "INT", "INT", "INT",
                    "INT", "INT", "CS_FLAGS", "CS_REFS")
    RETURN_NAMES = ("job", "prompt", "negative", "width", "height", "length",
                    "fps", "seed", "flags", "refs")
    OUTPUT_IS_LIST = tuple([True] * 29)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # store contents change between passes

    def run(self, parsed, pipeline, **after):
        print(f"len(parsed) {len(parsed)}")
        parsed = parsed[0]
        pipeline_name = pipeline[0]

        pipelines = parsed.get("pipelines")
        if not pipeline_name in pipelines:
            raise ValueError(
                f"CallsheetPipeline configuration errors:\n  - '{pipeline_name}' not in spec!")

        print("[CallsheetPipeline] using pipeline spec")
        pipeline = pipelines[pipeline_name]

        candidates = pipeline["candidates"]

        # ------------------------------------------------------------------
        # Phase 4: decode inputs, emit
        # ------------------------------------------------------------------
        cols = _pipeline_build_jobs_for_slots(candidates, self.RETURN_NAMES)

        passno = runstate.current_pass()
        deferred = pipeline["deferred"]
        msg = (f"[CallsheetPipeline pass {passno}] '{pipeline_name}': "
               f"{len(cols['job'])} job(s), {deferred} deferred")
        print(msg)
        runstate.report(len(cols["job"]), deferred)

        return tuple(cols[n] for n in self.RETURN_NAMES)


class CallsheetImageRefs:
    """expose image references"""
    # INPUT_IS_LIST = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "refs": ("CS_REFS",),
        }}

    RETURN_TYPES = tuple([f"IMAGE" for _ in range(MAX_REFS)])
    RETURN_NAMES = tuple([f"ref_{i}" for i in range(MAX_REFS)])
    # OUTPUT_IS_LIST = tuple([True] * MAX_REFS)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    def run(self, refs):
        return tuple(refs[n] for n in self.RETURN_NAMES)


class CallsheetAudioRefs:
    """expose audio references"""
    # INPUT_IS_LIST = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "refs": ("CS_REFS",),
        }}

    RETURN_TYPES = tuple([f"AUDIO" for _ in range(MAX_AUDIO_REFS)])
    RETURN_NAMES = tuple([f"audio_ref_{i}" for i in range(MAX_AUDIO_REFS)])
    # OUTPUT_IS_LIST = tuple([True] * MAX_AUDIO_REFS)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    def run(self, refs):
        return tuple(refs[n] for n in self.RETURN_NAMES)


class CallsheetVideoImageRefs:
    """expose video image references"""
    # INPUT_IS_LIST = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "refs": ("CS_REFS",),
        }}

    RETURN_TYPES = tuple([f"IMAGE" for _ in range(MAX_VIDEO_REFS)])
    RETURN_NAMES = tuple([f"video_ref_{i}" for i in range(MAX_VIDEO_REFS)])
    # OUTPUT_IS_LIST = tuple([True] * MAX_VIDEO_REFS)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    def run(self, refs):
        return tuple(refs[n] for n in self.RETURN_NAMES)


class CallsheetVideoAudioRefs:
    """expose video audio references"""
    # INPUT_IS_LIST = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "refs": ("CS_REFS",),
        }}

    RETURN_TYPES = tuple([f"AUDIO" for _ in range(MAX_VIDEO_REFS)])
    RETURN_NAMES = tuple([f"video_auido_ref_{i}" for i in range(MAX_VIDEO_REFS)])
    # OUTPUT_IS_LIST = tuple([True] * MAX_VIDEO_REFS)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    def run(self, refs):
        return tuple(refs[n] for n in self.RETURN_NAMES)


class CallsheetFirstLastRefs:
    """expose first/last references"""
    # INPUT_IS_LIST = True
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "refs": ("CS_REFS",),
        }}

    RETURN_TYPES = ("IMAGE","AUDIO","IMAGE","AUDIO")
    RETURN_NAMES = ("first_frames","first_audio","last_frames","last_audio")
    # OUTPUT_IS_LIST = (True, True, True, True)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    def run(self, refs):

        first_frames = refs["continue_video"]
        if first_frames:
            first_audio = refs["continue_video_audio"]
        else:
            first_frames = refs["continue_frame"]
            first_audio = None
        
        last_frames = refs["target_video"]
        if last_frames:
            last_audio = refs["target_video_audio"]
        else:
            last_frames = refs["target_frame"]
            last_audio = None

        return (first_frames, first_audio, last_frames, last_audio)


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
            "max_jobs_per_pass": ("INT", {"default": 4, "min": 0,
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
                    "IMAGE", "AUDIO", "CS_REFS",
                    "IMAGE", "IMAGE", "AUDIO")
    RETURN_NAMES = ("prompt", "negative", "width", "height", "length",
                    "fps", "seed", "job", "flags",
                    "ref_0", "ref_1", "ref_2", "ref_3",
                    "audio_ref_0", "audio_ref_1", "audio_ref_2",
                    "continue_frame",
                    "video_ref_0", "video_ref_1", "video_ref_2",
                    "video_audio_ref_0", "video_audio_ref_1",
                    "video_audio_ref_2",
                    "continue_video", "continue_video_audio", "refs",
                    "target_frame", "target_video", "target_video_audio")
    OUTPUT_IS_LIST = tuple([True] * 29)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # store contents change between passes

    def run(self, parsed, pipeline_filter, allowed_flags, uniform_flags,
            max_jobs_per_pass, **after):
        print(f"len(parsed) {len(parsed)}")
        parsed = parsed[0]
        pipeline_filter = pipeline_filter[0]

        pipelines = parsed.get("pipelines")
        if not pipeline_filter in pipelines:
            raise ValueError(
                "CallsheetPipeline configuration errors:\n  - No Pipeline Spec!")
        print("[CallsheetPipeline] using parsed pipeline spec")
        pipeline = pipelines[pipeline_filter]

        candidates = pipeline["candidates"]

        # ------------------------------------------------------------------
        # Phase 4: decode inputs, emit
        # ------------------------------------------------------------------
        cols = _pipeline_build_jobs_for_slots(candidates, self.RETURN_NAMES)

        passno = runstate.current_pass()
        deferred = pipeline["deferred"]
        msg = (f"[CallsheetPipeline pass {passno}] '{pipeline_filter}': "
               f"{len(cols['job'])} job(s), {deferred} deferred")
        print(msg)
        runstate.report(len(cols["job"]), deferred)

        return tuple(cols[n] for n in self.RETURN_NAMES)