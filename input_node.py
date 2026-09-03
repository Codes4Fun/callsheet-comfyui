import json

from .config import MAX_RESOLUTION
from .grammar import parse_and_validate, parse_pipeline_types
from . import runstate


class CallsheetTextInput:
    """Parses and validates the callsheet text once. Owns the UI-mutable
    state (variation_requests, selections, focus) and configures the
    auto-continue convergence loop.

    focus: a JSON list of labels. When non-empty, pipelines only emit
    jobs for the focused labels plus their transitive dependencies —
    everything else is put on hold (not deferred), so you can iterate on
    one asset's variations without regenerating its dependents. Clear it
    (or use the collector's focus controls) to resume full generation."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": ""}),
            "allowed_pipelines": ("STRING", {"default": "",
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

    def parse(self, text, allowed_pipelines, strict, auto_continue,
              max_passes, base_seed, fallback_width, fallback_height,
              fallback_fps, variation_requests, selections, focus):
        runstate.configure(auto_continue, max_passes)

        pipeline_types = parse_pipeline_types(allowed_pipelines)

        def load_json(s, name, expect):
            try:
                obj = json.loads(s or ("[]" if expect is list else "{}"))
                assert isinstance(obj, expect)
                return obj
            except (json.JSONDecodeError, AssertionError):
                raise ValueError(f"{name} is not a JSON "
                                 f"{'list' if expect is list else 'object'}")

        requests = load_json(variation_requests, "variation_requests",
                             dict)
        selected = load_json(selections, "selections", dict)
        focus_list = load_json(focus, "focus", list)

        items = parse_and_validate(
            text, pipeline_types, strict, base_seed,
            {"width": str(fallback_width), "height": str(fallback_height),
             "fps": str(fallback_fps)},
            requests)

        known = {it["label"] for it in items}
        stale_focus = [l for l in focus_list if l not in known]
        if stale_focus:
            print(f"[CallsheetTextInput] focus contains unknown label(s) "
                  f"(ignored): {', '.join(stale_focus)}")
        focus_list = [l for l in focus_list if l in known]

        return ({"items": items, "selections": selected,
                 "focus": focus_list},)