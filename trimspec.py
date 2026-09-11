"""Shared trim/window grammar for video references, continue_video, and
the concat node's labels input.

Entry:   label | label:SPEC
Specs:   (empty)      whole clip
         H:T          drop H from the head, T from the tail
         :A-B         keep frame window [A, B)  (end-exclusive)
         :-N          keep the last N
Tokens are frames (int) or seconds ('1.5s'), interpreted at the SOURCE
clip's fps at resolution time."""


def split_label_spec(entry):
    entry = entry.strip()
    if ":" in entry:
        label, _, spec = entry.partition(":")
        return label.strip(), spec.strip()
    return entry, ""


def _token(t):
    t = t.strip()
    if not t:
        raise ValueError("empty value in trim spec")
    if t.lower().endswith("s"):
        v = float(t[:-1])
        if v < 0:
            raise ValueError(f"negative seconds '{t}'")
        return ("s", v)
    v = int(t)
    if v < 0:
        raise ValueError(f"negative frame count '{t}'")
    return ("f", v)


def parse_spec(spec):
    """Returns ('all',) | ('last', tok) | ('range', tok, tok)
    | ('drop', tok, tok). Raises ValueError on bad syntax."""
    spec = spec.strip()
    if not spec:
        return ("all",)
    if spec.startswith("-"):
        return ("last", _token(spec[1:]))
    if "-" in spec:
        a, b = spec.split("-", 1)
        return ("range", _token(a), _token(b))
    parts = spec.split(":")
    if len(parts) == 1:
        return ("drop", _token(parts[0]), ("f", 0))
    if len(parts) == 2:
        return ("drop", _token(parts[0]), _token(parts[1]))
    raise ValueError(f"bad trim spec '{spec}' (use H:T, A-B, or -N)")


def validate_spec(spec):
    """Returns an error string, or None if the spec parses."""
    try:
        parse_spec(spec)
        return None
    except ValueError as e:
        return str(e)


def _frames(tok, src_fps):
    kind, v = tok
    return int(round(v * src_fps)) if kind == "s" else v


def resolve_window(parsed, n_frames, src_fps):
    """Parsed spec -> (start_frame, end_frame) in source frames.
    Raises ValueError if the window is empty."""
    mode = parsed[0]
    if mode == "all":
        sf, ef = 0, n_frames
    elif mode == "last":
        n = _frames(parsed[1], src_fps)
        sf, ef = max(0, n_frames - n), n_frames
    elif mode == "range":
        sf = _frames(parsed[1], src_fps)
        ef = min(_frames(parsed[2], src_fps), n_frames)
    else:  # drop
        sf = _frames(parsed[1], src_fps)
        ef = n_frames - _frames(parsed[2], src_fps)
    if sf >= ef or sf >= n_frames:
        raise ValueError(f"trim leaves no frames "
                         f"({n_frames} available)")
    return sf, ef
