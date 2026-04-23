#!/usr/bin/env python3
"""
query_detections.py — search processed_files for clips matching detection criteria.

Useful for finding footage containing specific objects, narrowing by camera,
time window, or disposition. Intended for content editing and review workflows.

Usage:
    python3 query_detections.py [options]

Examples:
    # Find all clips containing a person
    python3 query_detections.py --object person

    # Front-camera clips with a car, archived last month
    python3 query_detections.py --object car --camera F --after 2026-03-01 --before 2026-03-31

    # Everything archived (has detections), any object
    python3 query_detections.py --disposition archived

    # Show unique object labels seen across all clips
    python3 query_detections.py --list-objects
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


def list_all_objects(config):
    """Print every unique object label seen across all archived clips."""
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
        try:
            events = json.loads(events_json) if isinstance(events_json, str) else events_json
            for event in events or []:
                labels.update(event.get('objects', []))
        except (json.JSONDecodeError, TypeError):
            pass

    if not labels:
        print("No detections found in database.")
        return

    print(f"\n{len(labels)} unique object label(s) detected across all clips:\n")
    for label in sorted(labels):
        print(f"  {label}")


def query(config, object_label, camera, disposition, after, before):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses = []
            params = []

            if object_label:
                # JSON_SEARCH returns the path of the first match; IS NOT NULL means found.
                clauses.append(
                    "JSON_SEARCH(events, 'one', %s, NULL, '$[*].objects[*]') IS NOT NULL"
                )
                params.append(object_label)

            if camera:
                clauses.append("camera_id = %s")
                params.append(camera.upper())

            if disposition:
                clauses.append("disposition = %s")
                params.append(disposition)

            if after:
                clauses.append("processed_at >= %s")
                params.append(after.replace(tzinfo=None))

            if before:
                clauses.append("processed_at < %s")
                params.append(before.replace(tzinfo=None))

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            cursor.execute(
                f"SELECT file_key, camera_id, disposition, processed_at, events "
                f"FROM processed_files {where} ORDER BY processed_at DESC",
                params
            )
            return cursor.fetchall()
    finally:
        conn.close()


def _summarise_events(events_json):
    """Return a compact label summary from a JSON events blob."""
    try:
        events = json.loads(events_json) if isinstance(events_json, str) else events_json
        if not events:
            return "—"
        all_labels = []
        for e in events:
            all_labels.extend(e.get('objects', []))
        counts = {}
        for label in all_labels:
            counts[label] = counts.get(label, 0) + 1
        return ", ".join(f"{label}×{n}" if n > 1 else label for label, n in sorted(counts.items()))
    except (json.JSONDecodeError, TypeError):
        return "—"


def main():
    parser = argparse.ArgumentParser(
        description='Query processed_files for clips matching detection criteria.'
    )
    parser.add_argument('--object', metavar='LABEL',
                        help='Object type to search for (e.g. person, car, truck)')
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'],
                        help='Filter by camera ID')
    parser.add_argument('--disposition', choices=['archived', 'quarantined'],
                        help='Filter by disposition')
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

    if not any([args.object, args.camera, args.disposition, args.after, args.before]):
        parser.print_help()
        print("\nProvide at least one filter, or use --list-objects to see available labels.")
        sys.exit(1)

    after_dt  = _parse_date(args.after)  if args.after  else None
    before_dt = _parse_date(args.before) + timedelta(days=1) if args.before else None

    rows = query(config,
                 object_label=args.object,
                 camera=args.camera,
                 disposition=args.disposition,
                 after=after_dt,
                 before=before_dt)

    if not rows:
        print("No matching clips found.")
        return

    print(f"\n{len(rows)} clip(s) found:\n")
    col_w = max(len(r[0]) for r in rows)
    print(f"  {'FILE':<{col_w}}  CAM  DISPOSITION   PROCESSED AT          DETECTIONS")
    print(f"  {'-'*col_w}  ---  ------------  --------------------  ----------")
    for file_key, cam, disp, processed_at, events_json in rows:
        ts = processed_at.strftime('%Y-%m-%d %H:%M:%S') if processed_at else '—'
        summary = _summarise_events(events_json)
        print(f"  {file_key:<{col_w}}  {cam or '?':<3}  {disp:<12}  {ts}  {summary}")
    print()


if __name__ == '__main__':
    main()
