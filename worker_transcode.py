#!/usr/bin/env python3
"""
Transcoder Worker: converts .TS files to MP4 and queues them for analysis.

Consumes: footage.transcode
Produces: footage.analyze

Run with: python3 worker_transcode.py
Scale up by running multiple instances.
"""
import yaml
import json
import logging
import os
import pika
from src.transcoder import transcode
from src.persistence import record_dlq, ensure_buckets
from src.database import ensure_schema
from src.queue_client import get_channel, publish, TRANSCODE_QUEUE, ANALYZE_QUEUE, TRANSCODE_DLQ

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config = {}


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


MAX_ATTEMPTS = 3


def handle(ch, method, properties, body):
    msg = json.loads(body)
    ts_key = msg['ts_key']
    camera_id = msg['camera_id']
    mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'

    attempt = (properties.headers or {}).get('x-retry-count', 0) + 1
    logging.info(f"Transcoding: {ts_key} (attempt {attempt}/{MAX_ATTEMPTS})")

    try:
        local_mp4 = transcode(ts_key, config)

        # Clean up the local file — analyzer worker downloads fresh from MinIO
        if local_mp4 and os.path.exists(local_mp4):
            os.remove(local_mp4)

        # Publish to analyze queue whether we just converted or it already existed
        publish(ch, ANALYZE_QUEUE, {'mp4_key': mp4_key, 'camera_id': camera_id})
        logging.info(f"Queued for analysis: {mp4_key}")

        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        logging.error(f"Transcode failed for {ts_key} (attempt {attempt}/{MAX_ATTEMPTS}): {e}")
        # Ack the original so it leaves the work queue, then decide retry vs DLQ.
        # We can't modify headers via basic_nack(requeue=True), so we republish manually.
        ch.basic_ack(delivery_tag=method.delivery_tag)
        if attempt < MAX_ATTEMPTS:
            publish(ch, TRANSCODE_QUEUE, msg, headers={'x-retry-count': attempt})
            logging.warning(f"Requeued {ts_key} for retry (attempt {attempt + 1}/{MAX_ATTEMPTS}).")
        else:
            record_dlq(ts_key, TRANSCODE_DLQ, str(e), config)
            publish(ch, TRANSCODE_DLQ, {**msg, 'error': str(e)})
            logging.error(f"Sent to DLQ after {MAX_ATTEMPTS} failed attempts: {ts_key}")


if __name__ == '__main__':
    config = load_config()
    ensure_schema(config)
    ensure_buckets(config)
    conn, ch = get_channel(config)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=TRANSCODE_QUEUE, on_message_callback=handle)
    logging.info("Transcoder worker ready. Waiting for jobs...")
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        logging.info("Transcoder worker shutting down.")
        conn.close()
