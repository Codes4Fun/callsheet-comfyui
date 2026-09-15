from .store import VARIATION_STORE, variation_key
from . import runstate


def build_resolver(items, selections):
    """Returns resolve(label) -> store key or None.

    Explicit selections are honored whenever they are a stored variation
    of that label. The automatic fallback only accepts variations that
    match the item's CURRENT full definition (recursively), which forces
    dependents of edited items to defer until regeneration."""
    by_label = {it["label"]: it for it in items}
    memo = {}

    def resolve(label):
        if label in memo:
            return memo[label]
        memo[label] = None   # re-entry guard; graph is validated acyclic
        item = by_label.get(label)
        if item is None:
            return None

        sel = selections.get(label)
        if (sel and sel in VARIATION_STORE
                and VARIATION_STORE[sel]["label"] == label):
            memo[label] = sel
            return sel

        if item["injected"]:
            recs = [k for k, r in VARIATION_STORE.items()
                    if r["label"] == label]
            memo[label] = recs[-1] if recs else None
            return memo[label]

        ref_keys = [resolve(r) for r in item["refs"]]
        aref_keys = [resolve(r) for r in item["audio_refs"]]
        cont_label = item.get("continue_frame")
        cont_key = resolve(cont_label) if cont_label else None
        tgt_label = item.get("target_frame")
        tgt_key = resolve(tgt_label) if tgt_label else None

        def clip_pair(entry):
            if not entry:
                return None, False
            k = resolve(entry[0])
            if k is None:
                return None, True
            return [k, entry[1]], False

        vref_pairs = []
        for l, spec in item.get("video_refs", []):
            k = resolve(l)
            if k is None:
                return None
            vref_pairs.append([k, spec])

        cv_pair, cv_missing = clip_pair(item.get("continue_video"))
        tv_pair, tv_missing = clip_pair(item.get("target_video"))

        if (any(k is None for k in ref_keys)
                or any(k is None for k in aref_keys)
                or (cont_label and cont_key is None)
                or (tgt_label and tgt_key is None)
                or cv_missing or tv_missing):
            return None
        stored = [variation_key(item, s, ref_keys, aref_keys, cont_key,
                                vref_pairs, cv_pair, tgt_key, tv_pair)
                  for s in item["seeds"]]
        stored = [k for k in stored if k in VARIATION_STORE]
        memo[label] = stored[-1] if stored else None
        return memo[label]

    return resolve


def stale_hint(label):
    """True if the store holds ANY variations for this label."""
    return any(r["label"] == label for r in VARIATION_STORE.values())


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
        for k in ("continue_frame", "target_frame"):
            if it.get(k):
                deps.append(it[k])
        for k in ("continue_video", "target_video"):
            if it.get(k):
                deps.append(it[k][0])
        stack.extend(d for d in deps if d in by_label)
    return closure


def collect_candidates(items, selections, focus):
    closure = focus_closure(items, focus) if focus else None
    resolver = build_resolver(items, selections)
    deferred = {}
    passno = runstate.current_pass()

    def why(dep):
        return ("stale — its definition changed and it has not "
                "been regenerated yet" if stale_hint(dep)
                else "not generated yet")

    def rec_path(key):
        return os.path.join(STORE_DIR,
                            VARIATION_STORE[key]["filename"])

    candidates = []
    on_hold = {}
    hard_errors = []
    for item in items:
        if item["injected"]:
            continue

        pipeline = item["pipeline"]

        if closure is not None and item["label"] not in closure:
            if not pipeline in on_hold:
                on_hold[pipeline] = 0
            on_hold[pipeline] += 1
            continue

        ref_keys = [resolver(r) for r in item["refs"]]
        aref_keys = [resolver(r) for r in item["audio_refs"]]
        cont_label = item.get("continue_frame")
        cont_key = resolver(cont_label) if cont_label else None
        tgt_label = item.get("target_frame")
        tgt_key = resolver(tgt_label) if tgt_label else None

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

        tv = item.get("target_video")
        tv_pair, tv_unresolved = None, None
        if tv:
            k = resolver(tv[0])
            if k is None:
                tv_unresolved = tv[0]
            else:
                tv_pair = [k, tv[1]]

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
        if tgt_label and tgt_key is None:
            unresolved_deps.append(tgt_label)
        if tv_unresolved:
            unresolved_deps.append(tv_unresolved)

        if unresolved_deps:
            detail = "; ".join(f"'{d}' {why(d)}"
                                for d in unresolved_deps)
            print(f"[Callsheet pass {passno}] "
                    f"'{item['label']}': deferred ({detail})")
            if not pipeline in deferred:
                deferred[pipeline] = 0
            deferred[pipeline] += 1
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
        tgt_rec = VARIATION_STORE.get(tgt_key) if tgt_key else None
        if tgt_rec and tgt_rec.get("kind") not in ("video", "image"):
            item_errors.append(
                f"'{item['label']}': target_frame '{tgt_label}' "
                f"resolved to a {tgt_rec.get('kind')} variation — "
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
        tv_window = None
        if tv_pair:
            rec = VARIATION_STORE[tv_pair[0]]
            if rec.get("kind") != "video":
                item_errors.append(
                    f"'{item['label']}': target_video "
                    f"'{tv[0]}' resolved to a {rec.get('kind')} "
                    f"variation — must be video")
            else:
                n_frames, src_fps, _, _ = video_frame_info(
                    rec_path(tv_pair[0]))
                try:
                    win = resolve_window(parse_spec(tv_pair[1]),
                                            n_frames, src_fps)
                    tv_window = (tv_pair[0], win, src_fps)
                except ValueError as e:
                    item_errors.append(
                        f"'{item['label']}': target_video: {e}")

        # ---- flags: pipeline name + user + auto ------------------------
        auto = [pipeline] if pipeline else []
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
        if tgt_key:
            auto.append("target_frame")
        if tv_pair:
            auto.append("target_video")
            if VARIATION_STORE[tv_pair[0]].get("has_audio"):
                auto.append("target_video_audio")
        eff_flags = list(dict.fromkeys(item["flags"] + auto))

        if item_errors:
            hard_errors.extend(item_errors)
            continue

        pending = []
        for seed in item["seeds"]:
            key = variation_key(item, seed, ref_keys, aref_keys,
                                cont_key, vref_pairs, cv_pair,
                                tgt_key, tv_pair)
            if key not in VARIATION_STORE:
                pending.append((seed, key))
        if not pending:
            continue

        candidates.append({
            "item": item, "pending": pending, "flags": eff_flags,
            "ref_keys": ref_keys, "aref_keys": aref_keys,
            "cont_key": cont_key, "cont_rec": cont_rec,
            "vref_pairs": vref_pairs, "vref_windows": vref_windows,
            "cv_window": cv_window,
            "tgt_key": tgt_key, "tgt_rec": tgt_rec,
            "tv_window": tv_window})
    
    return candidates, on_hold, deferred, hard_errors