import os
import logging
import subprocess
import boto3
from botocore.exceptions import ClientError


def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=os.getenv('MINIO_ENDPOINT') or storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'us-east-1')
    )


def transcode(ts_key: str, config: dict) -> str | None:
    """
    Download a .TS file from the input bucket, remux to MP4, upload to the
    converted bucket, then delete the original .TS.

    Returns the local MP4 path so the caller can run analysis immediately
    without re-downloading. Caller is responsible for deleting the local file.

    Returns None if the converted MP4 already exists in MinIO (already done).
    """
    s3 = _get_s3_client(config)
    input_bucket = config['storage']['buckets']['input']
    converted_bucket = config['storage']['buckets']['converted']

    mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'
    safe_name = ts_key.replace('/', '_')
    ts_path = f"temp_{safe_name}"
    mp4_path = ts_path.rsplit('.', 1)[0] + '.mp4'

    # Skip if already converted
    try:
        s3.head_object(Bucket=converted_bucket, Key=mp4_key)
        logging.info(f"Already converted: {mp4_key} — skipping.")
        return None
    except ClientError:
        pass  # doesn't exist yet, proceed

    try:
        logging.info(f"Downloading {ts_key}...")
        s3.download_file(input_bucket, ts_key, ts_path)

        logging.info(f"Remuxing to MP4...")
        result = subprocess.run(
            ['ffmpeg', '-i', ts_path, '-c:v', 'copy', '-c:a', 'copy', '-y', mp4_path],
            capture_output=True, text=True
        )
        os.remove(ts_path)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")

        logging.info(f"Uploading {mp4_key} to converted bucket...")
        s3.upload_file(mp4_path, converted_bucket, mp4_key)

        logging.info(f"Deleting source {ts_key}...")
        s3.delete_object(Bucket=input_bucket, Key=ts_key)

        logging.info(f"Transcode complete: {mp4_key}")
        return mp4_path

    except Exception as e:
        logging.error(f"Transcode failed for {ts_key}: {e}")
        for f in [ts_path, mp4_path]:
            if os.path.exists(f):
                os.remove(f)
        return None


def download_for_analysis(mp4_key: str, config: dict) -> str | None:
    """Download an already-converted MP4 from the converted bucket for analysis."""
    s3 = _get_s3_client(config)
    converted_bucket = config['storage']['buckets']['converted']
    local_path = f"temp_{mp4_key.replace('/', '_')}"
    try:
        logging.info(f"Downloading {mp4_key} for analysis...")
        s3.download_file(converted_bucket, mp4_key, local_path)
        return local_path
    except Exception as e:
        logging.error(f"Download failed for {mp4_key}: {e}")
        return None
