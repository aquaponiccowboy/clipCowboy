import logging
import boto3
import os
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

def commit_result(file_key: str, config: dict, result: dict):
    s3 = _get_s3_client(config)
    input_bucket = config['storage']['buckets']['input']
    output_bucket = config['storage']['buckets']['output']
    quarantine_bucket = config['storage']['buckets']['quarantine']
    
    done_key = f"{file_key}.done"
    base_name = file_key.replace('.TS', '')

    try:
        if result.get("has_objects") and result.get("local_video_path"):
            logging.info(f"✅ ACTION DETECTED: Uploading annotated video for {file_key}...")
            
            new_video_name = f"annotated_{base_name}.mp4"
            local_path = result["local_video_path"]
            
            # 1. Upload the newly rendered video with bounding boxes
            s3.upload_file(local_path, output_bucket, new_video_name)
            
            # 2. Upload the JSON event ledger as a text file so you can read the timestamps
            import json
            s3.put_object(Bucket=output_bucket, Key=f"{base_name}_events.json", Body=json.dumps(result["events"], indent=2))
            
            # 3. Clean up the local hard drive
            os.remove(local_path)
            
        else:
            logging.info(f"🛡️ NO ACTION: Moving {file_key} to Quarantine for spot-checking.")
            # Move original file to quarantine instead of deleting
            copy_source = {'Bucket': input_bucket, 'Key': file_key}
            s3.copy_object(CopySource=copy_source, Bucket=quarantine_bucket, Key=file_key)
            
        # Write the .done marker to output bucket to prevent double-processing
        s3.put_object(Bucket=output_bucket, Key=done_key, Body=b"")
        
        # Finally, remove from the unprocessed queue
        s3.delete_object(Bucket=input_bucket, Key=file_key)
        
    except Exception as e:
        logging.error(f"Commit failed for {file_key}: {e}")
