import os
import logging
from src.transcoder import download_and_convert
from src.analyzer import detect_objects


def analyze_video(context: dict, config: dict) -> dict:
    """
    Orchestrates the processing pipeline for a single file:
      1. Transcoder  — download + remux TS → MP4
      2. Analyzer    — YOLO object detection + annotated video
    """
    result = {"success": False, "has_objects": False, "events": [], "local_video_path": None}
    mp4_path = None

    try:
        mp4_path = download_and_convert(context['file_key'], config)
        detection = detect_objects(mp4_path, context['mask_config'], config)
        result.update(detection)
        result["success"] = True

    except Exception as e:
        logging.error(f"Pipeline error for {context['file_key']}: {e}")

    finally:
        if mp4_path and os.path.exists(mp4_path):
            os.remove(mp4_path)

    return result
