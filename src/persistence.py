import json
import logging
import boto3
from botocore.exceptions import ClientError
from src.database import get_connection


def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'us-east-1')
    )


def is_processed(file_key: str, config: dict) -> bool:
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM processed_files WHERE file_key = %s",
                (file_key,)
            )
            return cursor.fetchone() is not None
    finally:
        conn.close()


def commit_result(file_key: str, config: dict, has_motion: bool, camera_id: str = None, events: list = None):
    """
    Smart Commit:
    - If motion: copies video to output bucket, then deletes original.
    - If boring: deletes original to save space.
    - Either way: writes a record to MariaDB so we never reprocess.
    """
    s3 = _get_s3_client(config)
    input_bucket = config['storage']['buckets']['input']
    output_bucket = config['storage']['buckets']['output']

    try:
        if has_motion:
            logging.warning(f"MOTION KEPT: Archiving {file_key} to processed bucket.")
            s3.copy_object(
                CopySource={'Bucket': input_bucket, 'Key': file_key},
                Bucket=output_bucket,
                Key=file_key
            )
        else:
            logging.info(f"CLEAR: Deleting {file_key} to save disk space.")

        s3.delete_object(Bucket=input_bucket, Key=file_key)

    except ClientError as e:
        logging.error(f"Storage operation failed for {file_key}: {e}")
        return

    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO processed_files (file_key, camera_id, has_motion, events)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    has_motion = VALUES(has_motion),
                    events     = VALUES(events)
            """, (file_key, camera_id, has_motion, json.dumps(events or [])))
        logging.info(f"DB record written for {file_key}.")
    except Exception as e:
        logging.error(f"DB commit failed for {file_key}: {e}")
    finally:
        conn.close()
