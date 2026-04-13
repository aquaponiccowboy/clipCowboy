#!/usr/bin/env python3
"""
pipeline_status.py — live pipeline status

Shows RabbitMQ queue depths, MinIO bucket file counts, and DB statistics.
Run this while the workers are active to watch files move through the pipeline.

    python3 pipeline_status.py           # single snapshot
    watch -n 5 python3 pipeline_status.py  # refresh every 5s
"""
import sys
import logging
import yaml
import boto3
from botocore.exceptions import ClientError

from src.database import get_connection
from src.queue_client import get_channel, TRANSCODE_QUEUE, ANALYZE_QUEUE

# Suppress pika and boto noise — only show our output
logging.basicConfig(level=logging.CRITICAL)


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


if __name__ == '__main__':
    config = load_config()
    ok = True

    # ── Queues ─────────────────────────────────────────────────────────────
    print("\n── RabbitMQ queues ────────────────────────────")
    depths = queue_depths(config)
    if '_error' in depths:
        print(f"  error: {depths['_error']}")
        ok = False
    else:
        for q, n in depths.items():
            bar = '█' * min(n, 40) if n else '·'
            print(f"  {q:<22}  {n:>5}  {bar}")

    # ── Buckets ─────────────────────────────────────────────────────────────
    print("\n── MinIO buckets ──────────────────────────────")
    counts = bucket_counts(config)
    for alias, (bucket, n, err) in counts.items():
        if err:
            print(f"  {alias:<12}  error: {err}")
            ok = False
        else:
            bar = '█' * min(n, 40) if n else '·'
            print(f"  {alias:<12}  {n:>5} files   {bar}")

    # ── Database ─────────────────────────────────────────────────────────────
    print("\n── Database ───────────────────────────────────")
    stats = db_stats(config)
    if '_error' in stats:
        print(f"  error: {stats['_error']}")
        ok = False
    else:
        print(f"  total processed : {stats['total']}")
        for disp, n in stats['by_disposition'].items():
            print(f"  {disp:<16}  {n}")
        if stats['by_camera']:
            cams = '  '.join(f"{k}={v}" for k, v in stats['by_camera'].items())
            print(f"  by camera       : {cams}")

    print()
    if not ok:
        sys.exit(1)
