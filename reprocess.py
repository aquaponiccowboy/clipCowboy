#!/usr/bin/env python3
"""
reprocess.py — copy clips from archive back to converted for re-analysis.

The watcher's Phase 2 recovery path picks up any .MP4 in the converted bucket
and re-queues it for the analyze worker automatically. This tool also clears
the processed_files and dlq_files DB records (for the current model version)
so the pipeline treats the clips as new.

Reads from the database rather than scanning MinIO directly, so it correctly
handles clips that have been sorted into category prefixes by sort_archive.py.

Usage:
    python3 reprocess.py [options]

Examples:
    # Dry run: see what would be reprocessed
    python3 reprocess.py --camera L --dry-run

    # Reprocess all people clips
    python3 reprocess.py --category people

    # Reprocess a specific recording
    python3 reprocess.py --glob "00011624_*"

    # Reprocess everything in a date window
    python3 reprocess.py --after 2026-03-01 --before 2026-03-31 --dry-run
"""
import argparse
import fnmatch
import logging
import sys
import yaml
import boto3
from datetime import datetime, timedelta, timezone
from botocore.exceptions import ClientError

from src.database import get_connection

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


def _get_s3(config):
    s = config['storage']
    return boto3.client('s3',
        endpoint_url=s.get('endpoint_url'),
        aws_access_key_id=s.get('access_key'),
        aws_secret_access_key=s.get('secret_key'),
        region_name=s.get('region_name', 'us-east-1'),
    )


def _parse_date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _camera_id(key: str) -> str:
    return key.upper().rsplit('.', 1)[0][-1]


def _fetch_candidates(config, camera, glob_pattern, category, after, before):
    """Query DB for archived clips matching the given filters."""
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = ["has_objects = 1"], []

            if camera:
                # camera_id stored in DB
                clauses.append("camera_id = %s")
                params.append(camera.upper())

            if category:
                # category maps to sort_prefix (or sort_prefix IS NULL for unsorted)
                if category == 'unsorted':
                    clauses.append("sort_prefix IS NULL")
                else:
                    clauses.append("sort_prefix LIKE %s")
                    params.append(f"%{category}%")

            if after:
                clauses.append("processed_at >= %s")
                params.append(after.replace(tzinfo=None))

            if before:
                clauses.append("processed_at < %s")
                params.append(before.replace(tzinfo=None))

            where = "WHERE " + " AND ".join(clauses)
            cursor.execute(
                f"SELECT id, file_key, sort_prefix FROM processed_files {where} "
                f"ORDER BY processed_at DESC",
                params
            )
            rows = cursor.fetchall()
    finally:
        conn.close()

    # Apply glob filter in Python (against bare filename, not prefix)
    if glob_pattern:
        rows = [
            r for r in rows
            if fnmatch.fnmatch(r[1].lower(), glob_pattern.lower())
        ]

    return rows  # [(id, file_key, sort_prefix), ...]


def _clear_db_records(file_keys, config):
    """Clear current-model processed_files row and dlq_files row for each key."""
    model_version = config.get('model', {}).get('path', 'models/yolov8n.pt')
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            for key in file_keys:
                cursor.execute(
                    "DELETE FROM processed_files WHERE file_key = %s AND model_version = %s",
                    (key, model_version)
                )
                cursor.execute("DELETE FROM dlq_files WHERE file_key = %s", (key,))
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='Copy archive clips back to converted for re-analysis with a new model.'
    )
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'],
                        help='Filter by camera ID')
    parser.add_argument('--category', metavar='NAME',
                        help='Filter by sort category (e.g. people, vehicles, people+vehicles, other, unsorted)')
    parser.add_argument('--glob', metavar='PATTERN',
                        help='Filename glob, e.g. "00011624_*"')
    parser.add_argument('--after', metavar='YYYY-MM-DD',
                        help='Only files processed on or after this date (UTC)')
    parser.add_argument('--before', metavar='YYYY-MM-DD',
                        help='Only files processed before this date (exclusive, UTC)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be copied without making any changes')
    args = parser.parse_args()

    config = load_config()
    s3 = _get_s3(config)
    archive_bucket   = config['storage']['buckets']['archive']
    converted_bucket = config['storage']['buckets']['converted']

    after_dt  = _parse_date(args.after)  if args.after  else None
    before_dt = _parse_date(args.before) + timedelta(days=1) if args.before else None

    rows = _fetch_candidates(config,
                             camera=args.camera,
                             glob_pattern=args.glob,
                             category=args.category,
                             after=after_dt,
                             before=before_dt)

    if not rows:
        print("No matching clips found.")
        return

    label = '[DRY RUN] ' if args.dry_run else ''
    verb  = 'Would copy' if args.dry_run else 'Will copy'
    print(f"\n{label}{verb} {len(rows)} clip(s) to converted:\n")
    for _, file_key, sort_prefix in rows:
        location = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
        print(f"  {location}")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to execute.")
        return

    print()
    copied_keys = []
    failed = 0

    for _, file_key, sort_prefix in rows:
        # Source in archive may be in a category prefix after sort_archive.py
        archive_key = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
        try:
            s3.copy_object(
                CopySource={'Bucket': archive_bucket, 'Key': archive_key},
                Bucket=converted_bucket,
                Key=file_key,       # always flat in converted
            )
            copied_keys.append(file_key)
            print(f"  copied: {archive_key}")
        except ClientError as e:
            print(f"  FAILED: {archive_key} — {e}")
            failed += 1

    if copied_keys:
        _clear_db_records(copied_keys, config)

    print(f"\n{len(copied_keys)} copied, {failed} failed.")
    if copied_keys:
        print("The watcher will queue them for re-analysis on its next scan.")
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
