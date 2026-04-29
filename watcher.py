#!/usr/bin/env python3
"""
Watcher: scans MinIO for new footage and publishes jobs to RabbitMQ.

Publishes .TS files to footage.transcode queue.
Publishes already-converted but unanalyzed .MP4s to footage.analyze queue
(crash recovery path).

Run with: python3 watcher.py
"""
import yaml
import logging
import time
import boto3
from botocore.exceptions import ClientError
from src.ingestion import get_raw_keys, get_converted_keys, get_input_mp4_keys
from src.persistence import is_processed, is_in_dlq
from src.router import get_camera_context
from src.queue_client import get_channel, publish, TRANSCODE_QUEUE, ANALYZE_QUEUE
from src.logging_setup import init_logging, discord_notify
from src.config import load_config

# Keys published this watcher process session, mapped to the monotonic time of
# publication. Prevents duplicate queue messages when a scan overlaps with slow
# processing (file still in input/converted but not yet in processed_files).
_in_flight: dict[str, float] = {}


def _prune_in_flight(ttl: float):
    now = time.monotonic()
    expired = [k for k, t in _in_flight.items() if now - t > ttl]
    for k in expired:
        del _in_flight[k]


def _is_in_flight(key: str, ttl: float) -> bool:
    t = _in_flight.get(key)
    return t is not None and time.monotonic() - t < ttl


def _mark_in_flight(key: str):
    _in_flight[key] = time.monotonic()




def _move_to_converted(mp4_key: str, config: dict) -> bool:
    """Copy an MP4 from input to converted bucket then delete from input."""
    storage = config.get('storage', {})
    s3 = boto3.client('s3',
        endpoint_url=storage.get('endpoint_url'),
        aws_access_key_id=storage.get('access_key'),
        aws_secret_access_key=storage.get('secret_key'),
        region_name=storage.get('region_name', 'us-east-1')
    )
    try:
        s3.copy_object(
            CopySource={'Bucket': storage['buckets']['input'], 'Key': mp4_key},
            Bucket=storage['buckets']['converted'],
            Key=mp4_key,
        )
        s3.delete_object(Bucket=storage['buckets']['input'], Key=mp4_key)
        logging.info(f"Moved {mp4_key} from input to converted (no transcode needed)")
        return True
    except Exception as e:
        logging.error(f"Failed to move {mp4_key} to converted: {e}")
        return False


def _already_converted(ts_key: str, config: dict) -> bool:
    """Check if the MP4 already exists in the converted bucket."""
    storage = config.get('storage', {})
    s3 = boto3.client('s3',
        endpoint_url=storage.get('endpoint_url'),
        aws_access_key_id=storage.get('access_key'),
        aws_secret_access_key=storage.get('secret_key'),
        region_name=storage.get('region_name', 'us-east-1')
    )
    mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'
    try:
        s3.head_object(Bucket=storage['buckets']['converted'], Key=mp4_key)
        return True
    except ClientError:
        return False


def scan_and_publish(config: dict):
    conn, ch = get_channel(config)
    queued = 0
    ttl = config.get('watcher', {}).get('inflight_ttl', 600)
    _prune_in_flight(ttl)

    try:
        # Phase 1: raw .TS files → transcode queue
        for ts_key in get_raw_keys(config):
            mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'

            if is_processed(mp4_key, config):
                continue
            if is_in_dlq(ts_key, config):
                logging.debug(f"Skipping DLQ'd file: {ts_key}")
                continue
            if _already_converted(ts_key, config):
                # Converted but not analyzed — handled by Phase 2
                continue
            if _is_in_flight(ts_key, ttl):
                logging.debug(f"Skipping in-flight file: {ts_key}")
                continue

            context = get_camera_context(ts_key, config)
            if not context:
                continue

            publish(ch, TRANSCODE_QUEUE, {'ts_key': ts_key, 'camera_id': context['id']})
            _mark_in_flight(ts_key)
            logging.info(f"Queued for transcode: {ts_key}")
            queued += 1

        # Phase 1b: MP4s in input bucket → move to converted, Phase 2 will queue for analysis
        for mp4_key in get_input_mp4_keys(config):
            if is_processed(mp4_key, config):
                continue
            if is_in_dlq(mp4_key, config):
                continue
            if _is_in_flight(mp4_key, ttl):
                continue
            if _move_to_converted(mp4_key, config):
                _mark_in_flight(mp4_key)
                queued += 1

        # Phase 2: converted but unanalyzed .MP4s → analyze queue (recovery)
        for mp4_key in get_converted_keys(config):
            if is_processed(mp4_key, config):
                continue
            if is_in_dlq(mp4_key, config):
                logging.debug(f"Skipping DLQ'd file: {mp4_key}")
                continue
            if _is_in_flight(mp4_key, ttl):
                logging.debug(f"Skipping in-flight file: {mp4_key}")
                continue

            context = get_camera_context(mp4_key, config)
            if not context:
                continue

            publish(ch, ANALYZE_QUEUE, {'mp4_key': mp4_key, 'camera_id': context['id']})
            _mark_in_flight(mp4_key)
            logging.info(f"Queued for analysis (recovery): {mp4_key}")
            queued += 1

    finally:
        conn.close()

    return queued


if __name__ == '__main__':
    config = load_config()
    init_logging('watcher')
    watcher_cfg = config.get('watcher', {})
    interval        = watcher_cfg.get('scan_interval', 60)
    status_interval = watcher_cfg.get('status_interval', 0)  # 0 = disabled
    last_status     = 0.0
    logging.info(f"Watcher started. Scanning every {interval}s.")

    while True:
        try:
            queued = scan_and_publish(config)
            if queued:
                logging.info(f"Published {queued} job(s) to queues.")
                discord_notify(f"▸ **[watcher]** queued {queued} new job{'s' if queued != 1 else ''}")
            else:
                logging.info("Nothing new to queue.")
        except Exception as e:
            logging.error(f"Watcher scan failed: {e}")

        if status_interval > 0 and (time.time() - last_status) >= status_interval:
            try:
                from reset_pipeline import status_summary, format_status_notify
                summary = status_summary(config)
                discord_notify(format_status_notify(summary))
                last_status = time.time()
            except Exception as e:
                logging.error(f"Periodic status notification failed: {e}")

        time.sleep(interval)
