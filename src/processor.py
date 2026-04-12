import os
import logging
from src.transcoder import transcode, download_for_analysis
from src.analyzer import detect_objects


def process_ts(ts_key: str, context: dict, config: dict) -> dict | None:
    """
    Full pipeline for a raw .TS file:
      1. Transcode TS → MP4 (uploads to converted bucket, deletes .TS)
      2. Analyze local MP4 with YOLO
    Returns result dict, or None if transcoding failed.
    The mp4_key (not ts_key) is what gets committed to the DB.
    """
    local_mp4 = transcode(ts_key, config)
    if local_mp4 is None:
        return None  # already converted or failed — Phase 2 will handle it
    try:
        return detect_objects(local_mp4, context['mask_config'], config)
    finally:
        if os.path.exists(local_mp4):
            os.remove(local_mp4)


def process_mp4(mp4_key: str, context: dict, config: dict) -> dict | None:
    """
    Analysis-only pipeline for an already-converted .MP4 in the converted bucket.
    Used for crash recovery or re-analysis.
    """
    local_mp4 = download_for_analysis(mp4_key, config)
    if local_mp4 is None:
        return None
    try:
        return detect_objects(local_mp4, context['mask_config'], config)
    finally:
        if os.path.exists(local_mp4):
            os.remove(local_mp4)
