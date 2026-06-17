import json


def _parse_events(events_json) -> list:
    try:
        return json.loads(events_json) if isinstance(events_json, str) else (events_json or [])
    except (json.JSONDecodeError, TypeError):
        return []


def find_windows(events_json, hl_config: dict) -> list:
    """
    Find high-activity windows within a clip from its YOLO events.

    Active seconds are merged into contiguous windows, padded, and split
    if they exceed max_duration. Returns list of dicts sorted by window
    activity score descending:
        {'start', 'end', 'duration', 'score'}
    """
    min_dur   = float(hl_config.get('min_duration', 10))
    max_dur   = float(hl_config.get('max_duration', 60))
    merge_gap = float(hl_config.get('merge_gap', 5))
    pad       = float(hl_config.get('pad', 2))

    events = _parse_events(events_json)
    points = []
    for event in events:
        try:
            t = float(str(event['time']).rstrip('s'))
        except (KeyError, ValueError):
            continue
        dets = event.get('detections') or [
            {'confidence': 0.5} for _ in event.get('objects', [])
        ]
        activity = sum(float(d.get('confidence', 0.5)) for d in dets)
        if activity > 0:
            points.append((t, activity))

    if not points:
        return []

    points.sort(key=lambda x: x[0])

    # Merge nearby active points into contiguous raw windows
    raw = []
    ws, we, wscore = points[0][0], points[0][0], points[0][1]
    for t, a in points[1:]:
        if t - we <= merge_gap:
            we, wscore = t, wscore + a
        else:
            raw.append((ws, we, wscore))
            ws, we, wscore = t, t, a
    raw.append((ws, we, wscore))

    result = []
    for ws, we, wscore in raw:
        start = max(0.0, ws - pad)
        end   = we + pad
        dur   = end - start

        # Extend short windows to min_duration
        if dur < min_dur:
            center = (start + end) / 2
            start  = max(0.0, center - min_dur / 2)
            end    = start + min_dur
            dur    = min_dur

        # Split long windows into max_duration chunks
        if dur > max_dur:
            pos = start
            while pos < end - 1.0:
                chunk_end = min(pos + max_dur, end)
                chunk_dur = chunk_end - pos
                result.append({
                    'start':    round(pos, 1),
                    'end':      round(chunk_end, 1),
                    'duration': round(chunk_dur, 1),
                    'score':    round(wscore * chunk_dur / dur, 3),
                })
                pos = chunk_end
        else:
            result.append({
                'start':    round(start, 1),
                'end':      round(end, 1),
                'duration': round(dur, 1),
                'score':    round(wscore, 3),
            })

    return sorted(result, key=lambda x: x['score'], reverse=True)
