import json

from .config import MAX_RESOLUTION
from .grammar import parse_and_validate
from .resolve import collect_candidates
from . import runstate


def _load_json(s, name, expect):
    try:
        obj = json.loads(s or ("[]" if expect is list else "{}"))
        assert isinstance(obj, expect)
        return obj
    except (json.JSONDecodeError, AssertionError):
        raise ValueError(f"{name} is not a JSON "
                            f"{'list' if expect is list else 'object'}")


def _build_pipeline_candidates(pipelines, items, selected, focus_list):
    # ------------------------------------------------------------------
    # Phase 1: resolve, validate, collect candidates (no media decode)
    # ------------------------------------------------------------------

    all_candidates, pipelines_on_hold, pipelines_deferred, hard_errors = collect_candidates(
        items, selected, focus_list)

    if hard_errors:
        # raised BEFORE report(): to never trigger a requeue
        raise ValueError(
            "CallsheetTextInput configuration errors:\n  - "
            + "\n  - ".join(hard_errors))

    passno = runstate.current_pass()
    print(f"[Callsheet pass {passno}] {len(all_candidates)} candidates, {sum(pipelines_deferred.values())} deferred")

    hard_errors = []
    for pipeline_name in pipelines:
        pipeline = pipelines[pipeline_name]
        allowed = {f for f in pipeline["allowed_flags"]}
        uniform = pipeline["uniform_flags"]
        budget = pipeline["max_jobs_per_pass"]
        wanted = {task for task in pipeline["tasks"]} # TODO: redundant?

        candidates = []
        for c in all_candidates:
            item = c["item"]
            if item["pipeline"] not in wanted:
                continue

            eff_flags = c["flags"]
            if allowed:
                bad = [f for f in eff_flags
                        if f not in allowed and f != item["pipeline"]]
                if bad:
                    hard_errors.append(
                        f"'{item['label']}': flag(s) not supported by "
                        f"this pipeline: {', '.join(bad)} "
                        f"(allowed: {', '.join(sorted(allowed))})")
                    continue
            signature = tuple(f in eff_flags for f in uniform)
            c["signature"] = signature

            candidates.append(c)

        if hard_errors:
            # raised BEFORE report(): this node never triggers a requeue
            raise ValueError(
                "CallsheetTextInput configuration errors:\n  - "
                + "\n  - ".join(hard_errors))

        deferred = 0
        if pipelines_deferred:
            for p in wanted:
                if p in pipelines_deferred:
                    deferred += pipelines_deferred[p]

        # for logging
        on_hold = 0
        if pipelines_on_hold:
            for p in wanted:
                if p in pipelines_on_hold:
                    on_hold += pipelines_on_hold[p]

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
        # Phase 3: budget check before decoding heavy inputs
        # ------------------------------------------------------------------
        capped = 0
        jobs = 0
        kept = []
        for c in candidates:
            pending = c["pending"]
            if budget:
                room = budget - jobs
                if room <= 0:
                    capped += len(pending)
                    deferred += len(pending)
                    print(f"[Callsheet pass {passno}] "
                            f"'{c['item']['label']}': deferred (max_jobs_per_pass)")
                    continue
                if len(pending) > room:
                    capped += len(pending) - room
                    deferred += len(pending) - room
                    pending = pending[:room]
                    c["pending"] = pending
                    print(f"[Callsheet pass {passno}] "
                            f"'{c['item']['label']}': partial deferral (max_jobs_per_pass)")
            kept.append(c)
            jobs += len(pending)
        candidates = kept

        for c in candidates:
            print(f"[Callsheet pass {passno}] "
                    f"'{c['item']['label']}': candidate {len(c["pending"])} job(s)")
        pipeline["candidates"] = candidates
        pipeline["deferred"] = deferred

        msg = (f"[CallsheetTextInput pass {passno}] '{pipeline_name}': "
            f"{jobs} job(s), {deferred} deferred")
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


class CallsheetTextInput:
    """Parses and validates the callsheet text once. Owns the UI-mutable
    state (variation_requests, selections, focus) and configures the
    auto-continue convergence loop.

    focus: a JSON list of labels. When non-empty, pipelines only emit
    jobs for the focused labels plus their transitive dependencies —
    everything else is put on hold (not deferred), so you can iterate on
    one asset's variations without regenerating its dependents. Clear it
    (or use the collector's focus controls) to resume full generation."""

    def __init__(self):
        self._last_text = None
        self._last_allowed_pipelines = None
        self._last_pipelines = None
        self._last_requests = None
        self._last_items = []

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": ""}),
            "allowed_pipelines": ("STRING", {"default": "", "multiline": True,
                                             "advanced": True}),
            "strict": ("BOOLEAN", {"default": False, "advanced": True}),
            "auto_continue": ("BOOLEAN", {"default": True,
                                          "advanced": True}),
            "max_passes": ("INT", {"default": 16, "min": 1, "max": 64,
                                   "advanced": True}),
            "base_seed": ("INT", {"default": 0, "min": 0,
                                  "max": 0xFFFFFFFFFFFFFFFF,
                                  "advanced": True}),
            "fallback_width": ("INT", {"default": 1024, "min": 16,
                                       "max": MAX_RESOLUTION, "step": 8,
                                       "advanced": True}),
            "fallback_height": ("INT", {"default": 1024, "min": 16,
                                        "max": MAX_RESOLUTION, "step": 8,
                                        "advanced": True}),
            "fallback_fps": ("INT", {"default": 24, "min": 1, "max": 120,
                                     "advanced": True}),
            "variation_requests": ("STRING", {"default": "{}",
                                              "multiline": True,
                                              "advanced": True}),
            "selections": ("STRING", {"default": "{}", "multiline": True,
                                      "advanced": True}),
            "focus": ("STRING", {"default": "[]", "multiline": True,
                                 "advanced": True}),
        }}

    RETURN_TYPES = ("CS_PARSED",)
    RETURN_NAMES = ("parsed",)
    FUNCTION = "parse"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def parse(self, text, allowed_pipelines, strict, auto_continue,
              max_passes, base_seed, fallback_width, fallback_height,
              fallback_fps, variation_requests, selections, focus):
        runstate.configure(auto_continue, max_passes)

        # parse items or use cached items
        is_text_new = self._last_text != text
        is_pipelines_new = self._last_allowed_pipelines != allowed_pipelines
        is_requests_new = self._last_requests != variation_requests
        print(f"[Callsheet] text {is_text_new} pipelines {is_pipelines_new} requests {is_requests_new}")

        # cached pipelines
        if is_pipelines_new:
            pipelines = _load_json(allowed_pipelines, "allowed_pipelines", dict)
            self._last_allowed_pipelines = allowed_pipelines
            self._last_pipelines = pipelines
        else:
            pipelines = self._last_pipelines

        # cached items
        if is_text_new or is_pipelines_new or is_requests_new:
            requests = _load_json(variation_requests, "variation_requests", dict)
            items = parse_and_validate(
                text, pipelines, strict, base_seed,
                {"width": str(fallback_width), "height": str(fallback_height),
                "fps": str(fallback_fps)},
                requests)
            self._last_text = text
            self._last_requests = variation_requests
            self._last_items = items
        else:
            items = self._last_items

        known = {it["label"] for it in items}
        focus_list = _load_json(focus, "focus", list)
        stale_focus = [l for l in focus_list if l not in known]
        if stale_focus:
            print(f"[CallsheetTextInput] focus contains unknown label(s) "
                  f"(ignored): {', '.join(stale_focus)}")
        focus_list = [l for l in focus_list if l in known]

        selected = _load_json(selections, "selections", dict)

        _build_pipeline_candidates(pipelines, items, selected, focus_list)

        return ({"items": items, "selections": selected,
                 "focus": focus_list, "pipelines": pipelines},)


class CallsheetTextInputB:
    """Parses and validates the callsheet text once. Owns the UI-mutable
    state and configures the auto-continue convergence loop.

    focus: a JSON list of labels. When non-empty, pipelines only emit
    jobs for the focused labels plus their transitive dependencies —
    everything else is put on hold (not deferred), so you can iterate on
    one asset's variations without regenerating its dependents. Clear it
    (or use the collector's focus controls) to resume full generation."""

    def __init__(self):
        self._last_text = None
        self._last_pipeline_specs = None
        self._last_pipelines = None
        self._last_state = None
        self._last_items = []

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": ""}),
            "strict": ("BOOLEAN", {"default": False, "advanced": True}),
            "auto_continue": ("BOOLEAN", {"default": True,
                                          "advanced": True}),
            "max_passes": ("INT", {"default": 16, "min": 1, "max": 64,
                                   "advanced": True}),
            "max_jobs_per_pass": ("INT", {"default": 0, "min": 0, "max": 64,
                                   "advanced": True}),
            "base_seed": ("INT", {"default": 0, "min": 0,
                                  "max": 0xFFFFFFFFFFFFFFFF,
                                  "advanced": True}),
            "fallback_width": ("INT", {"default": 1024, "min": 16,
                                       "max": MAX_RESOLUTION, "step": 8,
                                       "advanced": True}),
            "fallback_height": ("INT", {"default": 1024, "min": 16,
                                        "max": MAX_RESOLUTION, "step": 8,
                                        "advanced": True}),
            "fallback_fps": ("INT", {"default": 24, "min": 1, "max": 120,
                                     "advanced": True}),
            "pipeline_specs": ("STRING", {"default": "", "multiline": True,
                                             "advanced": True}),
            "state": ("STRING", {"default": "", "multiline": True,
                                 "advanced": True}),
        }}

    RETURN_TYPES = ("CS_PARSED",)
    RETURN_NAMES = ("parsed",)
    FUNCTION = "parse"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def parse(self, text, strict, auto_continue, max_passes, max_jobs_per_pass,
              base_seed, fallback_width, fallback_height, fallback_fps,
              pipeline_specs, state):
        runstate.configure(auto_continue, max_passes)

        # parse items or use cached items
        is_text_new = self._last_text != text
        is_pipelines_new = self._last_pipeline_specs != pipeline_specs
        is_state_new = self._last_state != state
        print(f"[Callsheet] text {is_text_new} pipelines {is_pipelines_new} state {is_state_new}")

        # cached pipelines
        if is_pipelines_new:
            pipelines = _load_json(pipeline_specs, "pipeline_specs", dict)
            self._last_pipeline_specs = pipeline_specs
            self._last_pipelines = pipelines
        else:
            pipelines = self._last_pipelines

        wf_state = _load_json(state, "state", dict)
        if not "variation_requests" in wf_state:
            wf_state["variation_requests"] = {}
        if not "selections" in wf_state:
            wf_state["selections"] = {}
        if not "focus" in wf_state:
            wf_state["focus"] = []

        # cached items
        if is_text_new or is_pipelines_new or is_state_new:
            items = parse_and_validate(
                text, pipelines, strict, base_seed,
                {"width": str(fallback_width), "height": str(fallback_height),
                "fps": str(fallback_fps)},
                wf_state["variation_requests"])
            self._last_text = text
            self._last_state = state
            self._last_items = items
        else:
            items = self._last_items

        known = {it["label"] for it in items}
        focus_list = wf_state["focus"]
        stale_focus = [l for l in focus_list if l not in known]
        if stale_focus:
            print(f"[CallsheetTextInput] focus contains unknown label(s) "
                  f"(ignored): {', '.join(stale_focus)}")
        focus_list = [l for l in focus_list if l in known]

        selected = wf_state["selections"]

        _build_pipeline_candidates(pipelines, items, selected, focus_list)

        return ({"items": items, "selections": selected,
                 "focus": focus_list, "pipelines": pipelines},)
