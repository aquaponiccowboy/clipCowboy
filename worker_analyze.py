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
from src.persistence import is_processed, commit_result, record_dlq, ensure_buckets
from src.database import ensure_schema
from src.queue_client import get_channel, publish, ANALYZE_QUEUE, ANALYZE_DLQ
from src.logging_setup import init_logging, discord_notify

config = {}


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


MAX_ATTEMPTS = 3


def handle(ch, method, properties, body):
    msg = json.loads(body)
    mp4_key = msg['mp4_key']
    camera_id = msg['camera_id']

    # Guard against duplicate messages
    if is_processed(mp4_key, config):
        logging.info(f"Already processed, skipping: {mp4_key}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        return

    attempt = (properties.headers or {}).get('x-retry-count', 0) + 1
    logging.info(f"Analyzing: {mp4_key} (attempt {attempt}/{MAX_ATTEMPTS})")

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

        n = len(result.get('events', []))
        discord_notify(f"▸ **[analyze]** {mp4_key} — {n} event{'s' if n != 1 else ''}")

        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        logging.error(f"Analysis failed for {mp4_key} (attempt {attempt}/{MAX_ATTEMPTS}): {e}")
        # Ack the original so it leaves the work queue, then decide retry vs DLQ.
        # We can't modify headers via basic_nack(requeue=True), so we republish manually.
        ch.basic_ack(delivery_tag=method.delivery_tag)
        if attempt < MAX_ATTEMPTS:
            publish(ch, ANALYZE_QUEUE, msg, headers={'x-retry-count': attempt})
            logging.warning(f"Requeued {mp4_key} for retry (attempt {attempt + 1}/{MAX_ATTEMPTS}).")
        else:
            record_dlq(mp4_key, ANALYZE_DLQ, str(e), config)
            publish(ch, ANALYZE_DLQ, {**msg, 'error': str(e)})
            logging.error(f"Sent to DLQ after {MAX_ATTEMPTS} failed attempts: {mp4_key}")

    finally:
        if local_mp4 and os.path.exists(local_mp4):
            os.remove(local_mp4)


if __name__ == '__main__':
    config = load_config()
    init_logging('analyzer')
    ensure_schema(config)
    ensure_buckets(config)
    conn, ch = get_channel(config)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=ANALYZE_QUEUE, on_message_callback=handle)
    logging.info("Analyzer worker ready. Waiting for jobs...")
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        logging.info("Analyzer worker shutting down.")
        conn.close()
