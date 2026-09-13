import torch
import torchaudio

from nodes import LoraLoader
import re


def _match_check(a, b, node_name):
    if a["sample_rate"] != b["sample_rate"]:
        raise ValueError(
            f"{node_name}: sample rates differ "
            f"({a['sample_rate']} vs {b['sample_rate']}) — use "
            f"Callsheet Audio Resample first")
    wa, wb = a["waveform"], b["waveform"]
    if wa.shape[0] != wb.shape[0] or wa.shape[1] != wb.shape[1]:
        raise ValueError(
            f"{node_name}: batch/channel shapes differ "
            f"({tuple(wa.shape[:2])} vs {tuple(wb.shape[:2])})")
    return wa, wb


class CallsheetAudioShift:
    """Shifts a waveform by a set number of samples. Positive = delay
    (silence inserted at the start); negative = advance (samples removed
    from the start). keep_length preserves the original duration by
    trimming or zero-padding the tail."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "shift_samples": ("INT", {"default": 0,
                                      "min": -0x7FFFFFFF,
                                      "max": 0x7FFFFFFF}),
            "keep_length": ("BOOLEAN", {"default": True}),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "shift"
    CATEGORY = "callsheet"

    def shift(self, audio, shift_samples, keep_length):
        wf = audio["waveform"]                    # [B, C, T]
        t = wf.shape[-1]
        s = shift_samples
        if s > 0:
            pad = torch.zeros(*wf.shape[:-1], s,
                              dtype=wf.dtype, device=wf.device)
            wf = torch.cat([pad, wf], dim=-1)
        elif s < 0:
            wf = wf[..., min(-s, t):]
        if keep_length:
            if wf.shape[-1] > t:
                wf = wf[..., :t]
            elif wf.shape[-1] < t:
                pad = torch.zeros(*wf.shape[:-1], t - wf.shape[-1],
                                  dtype=wf.dtype, device=wf.device)
                wf = torch.cat([wf, pad], dim=-1)
        return ({"waveform": wf,
                 "sample_rate": audio["sample_rate"]},)


class CallsheetAudioCrossfade:
    """Crossfades audio_b over audio_a, in one of two modes.

    aligned (default): both tracks share the same timeline origin
    (t=0 of a is t=0 of b). Output is a before the fade window, a blend
    across [offset, offset+fade), and b after — with b indexed at the
    SAME timeline positions as a. Use this to repair a distorted region
    when a sampler regenerated audio that should match the original
    (e.g. a continuation's overlap). offset -1 = fade across the tail
    of the shorter track. Output ends where b ends; a's content beyond
    the window is dropped (splice semantics).

    append: b's sample 0 is placed at offset on a's timeline
    (-1 = len(a) - fade), overlapping the fade window — classic
    DJ-style splice of two independent clips. Output length is
    max(len(a), offset + len(b)).

    If either input is None (unconnected, or a None list item from a
    pipeline output), the other passes through unchanged; both None is
    an error. curve: 'equal_power' (constant loudness, different
    material) or 'linear' (constant amplitude, near-identical overlap).
    For seam repair keep the fade short: 480-2400 samples at 48kHz."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "mode": (["aligned", "append"],),
            "offset_samples": ("INT", {"default": -1, "min": -1,
                                       "max": 0x7FFFFFFF}),
            "fade_samples": ("INT", {"default": 2400, "min": 0,
                                     "max": 0x7FFFFFFF}),
            "curve": (["equal_power", "linear"],),
        }, "optional": {
            "audio_a": ("AUDIO",),
            "audio_b": ("AUDIO",),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "crossfade"
    CATEGORY = "callsheet"

    def crossfade(self, mode, offset_samples, fade_samples, curve,
                  audio_a=None, audio_b=None):
        if audio_a is None and audio_b is None:
            raise ValueError("CallsheetAudioCrossfade: both audio inputs "
                             "are None — nothing to output")
        if audio_a is None:
            return (audio_b,)
        if audio_b is None:
            return (audio_a,)

        wa, wb = _match_check(audio_a, audio_b,
                              "CallsheetAudioCrossfade")
        la, lb = wa.shape[-1], wb.shape[-1]

        fade = fade_samples
        offset = offset_samples

        if mode == "aligned":
            short = min(la, lb)
            if offset < 0:
                offset = max(0, short - fade)
            offset = min(offset, short)
            fade = min(fade, short - offset)
            total = max(offset + fade, lb)
        else:  # append
            if offset < 0:
                offset = max(0, la - fade)
            offset = min(offset, la)
            fade = min(fade, la - offset, lb)
            total = max(la, offset + lb)

        out = torch.zeros(*wa.shape[:-1], total,
                          dtype=wa.dtype, device=wa.device)

        if fade > 0:
            t = torch.linspace(0.0, 1.0, fade,
                               dtype=wa.dtype, device=wa.device)
            if curve == "equal_power":
                g_out = torch.cos(t * torch.pi / 2)
                g_in = torch.sin(t * torch.pi / 2)
            else:
                g_out = 1.0 - t
                g_in = t

        # a: full until the window, ramp out across it, dropped after
        out[..., :offset] += wa[..., :offset]
        if fade > 0:
            out[..., offset:offset + fade] += \
                wa[..., offset:offset + fade] * g_out

        # b: indexed on the shared timeline (aligned) or from its own
        # start placed at offset (append)
        if mode == "aligned":
            if fade > 0:
                out[..., offset:offset + fade] += \
                    wb[..., offset:offset + fade] * g_in
            out[..., offset + fade:lb] += wb[..., offset + fade:]
        else:
            if fade > 0:
                out[..., offset:offset + fade] += wb[..., :fade] * g_in
            out[..., offset + fade:offset + lb] += wb[..., fade:]

        return ({"waveform": out,
                 "sample_rate": audio_a["sample_rate"]},)


class CallsheetAudioResample:
    """Resamples a waveform to target_sample_rate using torchaudio's
    band-limited sinc interpolation, with optional channel conversion
    (mono = mean of channels, stereo = duplicate mono / keep stereo).
    No-op when the audio already matches."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "target_sample_rate": ("INT", {"default": 48000, "min": 1000,
                                           "max": 384000}),
            "channels": (["keep", "mono", "stereo"],),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "resample"
    CATEGORY = "callsheet"

    def resample(self, audio, target_sample_rate, channels):
        wf = audio["waveform"]                    # [B, C, T]
        sr = audio["sample_rate"]
        if sr != target_sample_rate:
            wf = torchaudio.functional.resample(wf, sr,
                                                target_sample_rate)
        if channels == "mono" and wf.shape[1] > 1:
            wf = wf.mean(dim=1, keepdim=True)
        elif channels == "stereo" and wf.shape[1] == 1:
            wf = wf.repeat(1, 2, 1)
        return ({"waveform": wf,
                 "sample_rate": target_sample_rate},)


class CallsheetAudioInfo:
    """Reports a waveform's sample count, sample rate, channel count,
    and duration in seconds."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"audio": ("AUDIO",)}}

    RETURN_TYPES = ("INT", "INT", "INT", "FLOAT")
    RETURN_NAMES = ("sample_count", "sample_rate", "channels", "seconds")
    FUNCTION = "info"
    CATEGORY = "callsheet"

    def info(self, audio):
        wf = audio["waveform"]
        sr = audio["sample_rate"]
        return (wf.shape[-1], sr, wf.shape[1], wf.shape[-1] / sr)


class CallsheetAudioTrim:
    """Keeps [start_sample, end_sample) of a waveform. end_sample 0 =
    to the end; negative counts from the end (Python-slice style)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "start_sample": ("INT", {"default": 0, "min": 0,
                                     "max": 0x7FFFFFFF}),
            "end_sample": ("INT", {"default": 0,
                                   "min": -0x7FFFFFFF,
                                   "max": 0x7FFFFFFF}),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "trim"
    CATEGORY = "callsheet"

    def trim(self, audio, start_sample, end_sample):
        wf = audio["waveform"]
        t = wf.shape[-1]
        end = t if end_sample == 0 else end_sample
        if end < 0:
            end = t + end
        end = min(end, t)
        if start_sample >= end:
            raise ValueError(
                f"CallsheetAudioTrim: window [{start_sample}, {end}) is "
                f"empty ({t} samples available)")
        return ({"waveform": wf[..., start_sample:end],
                 "sample_rate": audio["sample_rate"]},)


class CallsheetAudioPad:
    """Adds pad_start / pad_end samples of silence around a waveform."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "pad_start": ("INT", {"default": 0, "min": 0,
                                  "max": 0x7FFFFFFF}),
            "pad_end": ("INT", {"default": 0, "min": 0,
                                "max": 0x7FFFFFFF}),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "pad"
    CATEGORY = "callsheet"

    def pad(self, audio, pad_start, pad_end):
        wf = audio["waveform"]
        pieces = []
        if pad_start:
            pieces.append(torch.zeros(*wf.shape[:-1], pad_start,
                                      dtype=wf.dtype, device=wf.device))
        pieces.append(wf)
        if pad_end:
            pieces.append(torch.zeros(*wf.shape[:-1], pad_end,
                                      dtype=wf.dtype, device=wf.device))
        return ({"waveform": torch.cat(pieces, dim=-1)
                 if len(pieces) > 1 else wf,
                 "sample_rate": audio["sample_rate"]},)


class CallsheetJobRouter:
    """Routes a CS_JOB list to one of two outputs by flag: jobs whose
    flags contain 'flag' go to job_on, the rest to job_off. A store node
    receiving an empty job list skips entirely and never evaluates its
    lazy media inputs.

    IMPORTANT: only the job wire is routed — media lists are not. Always
    pair the routing flag with uniform_flags on the pipeline node so each
    pass is homogeneous; a mixed batch will fail loudly at the store with
    a jobs/media length mismatch."""

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "job": ("CS_JOB",),
            "flags": ("CS_FLAGS",),
            "flag": ("STRING", {"default": ""}),
        }}

    RETURN_TYPES = ("CS_JOB", "CS_JOB")
    RETURN_NAMES = ("job_on", "job_off")
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "route"
    CATEGORY = "callsheet"

    def route(self, job, flags, flag):
        flag = flag[0].strip()
        if len(job) != len(flags):
            raise ValueError(f"CallsheetJobRouter: {len(job)} jobs vs "
                             f"{len(flags)} flag lists — wire both from "
                             f"the same pipeline")
        on, off = [], []
        for j, fl in zip(job, flags):
            (on if flag in fl else off).append(j)
        return (on, off)


class CallsheetHasValue:
    """Outputs True when the input carries a value, False when it is
    None — e.g. wire a pipeline's continue_frame / target_frame output
    in, and use the boolean to drive lazy switches without a separate
    CallsheetFlag lookup. Accepts any type. Maps per-item over lists.

    NOTE: this does not lift the uniform_flags requirement — a lazy
    switch downstream still resolves per-BATCH, so a flag/anchor that
    changes graph topology must keep the batch homogeneous."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"optional": {"value": ("*",)}}

    @classmethod
    def VALIDATE_INPUTS(cls, input_types):
        return True   # accept any upstream type

    RETURN_TYPES = ("BOOLEAN",)
    RETURN_NAMES = ("has_value",)
    FUNCTION = "check"
    CATEGORY = "callsheet"

    def check(self, value=None):
        return (value is not None,)


class CallsheetConcatText:
    """
    Concatenates an arbitrary number of string inputs (expanding sockets,
    managed by the frontend JS) using a selectable delimiter.

    Inputs that are None (e.g. from bypassed upstream nodes) or empty
    strings are skipped entirely -- no dangling delimiters.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "delimiter": (["comma", "period", "new line", "auto new line"],
                              {"default": "auto new line"}),
            },
            "optional": {
                # The JS extension adds text_2, text_3, ... as connections
                # are made. Extra inputs arrive via **kwargs; ComfyUI only
                # type-validates declared inputs, so this is safe.
                "text_1": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "concat"
    CATEGORY = "Callsheet/Text"

    def concat(self, delimiter, **kwargs):
        def sort_key(name):
            try:
                return int(name.rsplit("_", 1)[-1])
            except ValueError:
                return 0

        parts = []
        for key in sorted(kwargs.keys(), key=sort_key):
            value = kwargs[key]
            if value is None:  # bypassed node upstream
                continue
            value = str(value)
            if value == "":
                continue
            parts.append(value)

        if delimiter == "comma":
            return (", ".join(parts),)
        if delimiter == "period":
            return (". ".join(parts),)
        if delimiter == "new line":
            return ("\n".join(parts),)

        # auto new line: only insert \n if the boundary doesn't already have one
        out = ""
        for part in parts:
            if out and not out.endswith("\n") and not part.startswith("\n"):
                out += "\n"
            out += part
        return (out,)


class CallsheetTextChain:
    """
    A string node that concatenates a multiline text widget with an optional
    STRING input. If the STRING input is connected, it will prepend the input
    string to the widget text.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "default": ""}),
            },
            "optional": {
                "prefix": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "concatenate_strings"
    CATEGORY = "Callsheet/Text"

    def concatenate_strings(self, text, prefix=None):
        if prefix is None or prefix == "":
            return (text,)
        return (prefix + text,)


class CallsheetLoRATagLoader(LoraLoader):
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "The diffusion model the LoRA will be applied to."}),
                "clip": ("CLIP", {"tooltip": "The CLIP model the LoRA will be applied to."}),
                "prompt": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model","clip","prompt")
    OUTPUT_TOOLTIPS = ("The modified diffusion model.", "The modified CLIP model.")
    FUNCTION = "parse_and_load_loras"

    CATEGORY = "model/loaders"
    DESCRIPTION = "This LoRA loader is used to modify both diffusion and CLIP models, altering the way in which latents are denoised such as applying styles."
    SEARCH_ALIASES = ["lora", "load lora", "apply lora", "lora loader", "lora model"]

    def parse_and_load_loras(self, model, clip, prompt):
        # Regular expression to match LoRA tags
        # Pattern matches: <lora:path:strength> or <lora:path:strength_model:strength_clip>
        lora_pattern = r'<lora:([^:]+):([0-9.-]+)(?::([0-9.-]+))?>'
        
        # Find all LoRA tags in the prompt
        matches = re.findall(lora_pattern, prompt)
        
        # Process each LoRA tag
        for match in matches:
            lora_path = match[0]
            strength_model = float(match[1])
            
            if match[2]:  # If we have a second strength value (strength_clip)
                strength_clip = float(match[2])
                # Load LoRA with both model and clip strengths
                model, clip = self.load_lora(model, clip, lora_path, strength_model, strength_clip)
            else:
                # Load LoRA with only model strength
                model = self.load_lora(model, None, lora_path, strength_model, 0)[0]
        
        # Remove all LoRA tags from the prompt
        cleaned_prompt = re.sub(lora_pattern, '', prompt)
        
        return (model, clip, cleaned_prompt)