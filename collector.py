from .store import VARIATION_STORE, variation_key, load_store_image


def build_assets(items, selections, wanted):
    """Shared by the collector node and the /callsheet/assets route:
    builds the browsable asset list for a set of parsed items."""
    assets = []
    for item in sorted(items, key=lambda i: i["index"]):
        if wanted and item["pipeline"] not in wanted:
            continue

        has_deps = (item["injected"] or item["refs"]
                    or item["audio_refs"] or item.get("video_refs")
                    or item.get("continue_frame")
                    or item.get("continue_video"))
        if has_deps:
            recs = [(k, r) for k, r in VARIATION_STORE.items()
                    if r["label"] == item["label"]]
        else:
            recs = []
            for seed in item["seeds"]:
                k = variation_key(item, seed)
                if k in VARIATION_STORE:
                    recs.append((k, VARIATION_STORE[k]))

        variations = [{"key": k, "seed": r["seed"],
                       "kind": r.get("kind", "image"),
                       "poster": r.get("poster"),
                       "preview": r.get("preview"),
                       "filename": r["filename"],
                       "subfolder": r["subfolder"], "type": r["type"]}
                      for k, r in recs]

        chosen = selections.get(item["label"])
        if chosen not in {v["key"] for v in variations}:
            chosen = variations[-1]["key"] if variations else None

        assets.append({"label": item["label"], "index": item["index"],
                       "pipeline": item["pipeline"],
                       "injected": item["injected"],
                       "selected": chosen, "variations": variations})
    return assets


class FlexibleOptionalInputType(dict):
    """Claims to contain any key so the frontend can add sockets."""

    def __init__(self, io_type):
        super().__init__()
        self.io_type = io_type

    def __getitem__(self, key):
        return (self.io_type,)

    def __contains__(self, key):
        return True


class CallsheetCollector:
    """Pure viewer/controller: browses assets and variations, mutates
    selection/focus state on the CallsheetTextInput node via the
    frontend. The frontend can also rebuild the browser without queueing
    via the /callsheet/assets route (⟳ button)."""

    INPUT_IS_LIST = True
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "parsed": ("CS_PARSED",),
            "pipeline_filter": ("STRING", {"default": "",
                                           "advanced": True}),
        }, "optional": FlexibleOptionalInputType("CS_ITEMS")}

    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("images", "labels")
    OUTPUT_IS_LIST = (True, True)
    FUNCTION = "collect"
    CATEGORY = "callsheet"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def collect(self, parsed, pipeline_filter, **kwargs):
        parsed = parsed[0]
        items, selections = parsed["items"], parsed["selections"]
        wanted = {p.strip() for p in pipeline_filter[0].split(",")
                  if p.strip()}

        assets = build_assets(items, selections, wanted)

        out_images, out_labels = [], []
        for a in assets:
            if not a["selected"]:
                continue
            rec = VARIATION_STORE[a["selected"]]
            if rec.get("kind", "image") == "image":
                out_images.append(load_store_image(rec))
                out_labels.append(a["label"])

        return {"ui": {"cs_assets": assets},
                "result": (out_images, out_labels)}