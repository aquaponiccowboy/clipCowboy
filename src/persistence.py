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


def ensure_buckets(config: dict):
    """Create any configured MinIO buckets that don't already exist."""
    s3 = _get_s3_client(config)
    for alias, bucket in config['storage']['buckets'].items():
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as e:
            if e.response['Error']['Code'] in ('404', 'NoSuchBucket'):
                s3.create_bucket(Bucket=bucket)
                logging.info(f"Created bucket: {bucket} ({alias})")
            else:
                raise


def is_in_dlq(file_key: str, config: dict) -> bool:
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM dlq_files WHERE file_key = %s",
                (file_key,)
            )
            return cursor.fetchone() is not None
    finally:
        conn.close()


def record_dlq(file_key: str, queue: str, error: str, config: dict):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO dlq_files (file_key, queue, error)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       queue = VALUES(queue),
                       error = VALUES(error),
                       failed_at = CURRENT_TIMESTAMP""",
                (file_key, queue, error)
            )
        logging.warning(f"DLQ record written for {file_key} [{queue}].")
    except Exception as e:
        logging.error(f"Failed to write DLQ record for {file_key}: {e}")
    finally:
        conn.close()


def is_processed(file_key: str, config: dict) -> bool:
    model_version = config.get('model', {}).get('path', 'models/yolov8n.pt')
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM processed_files WHERE file_key = %s AND model_version = %s",
                (file_key, model_version)
            )
            return cursor.fetchone() is not None
    finally:
        conn.close()


def _delete_db_record(file_key: str, config: dict):
    """Roll back a just-written processed_files record so the file remains retryable."""
    model_version = config.get('model', {}).get('path', 'models/yolov8n.pt')
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM processed_files WHERE file_key = %s AND model_version = %s",
                (file_key, model_version)
            )
    except Exception as e:
        logging.error(f"Failed to roll back DB record for {file_key}: {e}")
    finally:
        conn.close()


def commit_result(file_key: str, config: dict, result: dict, camera_id: str = None):
    s3 = _get_s3_client(config)
    buckets = config['storage']['buckets']
    converted_bucket  = buckets['converted']
    archive_bucket    = buckets['archive']
    annotated_bucket  = buckets['annotated']
    quarantine_bucket = buckets['quarantine']

    has_objects   = result.get('has_objects', False)
    disposition   = 'archived' if has_objects else 'quarantined'
    model_version = config.get('model', {}).get('path', 'models/yolov8n.pt')

    # Phase 1: DB record first.
    # If this fails the file stays in `converted` and the worker retries cleanly.
    # Writing last (the old order) risked a file being moved with no DB record.
    # Each (file_key, model_version) pair gets its own row so reprocessing with
    # a new model appends history rather than overwriting it.
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO processed_files
                    (file_key, model_version, camera_id, has_objects, disposition, events)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    has_objects  = VALUES(has_objects),
                    disposition  = VALUES(disposition),
                    events       = VALUES(events),
                    sort_prefix  = NULL
            """, (
                file_key,
                model_version,
                camera_id,
                has_objects,
                disposition,
                json.dumps(result.get('events', []))
            ))
        logging.info(f"DB record written for {file_key} [{disposition}] model={model_version}.")
    except Exception as e:
        logging.error(f"DB commit failed for {file_key}: {e}")
        raise
    finally:
        conn.close()

    # Phase 2: move the file in S3.
    # On failure, roll back the DB record so the file in `converted` stays
    # visible and the worker can retry without is_processed() blocking it.
    try:
        if has_objects:
            logging.info(f"ACTION DETECTED: Archiving {file_key}...")
            s3.copy_object(
                CopySource={'Bucket': converted_bucket, 'Key': file_key},
                Bucket=archive_bucket,
                Key=file_key
            )

            # Annotated copy — only present when save_annotated: true in config.
            if result.get('local_video_path'):
                local_path = result['local_video_path']
                logging.info(f"Uploading annotated video for {file_key}...")
                s3.upload_file(local_path, annotated_bucket, file_key)
                os.remove(local_path)

        else:
            logging.info(f"NO ACTION: Moving {file_key} to quarantine for spot-checking.")
            s3.copy_object(
                CopySource={'Bucket': converted_bucket, 'Key': file_key},
                Bucket=quarantine_bucket,
                Key=file_key
            )

        # File is confirmed in archive or quarantine — remove from converted.
        s3.delete_object(Bucket=converted_bucket, Key=file_key)

    except ClientError as e:
        logging.error(f"Storage operation failed for {file_key}: {e}")
        _delete_db_record(file_key, config)
        raise
