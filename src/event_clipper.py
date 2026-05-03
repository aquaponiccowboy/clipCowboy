import logging
import os
import subprocess
import boto3
from botocore.exceptions import ClientError


def _get_s3_client(config: dict):
    s = config.get('storage', {})
    return boto3.client(
        's3',
        endpoint_url=s.get('endpoint_url'),
        aws_access_key_id=s.get('access_key'),
        aws_secret_access_key=s.get('secret_key'),
        region_name=s.get('region_name', 'us-east-1'),
    )


def _build_label_category_map(config: dict) -> dict:
    """Map each YOLO label → sort category name, e.g. 'person' → 'people'."""
    result = {}
    for cat, cfg in config.get('sort', {}).get('categories', {}).items():
        for label in cfg.get('labels', []):
            result[label.lower()] = cat
    return result


def _dominant_category(events_in_window: list, label_cat_map: dict) -> str:
    """
    Return the category with the highest total confidence across all detections
    in a window. Falls back to 'other' if no labels match any category.
    """
    scores: dict[str, float] = {}
    for ev in events_in_window:
        for det in ev.get('detections', []):
            cat = label_cat_map.get(det.get('label', '').lower())
            if cat:
                scores[cat] = scores.get(cat, 0.0) + det.get('confidence', 0.0)
    return max(scores, key=scores.get) if scores else 'other'


def _merge_windows(events: list, pad: float, merge_gap: float) -> list[tuple]:
    """
    Merge events into non-overlapping (start, end, [events_in_window]) tuples.
    Events are sorted by time; close windows are merged and their events pooled.
    """
    parsed = []
    for ev in events:
        t_str = ev.get('time', '0s').rstrip('s')
        try:
            parsed.append((float(t_str), ev))
        except ValueError:
            pass
    parsed.sort(key=lambda x: x[0])

    windows: list[list] = []  # each entry: [start, end, [events]]
    for t, ev in parsed:
        start = max(0.0, t - pad)
        end = t + pad
        if windows and start <= windows[-1][1] + merge_gap:
            windows[-1][1] = max(windows[-1][1], end)
            windows[-1][2].append(ev)
        else:
            windows.append([start, end, [ev]])

    return [(w[0], w[1], w[2]) for w in windows]


def extract_event_clips(local_mp4: str, mp4_key: str, events: list, config: dict) -> int:
    """
    Extract clean (no annotation) clips around each detection event, sorted into
    category subfolders (people/, vehicles/, animals/, other/) in the event_clips
    bucket. Returns the number of clips uploaded.
    """
    cfg = config.get('model', {}).get('event_clips', {})
    pad = float(cfg.get('pad', 2.0))
    merge_gap = float(cfg.get('merge_gap', 4.0))

    storage = config.get('storage', {})
    bucket = storage.get('buckets', {}).get('event_clips')
    if not bucket:
        logging.warning("event_clips bucket not configured — skipping clip extraction")
        return 0

    if not events:
        return 0

    label_cat_map = _build_label_category_map(config)
    windows = _merge_windows(events, pad, merge_gap)
    s3 = _get_s3_client(config)
    base_name = os.path.splitext(os.path.basename(mp4_key))[0]
    uploaded = 0

    for start, end, window_events in windows:
        duration = end - start
        category = _dominant_category(window_events, label_cat_map)
        clip_filename = f"{base_name}_clip_{start:.1f}s-{end:.1f}s.mp4"
        clip_path = os.path.join(os.path.dirname(local_mp4), clip_filename)
        clip_key = f"{category}/{clip_filename}"

        try:
            subprocess.run(
                [
                    'ffmpeg', '-y',
                    '-ss', str(start),
                    '-i', local_mp4,
                    '-t', str(duration),
                    '-c', 'copy',
                    clip_path,
                ],
                check=True,
                capture_output=True,
            )
            s3.upload_file(clip_path, bucket, clip_key)
            logging.info(f"Event clip uploaded: {clip_key} ({start:.1f}s–{end:.1f}s)")
            uploaded += 1
        except subprocess.CalledProcessError as e:
            logging.error(f"ffmpeg failed for {clip_filename}: {e.stderr.decode()[-200:]}")
        except ClientError as e:
            logging.error(f"Upload failed for {clip_key}: {e}")
        finally:
            if os.path.exists(clip_path):
                os.remove(clip_path)

    return uploaded
