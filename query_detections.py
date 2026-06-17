#!/usr/bin/env python3
"""
query_detections.py — search processed_files for clips matching detection criteria.

Useful for finding footage containing specific objects, narrowing by camera,
time window, disposition, or confidence. Intended for content editing and
review workflows.

Usage:
    python3 query_detections.py [options]

Examples:
    # See all unique object labels recorded in the DB
    python3 query_detections.py --list-objects

    # All clips containing a person at ≥90% confidence
    python3 query_detections.py --object person --min-confidence 0.9

    # Front-camera clips with a car, archived last month
    python3 query_detections.py --object car --camera F --after 2026-03-01 --before 2026-03-31

    # Everything archived (has detections), any object
    python3 query_detections.py --disposition archived
"""
import argparse
import json
import sys
import yaml
from datetime import datetime, timedelta, timezone

from src.database import get_connection


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


def _parse_date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _parse_events(events_json):
    """Return parsed events list regardless of old or new schema."""
    try:
        return json.loads(events_json) if isinstance(events_json, str) else (events_json or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _event_matches(event: dict, label_filter: str, min_conf: float) -> bool:
    """Return True if this event contains a matching detection."""
    detections = event.get('detections')
    if detections is not None:
        # New schema: list of {label, confidence, box}
        for d in detections:
            if label_filter and d.get('label', '').lower() != label_filter.lower():
                continue
            if d.get('confidence', 1.0) >= min_conf:
                return True
        return False
    else:
        # Old schema: {"time": "Xs", "objects": ["person", ...]}
        objects = [o.lower() for o in event.get('objects', [])]
        if label_filter:
            return label_filter.lower() in objects
        return bool(objects)


def _clip_matches(events: list, label_filter: str, min_conf: float) -> bool:
    return any(_event_matches(e, label_filter, min_conf) for e in events)


def list_all_objects(config):
    """Print every unique object label seen across all clips."""
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT events FROM processed_files WHERE has_objects = 1 AND events IS NOT NULL"
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    labels = set()
    for (events_json,) in rows:
        for event in _parse_events(events_json):
            detections = event.get('detections')
            if detections is not None:
                labels.update(d['label'] for d in detections)
            else:
                labels.update(event.get('objects', []))

    if not labels:
        print("No detections found in database.")
        return

    print(f"\n{len(labels)} unique label(s) detected across all clips:\n")
    for label in sorted(labels):
        print(f"  {label}")


def query(config, object_label, camera, disposition, category, after, before):
    """Return all rows that pass the SQL-level filters (label/conf filtering done in Python)."""
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = [], []

            if object_label:
                clauses.append(
                    "JSON_SEARCH(events, 'one', %s, NULL, '$[*].detections[*].label') IS NOT NULL"
                    " OR JSON_SEARCH(events, 'one', %s, NULL, '$[*].objects[*]') IS NOT NULL"
                )
                params += [object_label, object_label]

            if camera:
                clauses.append("camera_id = %s")
                params.append(camera.upper())

            if disposition:
                clauses.append("disposition = %s")
                params.append(disposition)

            if category:
                if category == 'unsorted':
                    clauses.append("sort_prefix IS NULL AND has_objects = 1")
                elif category == 'other':
                    clauses.append("sort_prefix = 'other'")
                else:
                    clauses.append("sort_prefix LIKE %s")
                    params.append(f"%{category}%")

            if after:
                clauses.append("processed_at >= %s")
                params.append(after.replace(tzinfo=None))

            if before:
                clauses.append("processed_at < %s")
                params.append(before.replace(tzinfo=None))

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            cursor.execute(
                f"SELECT file_key, camera_id, disposition, processed_at, events, model_version, sort_prefix "
                f"FROM processed_files {where} ORDER BY processed_at DESC",
                params
            )
            return cursor.fetchall()
    finally:
        conn.close()


def _summarise(events: list, label_filter: str, min_conf: float) -> str:
    """Compact per-clip detection summary, respecting confidence filter."""
    counts = {}
    for event in events:
        detections = event.get('detections')
        if detections is not None:
            for d in detections:
                if label_filter and d.get('label', '').lower() != label_filter.lower():
                    continue
                if d.get('confidence', 1.0) < min_conf:
                    continue
                counts[d['label']] = counts.get(d['label'], 0) + 1
        else:
            for obj in event.get('objects', []):
                if label_filter and obj.lower() != label_filter.lower():
                    continue
                counts[obj] = counts.get(obj, 0) + 1
    if not counts:
        return '—'
    return ', '.join(
        f"{lbl}×{n}" if n > 1 else lbl
        for lbl, n in sorted(counts.items())
    )


def main():
    parser = argparse.ArgumentParser(
        description='Query processed_files for clips matching detection criteria.'
    )
    parser.add_argument('--object', metavar='LABEL',
                        help='Object type to search for (e.g. person, car, dog)')
    parser.add_argument('--min-confidence', metavar='0.0-1.0', type=float, default=0.0,
                        help='Minimum detection confidence (default: 0.0, show all)')
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'],
                        help='Filter by camera ID')
    parser.add_argument('--disposition', choices=['archived', 'quarantined'],
                        help='Filter by disposition')
    parser.add_argument('--category', metavar='NAME',
                        help='Filter by sort category (e.g. people, vehicles, people+vehicles, other, unsorted)')
    parser.add_argument('--after', metavar='YYYY-MM-DD',
                        help='Only clips processed on or after this date (UTC)')
    parser.add_argument('--before', metavar='YYYY-MM-DD',
                        help='Only clips processed before this date (exclusive, UTC)')
    parser.add_argument('--list-objects', action='store_true',
                        help='Print all unique object labels seen in the DB and exit')
    args = parser.parse_args()

    config = load_config()

    if args.list_objects:
        list_all_objects(config)
        return

    if not any([args.object, args.camera, args.disposition, args.category, args.after, args.before]):
        parser.print_help()
        print("\nProvide at least one filter, or use --list-objects to see available labels.")
        sys.exit(1)

    after_dt  = _parse_date(args.after)  if args.after  else None
    before_dt = _parse_date(args.before) + timedelta(days=1) if args.before else None
    min_conf  = args.min_confidence

    rows = query(config,
                 object_label=args.object,
                 camera=args.camera,
                 disposition=args.disposition,
                 category=args.category,
                 after=after_dt,
                 before=before_dt)

    # Apply confidence filter in Python (can't do it cleanly in SQL against nested JSON)
    if args.object or min_conf > 0.0:
        rows = [
            r for r in rows
            if _clip_matches(_parse_events(r[4]), args.object, min_conf)
        ]

    if not rows:
        print("No matching clips found.")
        return

    print(f"\n{len(rows)} clip(s) found:\n")
    col_w  = max(len(r[0]) for r in rows)
    cat_w  = max((len(r[6] or 'unsorted') for r in rows), default=8)
    print(f"  {'FILE':<{col_w}}  CAM  {'CATEGORY':<{cat_w}}  PROCESSED AT          DETECTIONS")
    print(f"  {'-'*col_w}  ---  {'-'*cat_w}  --------------------  ----------")
    for file_key, cam, disp, processed_at, events_json, model_ver, sort_prefix in rows:
        ts      = processed_at.strftime('%Y-%m-%d %H:%M:%S') if processed_at else '—'
        summary = _summarise(_parse_events(events_json), args.object, min_conf)
        cat     = sort_prefix or 'unsorted'
        print(f"  {file_key:<{col_w}}  {cam or '?':<3}  {cat:<{cat_w}}  {ts}  {summary}")
    print()


if __name__ == '__main__':
    main()
