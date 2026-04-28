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


def _merge_windows(event_times: list[float], pad: float, merge_gap: float) -> list[tuple]:
    """Merge detection timestamps into non-overlapping [start, end] windows."""
    windows = []
    for t in sorted(event_times):
        start = max(0.0, t - pad)
        end = t + pad
        if windows and start <= windows[-1][1] + merge_gap:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def extract_event_clips(local_mp4: str, mp4_key: str, events: list, config: dict) -> int:
    """
    Extract clean (no annotation) clips around each detection event and upload
    to the event_clips MinIO bucket. Returns the number of clips uploaded.
    """
    cfg = config.get('model', {}).get('event_clips', {})
    pad = float(cfg.get('pad', 2.0))
    merge_gap = float(cfg.get('merge_gap', 4.0))

    storage = config.get('storage', {})
    bucket = storage.get('buckets', {}).get('event_clips')
    if not bucket:
        logging.warning("event_clips bucket not configured — skipping clip extraction")
        return 0

    event_times = []
    for ev in events:
        t_str = ev.get('time', '0s').rstrip('s')
        try:
            event_times.append(float(t_str))
        except ValueError:
            pass

    if not event_times:
        return 0

    windows = _merge_windows(event_times, pad, merge_gap)
    s3 = _get_s3_client(config)
    base_key = mp4_key.rsplit('.', 1)[0]  # strip .mp4
    uploaded = 0

    for start, end in windows:
        duration = end - start
        clip_filename = f"{os.path.basename(base_key)}_clip_{start:.1f}s-{end:.1f}s.mp4"
        clip_path = os.path.join(os.path.dirname(local_mp4), clip_filename)
        clip_key = f"{os.path.dirname(mp4_key)}/{clip_filename}".lstrip('/')

        try:
            subprocess.run(
                [
                    'ffmpeg', '-y',
                    '-ss', str(start),
                    '-i', local_mp4,
                    '-t', str(duration),
                    '-c', 'copy',          # stream copy — fast, no re-encode
                    clip_path,
                ],
                check=True,
                capture_output=True,
            )
            s3.upload_file(clip_path, bucket, clip_key)
            logging.info(f"Event clip uploaded: {clip_key} ({start:.1f}s–{end:.1f}s)")
            uploaded += 1
        except subprocess.CalledProcessError as e:
            logging.error(f"ffmpeg failed for clip {clip_filename}: {e.stderr.decode()[-200:]}")
        except ClientError as e:
            logging.error(f"Upload failed for {clip_key}: {e}")
        finally:
            if os.path.exists(clip_path):
                os.remove(clip_path)

    return uploaded
