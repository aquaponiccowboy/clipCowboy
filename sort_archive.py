#!/usr/bin/env python3
"""
sort_archive.py — organise archived clips into content-based bins.

Reads detection records from the database, maps YOLO labels to the categories
defined in config.yml under `sort.categories`, then moves each clip within the
archive bucket from a flat key to a prefixed key:

    Before:  security-camera-archive/00011624_105017L.mp4
    After:   security-camera-archive/people/00011624_105017L.mp4

Clips matching multiple categories get a combined bin:
    people+vehicles/00011624_105017L.mp4

Clips with detections that match no configured category go to:
    other/00011624_105017L.mp4

Clips with no detections (quarantine) are not touched.

Run this after an overnight processing batch.

Usage:
    python3 sort_archive.py --dry-run     # preview without moving anything
    python3 sort_archive.py               # execute
    python3 sort_archive.py --resort      # re-sort already-sorted clips too
"""
import argparse
import json
import logging
import sys
import yaml
import boto3
from botocore.exceptions import ClientError

from src.database import get_connection
from src.config import load_config

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')




def _get_s3(config):
    s = config['storage']
    return boto3.client('s3',
        endpoint_url=s.get('endpoint_url'),
        aws_access_key_id=s.get('access_key'),
        aws_secret_access_key=s.get('secret_key'),
        region_name=s.get('region_name', 'us-east-1'),
    )


def _build_category_map(config):
    """Return {category_name: frozenset_of_labels} from config."""
    categories = config.get('sort', {}).get('categories', {})
    return {
        name: frozenset(lbl.lower() for lbl in cfg.get('labels', []))
        for name, cfg in categories.items()
    }


def _parse_events(events_json):
    try:
        return json.loads(events_json) if isinstance(events_json, str) else (events_json or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _labels_in_clip(events):
    """Collect all unique lowercase YOLO labels seen across all events."""
    labels = set()
    for event in events:
        detections = event.get('detections')
        if detections is not None:
            labels.update(d['label'].lower() for d in detections)
        else:
            labels.update(o.lower() for o in event.get('objects', []))
    return labels


def _build_label_category_map(config) -> dict:
    """'person' → 'people', 'car' → 'vehicles', etc."""
    result = {}
    for cat, cfg in config.get('sort', {}).get('categories', {}).items():
        for label in cfg.get('labels', []):
            result[label.lower()] = cat
    return result


def _determine_prefix(labels, category_map):
    """Map a set of labels to a sort prefix. Returns e.g. 'people+vehicles'."""
    matched = sorted(
        name for name, label_set in category_map.items()
        if labels & label_set
    )
    return '+'.join(matched) if matched else 'other'


def _names_for_category(events: list, category: str, label_category_map: dict) -> set:
    """Collect distinct recognized names from detections that map to `category`."""
    names = set()
    for event in events:
        for det in event.get('detections', []):
            if label_category_map.get(det.get('label', '').lower()) == category:
                name = det.get('name')
                if name:
                    names.add(name)
    return names


def _fetch_unsorted(config, resort):
    """Return [(id, file_key, sort_prefix, events_json), ...] to be sorted."""
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            if resort:
                cursor.execute(
                    "SELECT id, file_key, sort_prefix, events "
                    "FROM processed_files WHERE has_objects = 1"
                )
            else:
                cursor.execute(
                    "SELECT id, file_key, sort_prefix, events "
                    "FROM processed_files WHERE has_objects = 1 AND sort_prefix IS NULL"
                )
            return cursor.fetchall()
    finally:
        conn.close()


def _update_sort_prefix(row_id, prefix, config):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE processed_files SET sort_prefix = %s WHERE id = %s",
                (prefix, row_id)
            )
    finally:
        conn.close()


def _move_in_s3(s3, bucket, old_key, new_key):
    """Copy to new key then delete old key within the same bucket."""
    s3.copy_object(
        CopySource={'Bucket': bucket, 'Key': old_key},
        Bucket=bucket,
        Key=new_key,
    )
    s3.delete_object(Bucket=bucket, Key=old_key)


def main():
    parser = argparse.ArgumentParser(
        description='Sort archived clips into content-based bins within the archive bucket.'
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be moved without making any changes')
    parser.add_argument('--resort', action='store_true',
                        help='Re-sort clips that have already been sorted (e.g. after updating categories)')
    args = parser.parse_args()

    config = load_config()
    category_map = _build_category_map(config)

    if not category_map:
        print("No sort.categories defined in config.yml. Nothing to do.")
        print("Add a sort: section — see config.yml.example.")
        sys.exit(1)

    print(f"Categories: {', '.join(sorted(category_map))}")

    recognition_enabled = config.get('recognition', {}).get('enabled', False)
    label_category_map = _build_label_category_map(config) if recognition_enabled else {}

    rows = _fetch_unsorted(config, args.resort)
    if not rows:
        print("No unsorted archived clips found.")
        return

    # Build sort plan: prefix → [(id, file_key, current_prefix)]
    plan = {}
    for row_id, file_key, current_prefix, events_json in rows:
        events = _parse_events(events_json)
        labels = _labels_in_clip(events)
        prefix = _determine_prefix(labels, category_map)

        # Named sub-prefix: only for single-category clips when recognition is on.
        # people/Zeke/ or people/Zeke+Neighbor/ rather than people/
        if recognition_enabled and '+' not in prefix and prefix != 'other':
            names = _names_for_category(events, prefix, label_category_map)
            if names:
                prefix = f"{prefix}/{'+'.join(sorted(names))}"

        plan.setdefault(prefix, []).append((row_id, file_key, current_prefix))

    total = sum(len(v) for v in plan.values())
    label = '[DRY RUN] ' if args.dry_run else ''
    verb  = 'Would sort' if args.dry_run else 'Sorting'

    print(f"\n{label}{verb} {total} clip(s):\n")
    for prefix in sorted(plan):
        print(f"  {prefix:<30}  {len(plan[prefix])} clip(s)")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to execute.")
        return

    print()
    s3            = _get_s3(config)
    archive_bucket = config['storage']['buckets']['archive']
    moved = 0
    failed = 0

    for prefix, items in sorted(plan.items()):
        for row_id, file_key, current_prefix in items:
            # Current location in archive — flat if unsorted, prefixed if resorting
            old_key = f"{current_prefix}/{file_key}" if current_prefix else file_key
            new_key = f"{prefix}/{file_key}"

            if old_key == new_key:
                # Already in the right place (resort with unchanged categories)
                _update_sort_prefix(row_id, prefix, config)
                moved += 1
                continue

            try:
                _move_in_s3(s3, archive_bucket, old_key, new_key)
                _update_sort_prefix(row_id, prefix, config)
                moved += 1
            except ClientError as e:
                print(f"  FAILED: {file_key} — {e}")
                failed += 1

    print(f"{moved} sorted, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
