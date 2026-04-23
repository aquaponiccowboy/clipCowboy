import json


def _parse_events(events_json) -> list:
    try:
        return json.loads(events_json) if isinstance(events_json, str) else (events_json or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_time(t: str) -> float:
    return float(str(t).rstrip('s'))


def compute_score(events_json, weights: dict) -> dict:
    """
    Heuristic activity score (0–1) derived from YOLO events already in the DB.

    Components
    ----------
    detection_rate    fraction of clip seconds that had at least one detection
    avg_objects       mean simultaneous detections per event (normalised to 3)
    label_diversity   distinct YOLO labels seen (normalised to 3)
    mean_confidence   average detection confidence
    temporal_coverage how evenly distributed the detections are across the clip

    Default weights sum to 1.0. Override under scoring.weights in config.yml.
    """
    events = _parse_events(events_json)
    n_events = len(events)

    if n_events == 0:
        return {
            'score': 0.0, 'n_events': 0, 'duration_sec': 0.0,
            'detection_rate': 0.0, 'avg_objects': 0.0,
            'label_diversity': 0, 'mean_confidence': 0.0,
            'temporal_coverage': 0.0,
        }

    times, all_dets = [], []
    for event in events:
        try:
            times.append(_parse_time(event['time']))
        except (KeyError, ValueError):
            pass
        dets = event.get('detections') or []
        # backwards-compat with old 'objects' list schema
        if not dets:
            dets = [{'label': o, 'confidence': None} for o in event.get('objects', [])]
        all_dets.extend(dets)

    if not times:
        times = [0.0]

    first_t, last_t = min(times), max(times)
    # last logged event is ~1 s before clip end (logged at ≤1 fps)
    duration_sec = last_t + 1.0

    detection_rate = min(1.0, n_events / duration_sec) if duration_sec > 0 else 0.0
    avg_objects = len(all_dets) / n_events

    labels = {d.get('label', '').lower() for d in all_dets if d.get('label')}
    label_diversity = len(labels)

    confs = [float(d['confidence']) for d in all_dets if d.get('confidence') is not None]
    mean_confidence = sum(confs) / len(confs) if confs else 0.0

    if duration_sec > 1.0:
        temporal_coverage = min(1.0, (last_t - first_t) / (duration_sec - 1.0))
    else:
        temporal_coverage = 1.0

    w = weights
    score = (
        w.get('detection_rate',    0.35) * detection_rate +
        w.get('avg_objects',       0.25) * min(1.0, avg_objects / 3.0) +
        w.get('label_diversity',   0.20) * min(1.0, label_diversity / 3.0) +
        w.get('mean_confidence',   0.10) * mean_confidence +
        w.get('temporal_coverage', 0.10) * temporal_coverage
    )

    return {
        'score':             round(min(1.0, max(0.0, score)), 4),
        'n_events':          n_events,
        'duration_sec':      round(duration_sec, 1),
        'detection_rate':    round(detection_rate, 4),
        'avg_objects':       round(avg_objects, 3),
        'label_diversity':   label_diversity,
        'mean_confidence':   round(mean_confidence, 4),
        'temporal_coverage': round(temporal_coverage, 4),
    }
