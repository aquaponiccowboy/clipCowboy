#!/usr/bin/env python3
"""
purge_quarantine.py — bulk-delete clips from the quarantine bucket.

Quarantine holds no-detection clips kept for spot-checking. Without periodic
purges it grows unbounded. Filters let you target a specific camera, time
window, or filename pattern. Always dry-run first.

Usage:
    python3 purge_quarantine.py [options]

Examples:
    # See what's in quarantine for the front camera (no changes made)
    python3 purge_quarantine.py --camera F --dry-run

    # Purge all front-camera clips, with confirmation prompt
    python3 purge_quarantine.py --camera F

    # Purge everything older than a specific date, skip confirmation
    python3 purge_quarantine.py --before 2026-01-01 --yes
"""
import argparse
import fnmatch
import sys
import yaml
import boto3
from datetime import datetime, timedelta, timezone
from botocore.exceptions import ClientError


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


def _list_quarantine(s3, bucket: str) -> list:
    """Return [(key, last_modified, size_bytes), ...] for all .MP4s in quarantine."""
    results = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            if obj['Key'].upper().endswith('.MP4'):
                results.append((obj['Key'], obj['LastModified'], obj['Size']))
    return results


def _apply_filters(objects, camera, glob_pattern, after, before):
    out = []
    for key, last_modified, size in objects:
        if camera and _camera_id(key) != camera.upper():
            continue
        if glob_pattern and not fnmatch.fnmatch(key.lower(), glob_pattern.lower()):
            continue
        if after and last_modified < after:
            continue
        if before and last_modified >= before:
            continue
        out.append((key, size))
    return out


def _parse_date(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _fmt_size(total_bytes):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if total_bytes < 1024:
            return f"{total_bytes:.1f} {unit}"
        total_bytes /= 1024
    return f"{total_bytes:.1f} PB"


def main():
    parser = argparse.ArgumentParser(
        description='Bulk-delete clips from the quarantine bucket to reclaim SAN space.'
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
                        help='Show what would be deleted without making any changes')
    parser.add_argument('--yes', '-y', action='store_true',
                        help='Skip confirmation prompt')
    args = parser.parse_args()

    config = load_config()
    s3 = _get_s3(config)
    quarantine_bucket = config['storage']['buckets']['quarantine']

    after_dt  = _parse_date(args.after)  if args.after  else None
    before_dt = _parse_date(args.before) + timedelta(days=1) if args.before else None

    print(f"Scanning {quarantine_bucket}...")
    try:
        objects = _list_quarantine(s3, quarantine_bucket)
    except ClientError as e:
        print(f"ERROR: Could not list quarantine bucket: {e}")
        sys.exit(1)

    matches = _apply_filters(objects,
                             camera=args.camera,
                             glob_pattern=args.glob,
                             after=after_dt,
                             before=before_dt)

    if not matches:
        print("No matching files found.")
        return

    total_bytes = sum(size for _, size in matches)
    label = '[DRY RUN] ' if args.dry_run else ''
    verb  = 'Would delete' if args.dry_run else 'Will delete'
    print(f"\n{label}{verb} {len(matches)} file(s) ({_fmt_size(total_bytes)}):\n")
    for key, size in matches:
        print(f"  {key}  ({_fmt_size(size)})")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to execute.")
        return

    if not args.yes:
        print(f"\nThis will permanently delete {len(matches)} file(s) ({_fmt_size(total_bytes)}).")
        answer = input("Type 'yes' to confirm: ").strip().lower()
        if answer != 'yes':
            print("Aborted.")
            return

    print()
    # S3 delete_objects accepts up to 1000 keys per call
    keys = [k for k, _ in matches]
    deleted = 0
    failed = 0
    for i in range(0, len(keys), 1000):
        batch = [{'Key': k} for k in keys[i:i + 1000]]
        try:
            resp = s3.delete_objects(Bucket=quarantine_bucket, Delete={'Objects': batch})
            deleted += len(resp.get('Deleted', []))
            for err in resp.get('Errors', []):
                print(f"  FAILED: {err['Key']} — {err['Message']}")
                failed += 1
        except ClientError as e:
            print(f"  ERROR on batch: {e}")
            failed += len(batch)

    print(f"\n{deleted} deleted, {failed} failed.  Freed ~{_fmt_size(total_bytes)}.")
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
