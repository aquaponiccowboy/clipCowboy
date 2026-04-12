import os
import logging
import subprocess
import boto3


def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'us-east-1')
    )


def download_and_convert(file_key: str, config: dict) -> str:
    """
    Download a .TS file from MinIO and remux it to MP4.
    Returns the local path to the converted MP4.
    Caller is responsible for deleting the file when done.
    """
    s3 = _get_s3_client(config)
    bucket = config['storage']['buckets']['input']
    safe_name = file_key.replace('/', '_')
    ts_path = f"temp_{safe_name}"
    mp4_path = ts_path.rsplit('.', 1)[0] + '.mp4'

    logging.info(f"Downloading {file_key}...")
    s3.download_file(bucket, file_key, ts_path)

    logging.info(f"Remuxing {file_key} to MP4...")
    result = subprocess.run(
        ['ffmpeg', '-i', ts_path, '-c:v', 'copy', '-c:a', 'copy', '-y', mp4_path],
        capture_output=True, text=True
    )

    os.remove(ts_path)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {file_key}:\n{result.stderr}")

    logging.info(f"Remux complete: {mp4_path}")
    return mp4_path
