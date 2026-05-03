#!/usr/bin/env python3
import yaml
import json
import logging
import os
import pika
from src.transcoder import transcode
from src.persistence import record_dlq, ensure_buckets
from src.database import ensure_schema
from src.queue_client import get_channel, publish, TRANSCODE_QUEUE, ANALYZE_QUEUE, TRANSCODE_DLQ
from src.logging_setup import init_logging, discord_notify
from src.config import load_config

config = {}
MAX_ATTEMPTS = 3
_files_since_summary = 0


def handle(ch, method, properties, body):
    global _files_since_summary
    msg = json.loads(body)
    ts_key = msg['ts_key']
    camera_id = msg['camera_id']
    mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'

    attempt = (properties.headers or {}).get('x-retry-count', 0) + 1
    logging.info(f"Transcoding: {ts_key} (attempt {attempt}/{MAX_ATTEMPTS})")

    try:
        local_mp4 = transcode(ts_key, config)
        if local_mp4 and os.path.exists(local_mp4):
            os.remove(local_mp4)

        publish(ch, ANALYZE_QUEUE, {'mp4_key': mp4_key, 'camera_id': camera_id})

        _files_since_summary += 1
        interval = config.get('discord', {}).get('summary_interval', 25)
        if _files_since_summary >= interval:
            discord_notify(f"▸ **[transcode]** transcoded {_files_since_summary} files")
            _files_since_summary = 0

        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        logging.error(f"Transcode failed for {ts_key} (attempt {attempt}/{MAX_ATTEMPTS}): {e}")
        ch.basic_ack(delivery_tag=method.delivery_tag)
        if attempt < MAX_ATTEMPTS:
            publish(ch, TRANSCODE_QUEUE, msg, headers={'x-retry-count': attempt})
        else:
            record_dlq(ts_key, TRANSCODE_DLQ, str(e), config)
            publish(ch, TRANSCODE_DLQ, {**msg, 'error': str(e)})


if __name__ == '__main__':
    config = load_config()
    init_logging('transcode')
    ensure_schema(config)
    ensure_buckets(config)
    conn, ch = get_channel(config)
    ch.basic_qos(prefetch_count=1)
    ch.basic_consume(queue=TRANSCODE_QUEUE, on_message_callback=handle)
    logging.info("Transcoder worker ready. Waiting for jobs...")
    try:
        ch.start_consuming()
    except KeyboardInterrupt:
        conn.close()
