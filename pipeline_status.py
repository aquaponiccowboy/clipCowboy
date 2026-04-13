#!/usr/bin/env python3
"""
pipeline_status.py — live pipeline status

Refreshes every 5 seconds by default.

    python3 pipeline_status.py              # watch mode (5s refresh)
    python3 pipeline_status.py -n 10        # watch mode, 10s refresh
    python3 pipeline_status.py --once       # single snapshot and exit
"""
import argparse
import logging
import sys
import time

import boto3
import yaml
from botocore.exceptions import ClientError

from src.database import get_connection
from src.queue_client import ANALYZE_QUEUE, TRANSCODE_QUEUE, get_channel

logging.basicConfig(level=logging.CRITICAL)

_CLEAR = '\033[2J\033[H'   # ANSI: clear screen + move cursor to top


def load_config(path: str = 'config.yml') -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def queue_depths(config: dict) -> dict:
    try:
        conn, channel = get_channel(config)
        depths = {}
        for q in [TRANSCODE_QUEUE, ANALYZE_QUEUE]:
            r = channel.queue_declare(queue=q, durable=True, passive=True)
            depths[q] = r.method.message_count
        conn.close()
        return depths
    except Exception as e:
        return {'_error': str(e)}


def bucket_counts(config: dict) -> dict:
    storage = config['storage']
    s3 = boto3.client(
        's3',
        endpoint_url=storage.get('endpoint_url'),
        aws_access_key_id=storage.get('access_key'),
        aws_secret_access_key=storage.get('secret_key'),
        region_name=storage.get('region_name', 'us-east-1'),
    )
    counts = {}
    for alias, bucket in storage['buckets'].items():
        try:
            paginator = s3.get_paginator('list_objects_v2')
            n = sum(len(page.get('Contents', [])) for page in paginator.paginate(Bucket=bucket))
            counts[alias] = (bucket, n, None)
        except ClientError as e:
            counts[alias] = (bucket, 0, str(e))
    return counts


def db_stats(config: dict) -> dict:
    try:
        conn = get_connection(config)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM processed_files")
            total = cur.fetchone()[0]
            cur.execute(
                "SELECT disposition, COUNT(*) FROM processed_files GROUP BY disposition"
            )
            by_disp = {row[0]: row[1] for row in cur.fetchall()}
            cur.execute(
                "SELECT camera_id, COUNT(*) FROM processed_files GROUP BY camera_id ORDER BY camera_id"
            )
            by_cam = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return {'total': total, 'by_disposition': by_disp, 'by_camera': by_cam}
    except Exception as e:
        return {'_error': str(e)}


def render(config: dict, interval: int) -> bool:
    """Print one status snapshot.  Returns False if any service errored."""
    ok = True
    lines = []

    # ── header ────────────────────────────────────────────────────────────
    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    lines.append(f"SecurityCowboy pipeline status  —  {ts}  (refreshing every {interval}s, Ctrl+C to exit)")
    lines.append('')

    # ── queues ─────────────────────────────────────────────────────────────
    lines.append('── RabbitMQ queues ────────────────────────────')
    depths = queue_depths(config)
    if '_error' in depths:
        lines.append(f"  error: {depths['_error']}")
        ok = False
    else:
        for q, n in depths.items():
            bar = '█' * min(n, 30) if n else '·'
            lines.append(f"  {q:<22}  {n:>4}  {bar}")

    lines.append('')

    # ── buckets ────────────────────────────────────────────────────────────
    lines.append('── MinIO buckets ──────────────────────────────')
    counts = bucket_counts(config)
    for alias, (bucket, n, err) in counts.items():
        if err:
            lines.append(f"  {alias:<12}  error: {err}")
            ok = False
        else:
            bar = '█' * min(n, 30) if n else '·'
            lines.append(f"  {alias:<12}  {n:>4} files  {bar}")

    lines.append('')

    # ── database ───────────────────────────────────────────────────────────
    lines.append('── Database ───────────────────────────────────')
    stats = db_stats(config)
    if '_error' in stats:
        lines.append(f"  error: {stats['_error']}")
        ok = False
    else:
        lines.append(f"  total processed : {stats['total']}")
        for disp, n in stats['by_disposition'].items():
            lines.append(f"  {disp:<16}  {n}")
        if stats.get('by_camera'):
            cams = '  '.join(f"{k}={v}" for k, v in stats['by_camera'].items())
            lines.append(f"  by camera       : {cams}")

    print('\n'.join(lines), flush=True)
    return ok


def main():
    parser = argparse.ArgumentParser(description='Pipeline status dashboard')
    parser.add_argument('--once', action='store_true', help='single snapshot then exit')
    parser.add_argument('-n', '--interval', type=int, default=5,
                        metavar='SECONDS', help='refresh interval (default 5)')
    args = parser.parse_args()

    config = load_config()

    if args.once:
        ok = render(config, args.interval)
        sys.exit(0 if ok else 1)

    try:
        while True:
            print(_CLEAR, end='', flush=True)
            render(config, args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nExiting.', flush=True)


if __name__ == '__main__':
    main()
