import uuid

from server import PromptServer

_SETTINGS = {"auto_continue": True, "max_passes": 16}
_RUNS = {}      # prompt_id -> {"emitted","deferred","received","requeued","capped"}
_PASS_OF = {}   # prompt_id -> pass number (1 = user-initiated run)


def configure(auto_continue, max_passes):
    _SETTINGS["auto_continue"] = auto_continue
    _SETTINGS["max_passes"] = max_passes


def _current_prompt_id():
    q = PromptServer.instance.prompt_queue
    cr = getattr(q, "currently_running", None)
    if cr:
        for item in cr.values():
            return tuple(item)[1]
    return "unknown"


def current_pass(pid=None):
    return _PASS_OF.get(pid or _current_prompt_id(), 1)


def _run(pid):
    if pid not in _RUNS:
        while len(_RUNS) > 32:          # prune old runs
            _RUNS.pop(next(iter(_RUNS)))
        _RUNS[pid] = {"emitted": 0, "deferred": 0, "received": 0,
                      "requeued": False, "capped": False}
    return _RUNS[pid]


def report(emitted, deferred):
    """Called by pipeline, inject, and concat nodes. Queues a convergence
    pass when this run both made progress (emitted) and left unresolved
    work (deferred). Counts are cumulative across nodes, so whichever
    node's report completes the condition pulls the trigger; the
    'requeued' flag makes it fire at most once per pass."""
    pid = _current_prompt_id()
    r = _run(pid)
    r["emitted"] += emitted
    r["deferred"] += deferred
    if r["requeued"] or not _SETTINGS["auto_continue"]:
        return


def report_received(received):
    pid = _current_prompt_id()
    r = _run(pid)
    r["received"] += received
    if r["received"] > r["emitted"]:
        raise RuntimeError("callsheet report_received: received more than emitted!")
    if r["requeued"] or not _SETTINGS["auto_continue"]:
        return
    if r["received"] == r["emitted"]:
        if r["deferred"] > 0:
            if current_pass(pid) >= _SETTINGS["max_passes"]:
                if not r["capped"]:
                    r["capped"] = True
                    print(f"[Callsheet] max_passes "
                        f"({_SETTINGS['max_passes']}) reached with "
                        f"{r['deferred']} item(s) still deferred; "
                        f"queue again to continue.")
                return
            r["requeued"] = True
            _requeue(pid)
            print(f"[Callsheet] pass {current_pass(pid) + 1} queued "
                f"({r['emitted']} job(s) this pass, "
                f"{r['deferred']} deferred)")
        else:
            print("[Callsheet] all passes completed")


def deferred_pending():
    return _RUNS.get(_current_prompt_id(), {}).get("deferred", 0)


def progress_made():
    """True if anything was emitted (generated or injected) this run —
    i.e. the store will differ after this pass, so a re-resolve may
    succeed where this pass's resolution failed."""
    r = _RUNS.get(_current_prompt_id())
    return bool(r and r["emitted"] > 0)


def will_continue():
    r = _RUNS.get(_current_prompt_id())
    return bool(r and r["requeued"])


def _requeue(pid):
    """Resubmit the running prompt with a fresh id at the front of the
    queue. Tolerates queue-item tuples longer than 5 elements."""
    q = PromptServer.instance.prompt_queue
    cr = getattr(q, "currently_running", None)
    if cr:
        item = next(iter(cr.values()))
    else:
        running, _ = q.get_current_queue()
        if not running:
            raise RuntimeError("callsheet requeue: no running prompt")
        item = running[0]
    item = tuple(item)
    number, prompt_id, prompt, extra_data, outputs = item[:5]
    tail = item[5:]

    new_number = -PromptServer.instance.number   # front of queue
    PromptServer.instance.number += 1
    new_id = str(uuid.uuid4())
    _PASS_OF[new_id] = current_pass(pid) + 1
    while len(_PASS_OF) > 64:
        _PASS_OF.pop(next(iter(_PASS_OF)))
    q.put((new_number, new_id, prompt, extra_data, outputs) + tail)
