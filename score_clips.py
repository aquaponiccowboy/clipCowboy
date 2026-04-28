#!/usr/bin/env python3
"""
score_clips.py — heuristic activity scoring for archived clips.

Reads YOLO events already in the database and scores each clip 0–1.
Higher score = more activity: denser detections, more simultaneous objects,
greater label variety, higher confidence, spread throughout the clip.

Scores are written to the clip_scores table and can be queried at any time.

Usage:
    python3 score_clips.py                   # score all unscored archived clips
    python3 score_clips.py --rescore         # rescore after tuning weights in config.yml
    python3 score_clips.py --top 20          # score then show top 20
    python3 score_clips.py --list --top 20   # show top 20 without scoring
    python3 score_clips.py --list --min-score 0.7
    python3 score_clips.py --camera F --top 10
"""
import argparse
import logging
import sys
import yaml

from src.database import get_connection, ensure_schema
from src.scorer import compute_score
from src.config import load_config

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')




def _fetch_unscored(config, rescore, camera):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = ['p.has_objects = 1'], []
            if not rescore:
                clauses.append(
                    "NOT EXISTS ("
                    "  SELECT 1 FROM clip_scores s"
                    "  WHERE s.file_key = p.file_key AND s.model_version = p.model_version"
                    ")"
                )
            if camera:
                clauses.append("p.camera_id = %s")
                params.append(camera.upper())
            where = "WHERE " + " AND ".join(clauses)
            cursor.execute(
                f"SELECT p.file_key, p.model_version, p.events "
                f"FROM processed_files p {where} "
                f"ORDER BY p.processed_at DESC",
                params
            )
            return cursor.fetchall()
    finally:
        conn.close()


def _write_score(file_key, model_version, m, config):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO clip_scores
                    (file_key, model_version, score, n_events, duration_sec,
                     detection_rate, avg_objects, label_diversity,
                     mean_confidence, temporal_coverage)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    score             = VALUES(score),
                    n_events          = VALUES(n_events),
                    duration_sec      = VALUES(duration_sec),
                    detection_rate    = VALUES(detection_rate),
                    avg_objects       = VALUES(avg_objects),
                    label_diversity   = VALUES(label_diversity),
                    mean_confidence   = VALUES(mean_confidence),
                    temporal_coverage = VALUES(temporal_coverage),
                    scored_at         = CURRENT_TIMESTAMP
            """, (
                file_key, model_version,
                m['score'], m['n_events'], m['duration_sec'],
                m['detection_rate'], m['avg_objects'], m['label_diversity'],
                m['mean_confidence'], m['temporal_coverage'],
            ))
    finally:
        conn.close()


def _show_ranked(config, top_n, camera, min_score):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = [], []
            if camera:
                clauses.append("p.camera_id = %s")
                params.append(camera.upper())
            if min_score is not None:
                clauses.append("s.score >= %s")
                params.append(min_score)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            limit = f"LIMIT {top_n}" if top_n else ""
            cursor.execute(f"""
                SELECT p.file_key, p.camera_id, p.sort_prefix,
                       s.score, s.n_events, s.duration_sec,
                       s.detection_rate, s.avg_objects, s.label_diversity
                FROM clip_scores s
                JOIN processed_files p USING (file_key, model_version)
                {where}
                ORDER BY s.score DESC
                {limit}
            """, params)
            rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No scored clips found.")
        return

    print(f"\n{'SCORE':>5}  {'CAM':>3}  {'EVTS':>4}  {'DUR':>6}  "
          f"{'RATE':>4}  {'OBJ':>4}  {'LBL':>3}  LOCATION")
    print('-' * 92)
    for file_key, cam, sort_prefix, score, n_events, dur, rate, avg_obj, lbl_div in rows:
        location = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
        print(f"{score:5.3f}  {(cam or '?'):>3}  {n_events:>4}  {dur:>5.0f}s  "
              f"{rate:>4.2f}  {avg_obj:>4.1f}  {lbl_div:>3}  {location}")


def main():
    parser = argparse.ArgumentParser(
        description='Score archived clips by heuristic activity level (0–1).'
    )
    parser.add_argument('--rescore', action='store_true',
                        help='Rescore already-scored clips (use after tuning weights)')
    parser.add_argument('--list', action='store_true',
                        help='Show ranked scores without re-scoring anything')
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'],
                        help='Filter by camera ID')
    parser.add_argument('--top', type=int, metavar='N',
                        help='Show top N clips after scoring (or with --list)')
    parser.add_argument('--min-score', type=float, metavar='N',
                        help='Only show clips with score >= N (use with --list or --top)')
    args = parser.parse_args()

    config = load_config()
    ensure_schema(config)

    if args.list:
        _show_ranked(config, args.top, args.camera, args.min_score)
        return

    rows = _fetch_unscored(config, args.rescore, args.camera)
    if not rows:
        print("No clips to score.")
        if args.top or args.min_score is not None:
            _show_ranked(config, args.top, args.camera, args.min_score)
        return

    weights = config.get('scoring', {}).get('weights', {})
    print(f"Scoring {len(rows)} clip(s)...")

    for i, (file_key, model_version, events_json) in enumerate(rows):
        metrics = compute_score(events_json, weights)
        _write_score(file_key, model_version, metrics, config)
        if (i + 1) % 50 == 0 or i == len(rows) - 1:
            print(f"  {i + 1}/{len(rows)}")

    print(f"\nDone. {len(rows)} clip(s) scored.")

    if args.top or args.min_score is not None:
        _show_ranked(config, args.top, args.camera, args.min_score)


if __name__ == '__main__':
    main()
