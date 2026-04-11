import logging
import boto3
from botocore.exceptions import ClientError

def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'texas-coast')
    )

def is_processed(file_key: str, config: dict) -> bool:
    s3 = _get_s3_client(config)
    output_bucket = config['storage']['buckets']['output']
    try:
        s3.head_object(Bucket=output_bucket, Key=f"{file_key}.done")
        return True
    except ClientError:
        return False

def commit_result(file_key: str, config: dict, has_motion: bool):
    """
    Smart Commit:
    - If motion: Copies video to output bucket, writes .done, deletes original.
    - If boring: Writes .done, deletes original (TRASHES the video to save space).
    """
    s3 = _get_s3_client(config)
    input_bucket = config['storage']['buckets']['input']
    output_bucket = config['storage']['buckets']['output']
    done_key = f"{file_key}.done"

    try:
        if has_motion:
            logging.warning(f"💾 MOTION KEPT: Archiving {file_key} to processed bucket.")
            copy_source = {'Bucket': input_bucket, 'Key': file_key}
            s3.copy_object(CopySource=copy_source, Bucket=output_bucket, Key=file_key)
        else:
            logging.info(f"🗑️ CLEAR: Deleting {file_key} to save disk space.")
            
        # Write the 0-byte audit log
        s3.put_object(Bucket=output_bucket, Key=done_key, Body=b"")
        
        # Destroy the original
        s3.delete_object(Bucket=input_bucket, Key=file_key)
        
    except Exception as e:
        logging.error(f"Commit failed for {file_key}: {e}")
