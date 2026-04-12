import json
import logging
import os
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


def commit_result(file_key: str, config: dict, result: dict, camera_id: str = None):
    s3 = _get_s3_client(config)
    input_bucket = config['storage']['buckets']['input']
    output_bucket = config['storage']['buckets']['output']
    quarantine_bucket = config['storage']['buckets']['quarantine']

    base_name = file_key.replace('.TS', '')
    has_objects = result.get('has_objects', False)
    disposition = 'archived' if has_objects else 'quarantined'

    try:
        if has_objects and result.get('local_video_path'):
            logging.info(f"ACTION DETECTED: Uploading annotated video for {file_key}...")
            local_path = result['local_video_path']

            s3.upload_file(local_path, output_bucket, f"annotated_{base_name}.mp4")
            s3.put_object(
                Bucket=output_bucket,
                Key=f"{base_name}_events.json",
                Body=json.dumps(result.get('events', []), indent=2)
            )
            os.remove(local_path)

        else:
            logging.info(f"NO ACTION: Moving {file_key} to quarantine for spot-checking.")
            s3.copy_object(
                CopySource={'Bucket': input_bucket, 'Key': file_key},
                Bucket=quarantine_bucket,
                Key=file_key
            )

        s3.delete_object(Bucket=input_bucket, Key=file_key)

    except ClientError as e:
        logging.error(f"Storage operation failed for {file_key}: {e}")
        return

    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO processed_files (file_key, camera_id, has_objects, disposition, events)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    has_objects  = VALUES(has_objects),
                    disposition  = VALUES(disposition),
                    events       = VALUES(events)
            """, (
                file_key,
                camera_id,
                has_objects,
                disposition,
                json.dumps(result.get('events', []))
            ))
        logging.info(f"DB record written for {file_key} [{disposition}].")
    except Exception as e:
        logging.error(f"DB commit failed for {file_key}: {e}")
    finally:
        conn.close()
