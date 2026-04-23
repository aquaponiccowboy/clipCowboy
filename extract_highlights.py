#!/usr/bin/env python3
"""
extract_highlights.py — cut high-activity sub-clips from archived footage.

Reads heuristic scores and YOLO events from the DB, finds the busiest windows
within each clip, and trims them with ffmpeg. One download per source clip;
all windows cut from the local copy before upload.

Results land in the highlights MinIO bucket and are tracked in the highlights
DB table. Source clips in archive are never modified.

Usage:
    python3 extract_highlights.py                   # all scored clips
    python3 extract_highlights.py --min-score 0.5   # skip low-activity clips
    python3 extract_highlights.py --top 50           # top 50 by score only
    python3 extract_highlights.py --camera F
    python3 extract_highlights.py --dry-run          # show windows, no ffmpeg
    python3 extract_highlights.py --re-extract       # redo already-extracted
    python3 extract_highlights.py --list             # show extracted highlights
    python3 extract_highlights.py --list --top 20 --min-score 0.4
"""
import argparse
import logging
import os
import subprocess
import sys
import tempfile
import yaml
import boto3
from botocore.exceptions import ClientError

from src.database import get_connection, ensure_schema
from src.highlighter import find_windows

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


def _get_s3(config):
    s = config['storage']
    return boto3.client('s3',
        endpoint_url=s.get('endpoint_url'),
        aws_access_key_id=s.get('access_key'),
        aws_secret_access_key=s.get('secret_key'),
        region_name=s.get('region_name', 'us-east-1'),
    )


def _ensure_bucket(s3, bucket):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response['Error']['Code'] in ('404', 'NoSuchBucket'):
            s3.create_bucket(Bucket=bucket)
            logging.info(f"Created bucket: {bucket}")
        else:
            raise


def _fetch_clips(config, min_score, top_n, camera, re_extract):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = ['p.has_objects = 1'], []
            if not re_extract:
                clauses.append(
                    "NOT EXISTS ("
                    "  SELECT 1 FROM highlights h"
                    "  WHERE h.file_key = p.file_key AND h.model_version = p.model_version"
                    ")"
                )
            if camera:
                clauses.append("p.camera_id = %s")
                params.append(camera.upper())
            if min_score is not None:
                clauses.append("s.score >= %s")
                params.append(min_score)
            where = "WHERE " + " AND ".join(clauses)
            limit = f"LIMIT {top_n}" if top_n else ""
            cursor.execute(f"""
                SELECT p.file_key, p.model_version, p.sort_prefix,
                       p.camera_id, p.events, s.score
                FROM processed_files p
                JOIN clip_scores s USING (file_key, model_version)
                {where}
                ORDER BY s.score DESC
                {limit}
            """, params)
            return cursor.fetchall()
    finally:
        conn.close()


def _hl_key(file_key: str, start: float, end: float) -> str:
    stem = os.path.splitext(os.path.basename(file_key))[0]
    return f"{stem}_hl_{int(start)}_{int(end)}.mp4"


def _write_highlight(file_key, model_version, hl_key, w, config):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO highlights
                    (file_key, model_version, highlight_key,
                     start_sec, end_sec, duration_sec, window_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    start_sec    = VALUES(start_sec),
                    end_sec      = VALUES(end_sec),
                    duration_sec = VALUES(duration_sec),
                    window_score = VALUES(window_score),
                    extracted_at = CURRENT_TIMESTAMP
            """, (
                file_key, model_version, hl_key,
                w['start'], w['end'], w['duration'], w['score'],
            ))
    finally:
        conn.close()


def _clear_highlights(file_key, model_version, config):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM highlights WHERE file_key = %s AND model_version = %s",
                (file_key, model_version)
            )
    finally:
        conn.close()


def _ffmpeg_cut(src, dst, start, duration):
    subprocess.run([
        'ffmpeg', '-y',
        '-ss', str(start),
        '-i', src,
        '-t', str(duration),
        '-c', 'copy',
        dst,
    ], check=True, capture_output=True)


def _show_list(config, top_n, camera, min_score):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = [], []
            if camera:
                clauses.append("p.camera_id = %s")
                params.append(camera.upper())
            if min_score is not None:
                clauses.append("h.window_score >= %s")
                params.append(min_score)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            limit = f"LIMIT {top_n}" if top_n else ""
            cursor.execute(f"""
                SELECT h.highlight_key, p.camera_id,
                       h.start_sec, h.end_sec, h.duration_sec, h.window_score
                FROM highlights h
                JOIN processed_files p USING (file_key, model_version)
                {where}
                ORDER BY h.window_score DESC
                {limit}
            """, params)
            rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No highlights found.")
        return
    print(f"\n{'ACT':>5}  {'CAM':>3}  {'START':>6}  {'END':>6}  {'DUR':>5}  KEY")
    print('-' * 82)
    for hl_key, cam, start, end, dur, score in rows:
        print(f"{score:5.2f}  {(cam or '?'):>3}  {start:>5.0f}s  "
              f"{end:>5.0f}s  {dur:>4.0f}s  {hl_key}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract high-activity sub-clips from archived footage using ffmpeg.'
    )
    parser.add_argument('--min-score', type=float, metavar='N',
                        help='Skip clips with overall activity score below N')
    parser.add_argument('--top', type=int, metavar='N',
                        help='Process only the top N clips by score')
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'])
    parser.add_argument('--dry-run', action='store_true',
                        help='Show detected windows without downloading or cutting')
    parser.add_argument('--re-extract', action='store_true',
                        help='Re-extract clips that already have highlights')
    parser.add_argument('--list', action='store_true',
                        help='Show already-extracted highlights without processing')
    args = parser.parse_args()

    config = load_config()
    ensure_schema(config)

    if args.list:
        _show_list(config, args.top, args.camera, args.min_score)
        return

    hl_config      = config.get('highlights', {})
    min_clip_score = args.min_score if args.min_score is not None \
                     else hl_config.get('min_clip_score', 0.0)
    archive_bucket = config['storage']['buckets']['archive']
    hl_bucket      = config['storage']['buckets'].get(
                         'highlights', 'security-camera-highlights')

    rows = _fetch_clips(config, min_clip_score, args.top, args.camera, args.re_extract)
    if not rows:
        print("No clips to process. Run score_clips.py first, or lower --min-score.")
        return

    label = '[DRY RUN] ' if args.dry_run else ''
    print(f"{label}Processing {len(rows)} clip(s)...\n")

    s3 = None
    if not args.dry_run:
        s3 = _get_s3(config)
        _ensure_bucket(s3, hl_bucket)

    extracted, skipped = 0, 0

    for file_key, model_version, sort_prefix, cam, events_json, clip_score in rows:
        windows = find_windows(events_json, hl_config)
        archive_key = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
        cam_label = cam or '?'

        if not windows:
            print(f"  [{cam_label}] {archive_key}  score={clip_score:.3f} — no active windows")
            skipped += 1
            continue

        print(f"  [{cam_label}] {archive_key}  score={clip_score:.3f}  "
              f"{len(windows)} window(s):")
        for w in windows:
            hl_key = _hl_key(file_key, w['start'], w['end'])
            print(f"    {w['start']:6.1f}s – {w['end']:6.1f}s  "
                  f"({w['duration']:.0f}s)  activity={w['score']:.2f}  → {hl_key}")

        if args.dry_run:
            continue

        if args.re_extract:
            _clear_highlights(file_key, model_version, config)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                src_path = os.path.join(tmp, 'source.mp4')
                s3.download_file(archive_bucket, archive_key, src_path)

                for w in windows:
                    hl_key = _hl_key(file_key, w['start'], w['end'])
                    dst_path = os.path.join(tmp, hl_key)
                    try:
                        _ffmpeg_cut(src_path, dst_path, w['start'], w['duration'])
                        s3.upload_file(dst_path, hl_bucket, hl_key)
                        _write_highlight(file_key, model_version, hl_key, w, config)
                        extracted += 1
                    except (subprocess.CalledProcessError, ClientError) as e:
                        print(f"    FAILED: {hl_key} — {e}")
        except ClientError as e:
            print(f"  FAILED to download {archive_key}: {e}")

    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to extract.")
    else:
        print(f"\n{extracted} highlight(s) extracted → bucket '{hl_bucket}'.")
        if skipped:
            print(f"{skipped} clip(s) had no active windows.")


if __name__ == '__main__':
    main()
