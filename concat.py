import json
import os
import re
import subprocess

import folder_paths

from .media import video_frame_info
from .resolve import build_resolver
from .store import STORE_DIR, VARIATION_STORE
from .trimspec import split_label_spec, parse_spec, resolve_window
from . import runstate
from .binaries import FFMPEG


def _first(x):
    return x[0] if isinstance(x, list) else x


def _embed_metadata(out_path, payload):
    """Remux (stream copy) with an ffmetadata comment tag containing the
    workflow, matching the convention frontends parse for mp4s."""
    def esc(s):
        return re.sub(r"([=;#\\\n])", r"\\\1", s)

    meta_path = out_path + ".ffmeta"
    with open(meta_path, "w") as f:
        f.write(";FFMETADATA1\n")
        f.write("comment=" + esc(json.dumps(payload)) + "\n")
    tmp = out_path + ".tagged.mp4"
    ok = subprocess.run(
        [FFMPEG, "-y", "-i", out_path, "-i", meta_path,
         "-map_metadata", "1", "-map", "0", "-c", "copy", tmp],
        stderr=subprocess.DEVNULL).returncode == 0
    os.remove(meta_path)
    if ok:
        os.replace(tmp, out_path)
    else:
        if os.path.exists(tmp):
            os.remove(tmp)
        print("[CallsheetConcatVideos] warning: workflow embed failed; "
              "video written without metadata")


class CallsheetConcatVideos:
    """Concatenates selected video variations. User-fixable problems
    (renamed/unknown labels, missing clips with nothing pending, bad trim
    specs) never raise — they render as an in-node warning so the
    workflow keeps running without bypassing this node. Only internal
    failures (ffmpeg) raise.

    Missing clips are reported as deferred work to the convergence loop:
    if this pass made progress, another pass is queued automatically and
    this node waits. When the callsheet's focus list is active, the
    timeline is simply paused (no deferral reporting, no error).

    labels: comma-separated entries with optional trims — label,
    label:H:T (drop), label:A-B (keep range), label:-N (keep last N);
    values in source frames or seconds ('0.5s'). Empty = all selected
    videos not referenced as source material; continue_frame /
    continue_video targets are timeline material and are included.

    embed_workflow writes the workflow into the output file's metadata
    (stream-copy remux). Wire store acks into after_N so this node runs
    after the stores within each pass."""

    INPUT_IS_LIST = True
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "labels": ("STRING", {"default": ""}),
            "output_name": ("STRING", {"default": "final"}),
            "fps": ("INT", {"default": 24, "min": 1, "max": 120}),
            "dedupe_continues": ("BOOLEAN", {"default": True}),
            "embed_workflow": ("BOOLEAN", {"default": True}),
        }, "optional": {
            "after_1": ("CS_ITEMS",),
            "after_2": ("CS_ITEMS",),
            "after_3": ("CS_ITEMS",),
        }, "hidden": {
            "prompt": "PROMPT",
            "extra_pnginfo": "EXTRA_PNGINFO",
        }}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    OUTPUT_IS_LIST = (False,)
    FUNCTION = "combine"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def _notice(self, text, problem=False):
        kind = "problem" if problem else "waiting"
        print(f"[CallsheetConcatVideos] {kind}: {text}")
        return {"ui": {"cs_final": [{kind: True, "text": text}]},
                "result": ("",)}

    def _wait(self, reason):
        passno = runstate.current_pass()
        if runstate.will_continue():
            text = (f"pass {passno}: {reason} — "
                    f"pass {passno + 1} queued")
        elif runstate.progress_made():
            text = (f"pass {passno}: {reason} — progress was made but "
                    f"auto_continue is off; queue again to continue")
        elif runstate.deferred_pending():
            text = (f"pass {passno}: {reason} — no further progress "
                    f"possible; check deferral messages in the console")
        else:
            text = f"pass {passno}: {reason}"
        return self._notice(text)

    def _missing(self, missing, focus):
        if focus:
            # focus intentionally holds work back; don't feed the loop
            return self._notice(
                f"focus active ({', '.join(focus)}) — timeline paused; "
                f"waiting on: " + ", ".join(missing))
        already = runstate.deferred_pending()
        runstate.report(0, len(missing))
        reason = (f"waiting on {len(missing)} clip(s): "
                  + ", ".join(missing))
        if (runstate.will_continue() or runstate.progress_made()
                or already > 0):
            return self._wait(reason)
        return self._notice(
            "missing clips with no pending work: " + ", ".join(missing)
            + " — if labels were renamed, update the labels widget; "
            "otherwise check the console for deferral reasons",
            problem=True)

    def combine(self, parsed, labels, output_name, fps, dedupe_continues,
                embed_workflow, prompt=None, extra_pnginfo=None, **after):
        parsed = parsed[0]
        labels = labels[0]
        output_name = output_name[0]
        fps = fps[0]
        dedupe_continues = dedupe_continues[0]
        embed_workflow = embed_workflow[0]
        prompt = _first(prompt)
        extra_pnginfo = _first(extra_pnginfo)

        items, selections = parsed["items"], parsed["selections"]
        focus = parsed.get("focus") or []
        by_label = {it["label"]: it for it in items}
        resolver = build_resolver(items, selections)

        if not labels:
            return self._notice("no labels specified")

        specs = []
        for entry in labels.split(","):
            entry = entry.strip()
            if not entry:
                continue
            label, spec = split_label_spec(entry)
            try:
                parse_spec(spec)
            except ValueError as e:
                return self._notice(f"labels entry '{entry}': {e}",
                                    problem=True)
            specs.append((label, spec))

        def selected_video(label):
            key = resolver(label)
            return VARIATION_STORE.get(key) if key else None

        def is_video_item(it):
            if it.get("type"):
                return it["type"] == "video"
            return any(r["label"] == it["label"]
                       and r.get("kind") == "video"
                       for r in VARIATION_STORE.values())

        if specs:
            missing = []
            for label, _ in specs:
                if label not in by_label:
                    return self._notice(
                        f"label '{label}' is not in the callsheet — "
                        f"was it renamed? Update the labels widget.",
                        problem=True)
                rec = selected_video(label)
                if rec is None:
                    missing.append(label)
                elif rec.get("kind") != "video":
                    return self._notice(
                        f"'{label}' is not a video item", problem=True)
            if missing:
                return self._missing(missing, focus)
        else:
            referenced = set()
            for it in items:
                referenced.update(it["refs"])
                referenced.update(it["audio_refs"])
                referenced.update(l for l, _ in it["video_refs"])
                # continue_frame / continue_video targets are NOT
                # excluded: continued shots are timeline material
            missing = []
            for it in sorted(items, key=lambda i: i["index"]):
                if it["label"] in referenced:
                    continue
                rec = selected_video(it["label"])
                if rec is not None and rec.get("kind") == "video":
                    specs.append((it["label"], ""))
                elif rec is None and is_video_item(it):
                    missing.append(it["label"])
            if missing:
                return self._missing(missing, focus)
            if not specs:
                if runstate.deferred_pending() or runstate.progress_made():
                    return self._wait("no clips stored yet")
                return self._notice(
                    "no video clips found to concatenate — are any "
                    "video items defined and generated?", problem=True)
            if runstate.deferred_pending():
                return self._wait(
                    f"{len(specs)} clip(s) ready, batch still converging")
            print("[CallsheetConcatVideos] auto order: "
                  + ", ".join(s[0] for s in specs))

        # ---- seam dedupe for continue/target anchored clips ----------------
        resolved_specs = []
        for i, (label, spec) in enumerate(specs):
            if spec == "" and dedupe_continues:
                it = by_label[label]
                prev_l = specs[i - 1][0] if i > 0 else None
                next_l = specs[i + 1][0] if i + 1 < len(specs) else None
                head = 1 if (prev_l
                             and it.get("continue_frame") == prev_l) else 0
                tail = 1 if (next_l
                             and it.get("target_frame") == next_l) else 0
                if head or tail:
                    spec = f"{head}:{tail}"
                    print(f"[CallsheetConcatVideos] '{label}': dropping "
                          f"{head} head / {tail} tail seam frame(s) "
                          f"(continue/target anchors)")
            resolved_specs.append((label, spec))
        specs = resolved_specs

        clips = [(selected_video(l), spec) for l, spec in specs]

        # target resolution from first clip
        first_path = os.path.join(STORE_DIR, clips[0][0]["filename"])
        _, _, w, h = video_frame_info(first_path)

        # per-clip frame windows in source frames
        windows = []
        for i, (rec, spec) in enumerate(clips):
            path = os.path.join(STORE_DIR, rec["filename"])
            n_frames, src_fps, _, _ = video_frame_info(path)
            try:
                sf, ef = resolve_window(parse_spec(spec),
                                        n_frames, src_fps)
            except ValueError as e:
                return self._notice(f"'{specs[i][0]}': {e}", problem=True)
            windows.append((sf, ef, src_fps))

        cmd = [FFMPEG, "-y"]
        for rec, _ in clips:
            cmd += ["-i", os.path.join(STORE_DIR, rec["filename"])]

        parts, pads = [], []
        extra = len(clips)
        for i, ((rec, _), (sf, ef, src_fps)) in enumerate(
                zip(clips, windows)):
            parts.append(
                f"[{i}:v]trim=start_frame={sf}:end_frame={ef},"
                f"setpts=PTS-STARTPTS,"
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[v{i}]")
            if rec.get("has_audio"):
                start_t, end_t = sf / src_fps, ef / src_fps
                parts.append(f"[{i}:a]atrim=start={start_t:.4f}:"
                             f"end={end_t:.4f},asetpts=PTS-STARTPTS,"
                             f"aresample=48000,"
                             f"aformat=channel_layouts=stereo[a{i}]")
            else:
                dur = (ef - sf) / src_fps
                cmd += ["-f", "lavfi", "-t", f"{dur:.3f}",
                        "-i", "anullsrc=r=48000:cl=stereo"]
                parts.append(f"[{extra}:a]aformat="
                             f"channel_layouts=stereo[a{i}]")
                extra += 1
            pads.append(f"[v{i}][a{i}]")
        parts.append("".join(pads)
                     + f"concat=n={len(clips)}:v=1:a=1[vout][aout]")

        out_file = f"{output_name}.mp4"
        out_path = os.path.join(folder_paths.get_output_directory(),
                                out_file)
        cmd += ["-filter_complex", ";".join(parts),
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19",
                "-c:a", "aac", "-b:a", "192k", out_path]
        if subprocess.run(cmd, stderr=subprocess.DEVNULL).returncode:
            raise RuntimeError("ffmpeg concat failed: " + " ".join(cmd))

        if embed_workflow:
            payload = {}
            if extra_pnginfo is not None:
                for x in extra_pnginfo:
                    payload[x] = extra_pnginfo[x]
            if prompt is not None:
                payload["prompt"] = json.dumps(prompt)
            if payload:
                _embed_metadata(out_path, payload)

        return {"ui": {"cs_final": [{
                    "filename": out_file, "subfolder": "",
                    "type": "output",
                    "labels": [s[0] for s in specs]}]},
                "result": (out_path,)}