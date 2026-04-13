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
from src.queue_client import get_channel, publish, TRANSCODE_QUEUE, ANALYZE_QUEUE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config = {}


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


def handle(ch, method, properties, body):
    msg = json.loads(body)
    ts_key = msg['ts_key']
    camera_id = msg['camera_id']
    mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'

    logging.info(f"Transcoding: {ts_key}")

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
        logging.error(f"Transcode failed for {ts_key}: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


if __name__ == '__main__':
    config = load_config()
    conn, ch = get_channel(config)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=TRANSCODE_QUEUE, on_message_callback=handle)
    logging.info("Transcoder worker ready. Waiting for jobs...")
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        logging.info("Transcoder worker shutting down.")
        conn.close()
