import os

from .config import MAX_RESOLUTION, MAX_REFS, MAX_AUDIO_REFS, MAX_VIDEO_REFS
from . import runstate


def _map_jobs_to_slots(jobs, slot_names):
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

    RETURN_TYPES = ("CS_JOB", "INT", "STRING", "STRING", "INT", "INT", "INT",
                    "INT", "CS_FLAGS", "CS_REFS")
    RETURN_NAMES = ("job", "fps", "prompt", "negative", "width", "height",
                    "length", "seed", "flags", "refs")
    OUTPUT_IS_LIST = tuple([True] * 29)
    FUNCTION = "run"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")   # store contents change between passes

    def run(self, parsed, pipeline, **after):
        parsed = parsed[0]
        pipeline_name = pipeline[0]

        pipelines = parsed.get("pipelines")
        if not pipeline_name in pipelines:
            raise ValueError(
                f"CallsheetPipeline configuration errors:\n  - '{pipeline_name}' not in spec!")
        pipeline = pipelines[pipeline_name]

        # ------------------------------------------------------------------
        # Phase 4: decode inputs, emit
        # ------------------------------------------------------------------
        jobs = pipeline["jobs"]
        cols = _map_jobs_to_slots(jobs, self.RETURN_NAMES)

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
    RETURN_NAMES = tuple([f"video_audio_ref_{i}" for i in range(MAX_VIDEO_REFS)])
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
        if not first_frames is None:
            first_audio = refs["continue_video_audio"]
        else:
            first_frames = refs["continue_frame"]
            first_audio = None
        
        last_frames = refs["target_video"]
        if not last_frames is None:
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
        parsed = parsed[0]
        pipeline_filter = pipeline_filter[0]

        pipelines = parsed.get("pipelines")
        if not pipeline_filter in pipelines:
            raise ValueError(
                "CallsheetPipeline configuration errors:\n  - No Pipeline Spec!")
        pipeline = pipelines[pipeline_filter]

        # ------------------------------------------------------------------
        # Phase 4: decode inputs, emit
        # ------------------------------------------------------------------
        jobs = pipeline["jobs"]
        cols = _map_jobs_to_slots(jobs, self.RETURN_NAMES)

        passno = runstate.current_pass()
        deferred = pipeline["deferred"]
        msg = (f"[CallsheetPipeline pass {passno}] '{pipeline_filter}': "
               f"{len(cols['job'])} job(s), {deferred} deferred")
        print(msg)
        runstate.report(len(cols["job"]), deferred)

        return tuple(cols[n] for n in self.RETURN_NAMES)


class CallsheetPipelineTester:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "pipeline": ("STRING", {"default":""}),
            "prompt": ("STRING", {"default":"", "multiline": True}),
            "width": ("INT", {"default": 1024, "min": 1, "max": MAX_RESOLUTION}),
            "height": ("INT", {"default": 1024, "min": 1, "max": MAX_RESOLUTION}),
            "length": ("INT", {"default": 124, "min": 1, "max": MAX_RESOLUTION}),
            "fps": ("INT", {"default": 24, "min": 1, "max": 120}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF}),
            "flags": ("STRING", {"default":""}),
        }, "optional": {
            "ref_0": ("IMAGE",),
            "ref_1": ("IMAGE",),
            "ref_2": ("IMAGE",),
            "ref_3": ("IMAGE",),
            "ref_4": ("IMAGE",),
            "ref_5": ("IMAGE",),
            "ref_6": ("IMAGE",),
            "ref_7": ("IMAGE",),
            "audio_ref_0": ("AUDIO",),
            "audio_ref_1": ("AUDIO",),
            "audio_ref_2": ("AUDIO",),
            "audio_ref_3": ("AUDIO",),
            "video_ref_0": ("IMAGE",),
            "video_ref_1": ("IMAGE",),
            "video_ref_2": ("IMAGE",),
            "video_ref_3": ("IMAGE",),
            "video_audio_ref_0": ("AUDIO",),
            "video_audio_ref_1": ("AUDIO",),
            "video_audio_ref_2": ("AUDIO",),
            "video_audio_ref_3": ("AUDIO",),
            "continue_frame": ("IMAGE",),
            "continue_video": ("IMAGE",),
            "continue_video_audio": ("AUDIO",),
            "target_frame": ("IMAGE",),
            "target_video": ("IMAGE",),
            "target_video_audio": ("AUDIO",),
        }}

    RETURN_TYPES = ("CS_PARSED",)
    RETURN_NAMES = ("parsed",)
    FUNCTION = "test"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def test(self, pipeline, prompt, width, height, length, fps, seed, flags,
             ref_0 = None, ref_1 = None, ref_2 = None, ref_3 = None,
             ref_4 = None, ref_5 = None, ref_6 = None, ref_7 = None,
             audio_ref_0 = None, audio_ref_1 = None,
             audio_ref_2 = None, audio_ref_3 = None,
             video_ref_0 = None, video_ref_1 = None,
             video_ref_2 = None, video_ref_3 = None,
             video_audio_ref_0 = None, video_audio_ref_1 = None,
             video_audio_ref_2 = None, video_audio_ref_3 = None,
             continue_frame = None, continue_video = None, continue_video_audio = None,
             target_frame = None, target_video = None, target_video_audio = None ):
        _flags = [f.strip() for f in flags.split(",") if f.strip()]
        parsed = {
            'pipelines': {
                pipeline: {
                    'jobs':[{
                        'prompt': prompt,
                        'negative': '',
                        'width': width,
                        'height': height,
                        'length': length,
                        'fps': fps,
                        'seed': seed,
                        'job': {},
                        'flags': _flags,
                        'refs': {
                            'ref_0': ref_0,
                            'ref_1': ref_1,
                            'ref_2': ref_2,
                            'ref_3': ref_3,
                            'ref_4': ref_4,
                            'ref_5': ref_5,
                            'ref_6': ref_6,
                            'ref_7': ref_7,
                            'audio_ref_0': audio_ref_0,
                            'audio_ref_1': audio_ref_1,
                            'audio_ref_2': audio_ref_2,
                            'audio_ref_3': audio_ref_3,
                            'video_ref_0': video_ref_0,
                            'video_audio_ref_0': video_audio_ref_0,
                            'video_ref_1': video_ref_1,
                            'video_audio_ref_1': video_audio_ref_1,
                            'video_ref_2': video_ref_2,
                            'video_audio_ref_2': video_audio_ref_2,
                            'video_ref_3': video_ref_3,
                            'video_audio_ref_3': video_audio_ref_3,
                            'continue_frame': continue_frame,
                            'continue_video': continue_video,
                            'continue_video_audio': continue_video_audio,
                            'target_frame': target_frame,
                            'target_video': target_video,
                            'target_video_audio': target_video_audio
                        }
                    }],
                    'deferred': 0
                }
            }
        }
        return (parsed,)

