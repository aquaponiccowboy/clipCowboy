#!/usr/bin/env python3
"""
Analyzer Worker: runs YOLO on converted MP4s and commits results to DB + MinIO.

Consumes: footage.analyze

Run with: python3 worker_analyze.py
Scale up by running multiple instances to parallelize GPU work.
"""
import yaml
import json
import logging
import os
import pika
from src.transcoder import download_for_analysis
from src.analyzer import detect_objects
from src.router import get_camera_context
from src.persistence import is_processed, commit_result
from src.database import ensure_schema
from src.queue_client import get_channel, ANALYZE_QUEUE

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

config = {}


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


def handle(ch, method, properties, body):
    msg = json.loads(body)
    mp4_key = msg['mp4_key']
    camera_id = msg['camera_id']

    # Guard against duplicate messages
    if is_processed(mp4_key, config):
        logging.info(f"Already processed, skipping: {mp4_key}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    logging.info(f"Analyzing: {mp4_key}")

    context = get_camera_context(mp4_key, config)
    if not context:
        logging.warning(f"No camera context for {mp4_key} — acking and skipping.")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    local_mp4 = None
    try:
        local_mp4 = download_for_analysis(mp4_key, config)
        if not local_mp4:
            raise RuntimeError("Download failed")

        result = detect_objects(local_mp4, context['mask_config'], config)
        commit_result(mp4_key, config, result, camera_id=camera_id)

        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        logging.error(f"Analysis failed for {mp4_key}: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    finally:
        if local_mp4 and os.path.exists(local_mp4):
            os.remove(local_mp4)


if __name__ == '__main__':
    config = load_config()
    ensure_schema(config)
    conn, ch = get_channel(config)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=ANALYZE_QUEUE, on_message_callback=handle)
    logging.info("Analyzer worker ready. Waiting for jobs...")
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        logging.info("Analyzer worker shutting down.")
        conn.close()
