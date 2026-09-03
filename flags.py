class CallsheetFlag:
    """Per-item flag test. Wire the pipeline's 'flags' output in and name
    a flag; outputs True for items carrying it. Auto flags exist for
    populated slots: ref_1..ref_4, audio_ref_1..audio_ref_3, and
    continue_frame. Feed the boolean into lazy switches to gate optional
    parts of a pipeline (turbo LoRA stacks, optional ref consumers).
    Convert to INT/FLOAT downstream with a MathExpression node."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "flags": ("CS_FLAGS",),
            "flag": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("present",)
    FUNCTION = "check"
    CATEGORY = "callsheet"

    def check(self, flags, flag):
        return (flag.strip() in flags,)