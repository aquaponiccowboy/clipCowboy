import logging
import boto3
from botocore.exceptions import ClientError


def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'us-east-1')
    )


def _scan_bucket(s3, bucket: str, extension: str) -> list:
    targets = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get('Contents', []):
            if obj['Key'].upper().endswith(extension.upper()):
                targets.append(obj['Key'])
    return targets


def get_raw_keys(config: dict) -> list:
    """Return .TS files from the input bucket (need transcoding)."""
    s3 = _get_s3_client(config)
    bucket = config['storage']['buckets']['input']
    try:
        keys = _scan_bucket(s3, bucket, '.TS')
        logging.info(f"Input bucket: {len(keys)} .TS files found.")
        return keys
    except ClientError as e:
        logging.error(f"Ingestion error scanning input bucket: {e}")
        return []


def get_input_mp4_keys(config: dict) -> list:
    """Return .mp4 files from the input bucket (content footage — skip transcoding)."""
    s3 = _get_s3_client(config)
    bucket = config['storage']['buckets']['input']
    try:
        keys = _scan_bucket(s3, bucket, '.mp4')
        if keys:
            logging.info(f"Input bucket: {len(keys)} .MP4 files found (content footage).")
        return keys
    except ClientError as e:
        logging.error(f"Ingestion error scanning input bucket for MP4s: {e}")
        return []


def get_converted_keys(config: dict) -> list:
    """Return .MP4 files from the converted bucket (ready for analysis)."""
    s3 = _get_s3_client(config)
    bucket = config['storage']['buckets']['converted']
    try:
        keys = _scan_bucket(s3, bucket, '.mp4')
        logging.info(f"Converted bucket: {len(keys)} .MP4 files found.")
        return keys
    except ClientError as e:
        logging.error(f"Ingestion error scanning converted bucket: {e}")
        return []
