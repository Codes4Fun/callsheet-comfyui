from .store import VARIATION_STORE, variation_key


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