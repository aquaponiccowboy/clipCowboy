#!/usr/bin/env python3
"""
classify_scenes.py — zero-shot scene labelling for archived clips.

Downloads each source clip, samples N frames, and runs OpenCLIP against
configurable text prompts to label the dominant scene type (construction_work,
arrival, idle, etc.). No training data required.

Prompts are defined in config.yml under scenes.prompts. Add or rename scenes
freely — the model never needs to be retrained.

Results stored in scene_labels table. Labels feed into filtering and search:
    python3 query_detections.py --scene construction_work

Usage:
    python3 classify_scenes.py                   # all unclassified archived clips
    python3 classify_scenes.py --min-score 0.3   # only clips above heuristic score
    python3 classify_scenes.py --top 50          # top 50 by heuristic score
    python3 classify_scenes.py --camera F
    python3 classify_scenes.py --reclassify      # redo already-classified clips
    python3 classify_scenes.py --dry-run         # list clips without classifying
    python3 classify_scenes.py --list            # show stored scene labels
    python3 classify_scenes.py --list --scene construction_work
"""
import argparse
import json
import logging
import os
import sys
import tempfile
import yaml
import boto3
from botocore.exceptions import ClientError

from src.database import get_connection, ensure_schema
from src.scene_classifier import classify, EMBED_MODEL

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')

_DEFAULT_PROMPTS = {
    'construction_work': [
        'person doing construction work',
        'person using power tools or hand tools',
        'building or framing construction activity',
    ],
    'material_handling': [
        'person carrying lumber or building materials',
        'loading or unloading materials from a vehicle',
        'stacking or moving construction supplies',
    ],
    'arrival': [
        'person walking toward the camera arriving',
        'vehicle arriving and parking',
        'person getting out of a truck or car',
    ],
    'idle': [
        'empty outdoor scene with no people',
        'person standing still not working',
        'quiet scene minimal activity',
    ],
}


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


def _fetch_clips(config, min_score, top_n, camera, reclassify):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = ['p.has_objects = 1'], []
            if not reclassify:
                clauses.append(
                    "NOT EXISTS ("
                    "  SELECT 1 FROM scene_labels sl"
                    "  WHERE sl.file_key = p.file_key AND sl.model_version = p.model_version"
                    ")"
                )
            if camera:
                clauses.append("p.camera_id = %s")
                params.append(camera.upper())

            # join clip_scores only when a score filter is requested
            if min_score is not None:
                join  = "JOIN clip_scores s USING (file_key, model_version)"
                clauses.append("s.score >= %s")
                params.append(min_score)
                order = "ORDER BY s.score DESC"
            else:
                join  = "LEFT JOIN clip_scores s USING (file_key, model_version)"
                order = "ORDER BY p.processed_at DESC"

            where = "WHERE " + " AND ".join(clauses)
            limit = f"LIMIT {top_n}" if top_n else ""
            cursor.execute(
                f"SELECT p.file_key, p.model_version, p.sort_prefix, p.camera_id "
                f"FROM processed_files p {join} {where} {order} {limit}",
                params
            )
            return cursor.fetchall()
    finally:
        conn.close()


def _write_label(file_key, model_version, top_scene, confidence, scores, config):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO scene_labels
                    (file_key, model_version, scene, confidence, scores, clip_model)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    scene         = VALUES(scene),
                    confidence    = VALUES(confidence),
                    scores        = VALUES(scores),
                    clip_model    = VALUES(clip_model),
                    classified_at = CURRENT_TIMESTAMP
            """, (file_key, model_version, top_scene, confidence,
                  json.dumps(scores), EMBED_MODEL))
    finally:
        conn.close()


def _show_list(config, top_n, camera, scene_filter):
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            clauses, params = [], []
            if camera:
                clauses.append("p.camera_id = %s")
                params.append(camera.upper())
            if scene_filter:
                clauses.append("sl.scene = %s")
                params.append(scene_filter)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            limit = f"LIMIT {top_n}" if top_n else ""
            cursor.execute(f"""
                SELECT sl.scene, sl.confidence, p.camera_id,
                       p.sort_prefix, p.file_key, sl.scores
                FROM scene_labels sl
                JOIN processed_files p USING (file_key, model_version)
                {where}
                ORDER BY sl.confidence DESC
                {limit}
            """, params)
            rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No scene labels found.")
        return

    print(f"\n{'SCENE':<22} {'CONF':>4}  {'CAM':>3}  LOCATION")
    print('-' * 88)
    for scene, conf, cam, sort_prefix, file_key, scores_json in rows:
        location = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
        try:
            scores = json.loads(scores_json) if isinstance(scores_json, str) else scores_json
            runner_up = [f"{k}:{v:.2f}" for k, v in list(scores.items())[:3]
                         if k != '__top__' and k != scene]
            detail = '  ' + '  '.join(runner_up) if runner_up else ''
        except Exception:
            detail = ''
        print(f"{scene:<22} {conf:>4.2f}  {(cam or '?'):>3}  {location}{detail}")


def main():
    parser = argparse.ArgumentParser(
        description='Zero-shot scene classification for archived clips using OpenCLIP.'
    )
    parser.add_argument('--min-score', type=float, metavar='N',
                        help='Only classify clips with heuristic score >= N')
    parser.add_argument('--top', type=int, metavar='N',
                        help='Process only the top N clips by heuristic score')
    parser.add_argument('--camera', choices=['F', 'B', 'L', 'R'])
    parser.add_argument('--reclassify', action='store_true',
                        help='Redo clips that are already classified')
    parser.add_argument('--dry-run', action='store_true',
                        help='List clips that would be classified without running CLIP')
    parser.add_argument('--list', action='store_true',
                        help='Show stored scene labels without classifying')
    parser.add_argument('--scene', metavar='NAME',
                        help='Filter --list to a specific scene name')
    args = parser.parse_args()

    config = load_config()
    ensure_schema(config)

    if args.list:
        _show_list(config, args.top, args.camera, args.scene)
        return

    scenes_cfg = config.get('scenes', {})
    prompts    = scenes_cfg.get('prompts') or _DEFAULT_PROMPTS
    n_frames   = int(scenes_cfg.get('n_frames', 8))

    rows = _fetch_clips(config, args.min_score, args.top, args.camera, args.reclassify)
    if not rows:
        print("No clips to classify.")
        return

    label = '[DRY RUN] ' if args.dry_run else ''
    print(f"{label}Classifying {len(rows)} clip(s) against {len(prompts)} scene(s)...\n")

    if args.dry_run:
        for file_key, _, sort_prefix, cam in rows:
            location = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
            print(f"  [{cam or '?'}] {location}")
        print(f"\n{len(rows)} clip(s) would be classified.")
        return

    archive_bucket = config['storage']['buckets']['archive']
    s3 = _get_s3(config)
    done, failed = 0, 0

    for file_key, model_version, sort_prefix, cam in rows:
        archive_key = f"{sort_prefix}/{file_key}" if sort_prefix else file_key
        try:
            with tempfile.TemporaryDirectory() as tmp:
                local = os.path.join(tmp, 'clip.mp4')
                s3.download_file(archive_bucket, archive_key, local)
                scores = classify(local, prompts, n_frames)

            top_scene  = scores.pop('__top__', 'unknown')
            confidence = scores.get(top_scene, 0.0)
            _write_label(file_key, model_version, top_scene, confidence, scores, config)
            runner_up = [(k, v) for k, v in scores.items() if k != top_scene][:2]
            detail = '  ' + '  '.join(f"{k}:{v:.2f}" for k, v in runner_up)
            print(f"  [{cam or '?'}] {archive_key}  → {top_scene} ({confidence:.2f}){detail}")
            done += 1
        except (ClientError, Exception) as e:
            print(f"  FAILED: {archive_key} — {e}")
            failed += 1

    print(f"\n{done} classified, {failed} failed.")


if __name__ == '__main__':
    main()
