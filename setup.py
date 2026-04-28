#!/usr/bin/env python3
"""
setup.py — one-time infrastructure setup

Creates MinIO buckets, verifies MariaDB schema, and confirms RabbitMQ
connectivity.  Run this once before starting the pipeline workers.

    python3 setup.py
"""
import sys
import logging
import boto3
from botocore.exceptions import ClientError

from src.config import load_config
from src.database import ensure_schema
from src.queue_client import get_channel

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def setup_minio(config: dict) -> bool:
    storage = config['storage']
    s3 = boto3.client(
        's3',
        endpoint_url=storage.get('endpoint_url'),
        aws_access_key_id=storage.get('access_key'),
        aws_secret_access_key=storage.get('secret_key'),
        region_name=storage.get('region_name', 'us-east-1'),
    )

    ok = True
    for alias, bucket in storage['buckets'].items():
        try:
            s3.head_bucket(Bucket=bucket)
            print(f"  [ok]      {bucket}  ({alias})")
        except ClientError as e:
            if e.response['Error']['Code'] in ('404', 'NoSuchBucket'):
                try:
                    s3.create_bucket(Bucket=bucket)
                    print(f"  [created] {bucket}  ({alias})")
                except ClientError as ce:
                    print(f"  [FAIL]    {bucket}  ({alias}): {ce}")
                    ok = False
            else:
                print(f"  [FAIL]    {bucket}  ({alias}): {e}")
                ok = False
    return ok


def setup_database(config: dict) -> bool:
    try:
        ensure_schema(config)
        print("  [ok]      schema verified")
        return True
    except Exception as e:
        print(f"  [FAIL]    {e}")
        return False


def check_rabbitmq(config: dict) -> bool:
    try:
        conn, _ = get_channel(config)
        conn.close()
        print("  [ok]      connection established, queues declared")
        return True
    except Exception as e:
        print(f"  [FAIL]    {e}")
        return False


if __name__ == '__main__':
    config = load_config()
    results = []

    print("\n── MinIO buckets ──────────────────────────────")
    results.append(setup_minio(config))

    print("\n── MariaDB schema ─────────────────────────────")
    results.append(setup_database(config))

    print("\n── RabbitMQ ───────────────────────────────────")
    results.append(check_rabbitmq(config))

    print()
    if all(results):
        print("All services ready.  Start workers with:")
        print("  python3 watcher.py")
        print("  python3 worker_transcode.py")
        print("  python3 worker_analyze.py")
    else:
        print("One or more services failed.  Check docker-compose logs.")
        sys.exit(1)
