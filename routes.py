import json
import os

from aiohttp import web
from server import PromptServer

from .collector import build_assets
from .grammar import parse_and_validate
from .store import STORE_DIR, VARIATION_STORE, save_manifest


@PromptServer.instance.routes.post("/callsheet/clear")
async def cs_clear(request):
    data = await request.json()
    keys = set(data.get("keys") or [])
    label = data.get("label")
    clear_all = bool(data.get("all"))
    doomed = [k for k, r in VARIATION_STORE.items()
              if clear_all or k in keys
              or (label and r["label"] == label)]
    for k in doomed:
        rec = VARIATION_STORE.pop(k)
        for f in (rec.get("filename"), rec.get("poster"),
                  rec.get("preview")):
            if f:
                try:
                    os.remove(os.path.join(STORE_DIR, f))
                except FileNotFoundError:
                    pass
    save_manifest()
    return web.json_response({"cleared": len(doomed)})


@PromptServer.instance.routes.post("/callsheet/assets")
async def cs_assets(request):
    """Builds the collector's asset list from widget values WITHOUT
    queueing a run — used by the collector's refresh button and its
    populate-on-load, so browsing never triggers generation."""
    data = await request.json()
    try:
        pipeline_specs = json.loads(data.get("pipeline_specs") or "{}")
        state = json.loads(data.get("state") or '{}')
        requests_ = state["variation_requests"] if "variation_requests" in state else {}
        selections = state["selections"] if "selections" in state else {}
        if not isinstance(requests_, dict) or not isinstance(
                selections, dict):
            raise ValueError("variation_requests/selections must be "
                             "JSON objects")
        items = parse_and_validate(
            data.get("text", ""), pipeline_specs,
            bool(data.get("strict")),
            int(data.get("base_seed") or 0),
            {"width": str(data.get("fallback_width") or 1024),
             "height": str(data.get("fallback_height") or 1024),
             "fps": str(data.get("fallback_fps") or 24)},
            requests_)
    except (ValueError, json.JSONDecodeError) as e:
        return web.json_response({"error": str(e)})
    wanted = {p.strip()
              for p in (data.get("pipeline_filter") or "").split(",")
              if p.strip()}
    return web.json_response(
        {"assets": build_assets(items, selections, wanted)})