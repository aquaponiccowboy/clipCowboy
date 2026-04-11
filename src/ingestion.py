import logging
import boto3
from botocore.exceptions import ClientError

def get_target_keys(config: dict) -> list:
    """
    Phase 1: Ingestion (Object Storage)
    Connects to MinIO and returns a list of object keys (.TS files) from the input bucket.
    """
    targets = []
    storage_cfg = config.get('storage', {})

    try:
        # Initialize S3 client pointing to local MinIO
        s3_client = boto3.client(
            's3',
            endpoint_url=storage_cfg.get('endpoint_url'),
            aws_access_key_id=storage_cfg.get('access_key'),
            aws_secret_access_key=storage_cfg.get('secret_key'),
            region_name=storage_cfg.get('region_name', 'us-east-1')
        )

        input_bucket = storage_cfg.get('buckets', {}).get('input')

        if not input_bucket:
            logging.error("Ingestion Error: No input bucket defined in config.yml")
            return targets

        logging.info(f"Scanning MinIO bucket: {input_bucket}")

        # Paginate through the bucket (in case there are >1000 files)
        paginator = s3_client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=input_bucket)

        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    key = obj['Key']
                    if key.upper().endswith('.TS'):
                        targets.append(key)

        logging.info(f"Ingestion complete. Found {len(targets)} targets.")

    except ClientError as e:
        logging.error(f"MinIO Connection Error: {e}")
    except Exception as e:
        logging.error(f"Unexpected Ingestion Error: {e}")

    return targets
