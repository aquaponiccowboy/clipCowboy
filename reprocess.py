#!/usr/bin/env python3
"""
reprocess.py — copy clips from archive back to converted for re-analysis.

The watcher's Phase 2 recovery path picks up any .MP4 in the converted bucket
and re-queues it for the analyze worker automatically. This tool also clears
the processed_files and dlq_files DB records so the pipeline treats the clips
as new rather than skipping them.

Usage:
    python3 reprocess.py [options]

Examples:
    # Reprocess all left-camera clips (dry run first)
    python3 reprocess.py --camera L --dry-run
    python3 reprocess.py --camera L

    # Reprocess a specific recording by filename prefix
    python3 reprocess.py --glob "00011624_*"

    # Reprocess everything archived in a date window
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


def _camera_id(key: str) -> str:
    return key.upper().rsplit('.', 1)[0][-1]


def _list_archive(s3, bucket: str) -> list:
    """Return [(key, last_modified), ...] for all .MP4s in the archive bucket."""
    results = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            if obj['Key'].upper().endswith('.MP4'):
                results.append((obj['Key'], obj['LastModified']))
    return results


def _apply_filters(objects, camera, glob_pattern, after, before):
    out = []
    for key, last_modified in objects:
        if camera and _camera_id(key) != camera.upper():
            continue
        if glob_pattern and not fnmatch.fnmatch(key.lower(), glob_pattern.lower()):
            continue
        if after and last_modified < after:
            continue
        if before and last_modified >= before:
            continue
        out.append(key)
    return out


def _clear_db_records(keys, config):
    """Clear DB state so the pipeline treats these files as new for the current model.

    processed_files: only the current-model row is deleted — rows from previous
    model versions are preserved as history.
    dlq_files: always cleared so the watcher doesn't skip the file.
    """
    model_version = config.get('model', {}).get('path', 'models/yolov8n.pt')
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            for key in keys:
                cursor.execute(
                    "DELETE FROM processed_files WHERE file_key = %s AND model_version = %s",
                    (key, model_version)
                )
                cursor.execute("DELETE FROM dlq_files WHERE file_key = %s", (key,))
    finally:
        conn.close()


def _parse_date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def main():
    parser = argparse.ArgumentParser(
        description='Copy archive clips back to converted for re-analysis with a new model.'
    )
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'],
                        help='Filter by camera ID (last char before extension)')
    parser.add_argument('--glob', metavar='PATTERN',
                        help='Filename glob filter, e.g. "00011624_*"')
    parser.add_argument('--after', metavar='YYYY-MM-DD',
                        help='Only files last modified on or after this date (UTC)')
    parser.add_argument('--before', metavar='YYYY-MM-DD',
                        help='Only files last modified before this date (exclusive, UTC)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be copied without making any changes')
    args = parser.parse_args()

    config = load_config()
    s3 = _get_s3(config)
    archive_bucket   = config['storage']['buckets']['archive']
    converted_bucket = config['storage']['buckets']['converted']

    after_dt  = _parse_date(args.after)  if args.after  else None
    before_dt = _parse_date(args.before) + timedelta(days=1) if args.before else None

    print(f"Scanning {archive_bucket}...")
    try:
        objects = _list_archive(s3, archive_bucket)
    except ClientError as e:
        print(f"ERROR: Could not list archive bucket: {e}")
        sys.exit(1)

    matches = _apply_filters(objects,
                             camera=args.camera,
                             glob_pattern=args.glob,
                             after=after_dt,
                             before=before_dt)

    if not matches:
        print("No matching files found.")
        return

    label = '[DRY RUN] ' if args.dry_run else ''
    verb  = 'Would copy' if args.dry_run else 'Will copy'
    print(f"\n{label}{verb} {len(matches)} file(s) to {converted_bucket}:\n")
    for key in matches:
        print(f"  {key}")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to execute.")
        return

    print()
    copied = []
    failed = 0
    for key in matches:
        try:
            s3.copy_object(
                CopySource={'Bucket': archive_bucket, 'Key': key},
                Bucket=converted_bucket,
                Key=key,
            )
            copied.append(key)
            print(f"  copied: {key}")
        except ClientError as e:
            print(f"  FAILED: {key} — {e}")
            failed += 1

    if copied:
        _clear_db_records(copied, config)

    print(f"\n{len(copied)} copied, {failed} failed.")
    if copied:
        print("The watcher will queue them for re-analysis on its next scan.")
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
