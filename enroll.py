#!/usr/bin/env python3
"""
enroll.py — register known individuals in the gallery for named recognition.

Usage:
    # Enroll from a reference video or image
    python3 enroll.py --name Zeke --category people --from clip.mp4
    python3 enroll.py --name BlueTruck --category vehicles --from photo.jpg

    # List enrolled subjects
    python3 enroll.py --list

    # Remove a subject from the gallery
    python3 enroll.py --remove --name Zeke --category people
"""
import argparse
import logging
import os
import sys
import yaml
import cv2

from src.database import ensure_schema
from src.gallery import store_embedding, list_enrolled, remove_enrolled
from src.recognizer import get_embedding

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')

# YOLO labels that map to each enrollable category
_CATEGORY_LABELS = {
    'people':   {'person'},
    'vehicles': {'car', 'truck', 'bus', 'motorcycle', 'bicycle'},
    'animals':  {'dog', 'cat', 'bird', 'horse', 'cow', 'sheep',
                 'bear', 'elephant', 'zebra', 'giraffe'},
}

_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def load_config(path='config.yml'):
    with open(path) as f:
        return yaml.safe_load(f)


def _sample_frames(path: str, max_frames: int) -> list:
    """Return up to max_frames evenly-spaced BGR frames from a video or image."""
    if os.path.splitext(path)[1].lower() in _IMAGE_EXTS:
        frame = cv2.imread(path)
        return [frame] if frame is not None else []

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open {path}")

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or max_frames
    step = max(1, total // max_frames)
    frames, idx = [], 0
    while len(frames) < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
        idx += step
    cap.release()
    return frames


def _do_enroll(args, config):
    from ultralytics import YOLO

    model_path = config.get('model', {}).get('path', 'models/yolov8n.pt')
    conf_threshold = (
        config.get('global_parameters', {}).get('confidence_threshold')
        or config.get('model', {}).get('confidence', 0.25)
    )
    yolo = YOLO(model_path)

    target_labels = _CATEGORY_LABELS.get(args.category, set())
    rec_cfg = config.get('recognition', {}).get('categories', {}).get(args.category, {})
    if not rec_cfg:
        print(f"Note: no recognition.categories.{args.category} in config.yml — defaulting to CLIP.")
        rec_cfg = {'model': 'clip'}

    print(f"Sampling up to {args.max_frames} frames from {args.source} ...")
    frames = _sample_frames(args.source, args.max_frames)
    if not frames:
        print(f"Could not read any frames from {args.source}")
        sys.exit(1)

    stored, skipped = 0, 0
    for i, frame in enumerate(frames):
        results = yolo(frame, conf=conf_threshold, verbose=False)
        for r in results:
            for box in r.boxes:
                label = yolo.names[int(box.cls[0])]
                if label.lower() not in target_labels:
                    continue
                xyxy = box.xyxy[0].tolist()
                emb, model_name = get_embedding(frame, xyxy, args.category, rec_cfg)
                if emb is None:
                    skipped += 1
                    continue
                store_embedding(
                    name=args.name,
                    category=args.category,
                    embedding=emb,
                    embed_model=model_name,
                    source_file=os.path.basename(args.source),
                    config=config,
                )
                stored += 1

        if (i + 1) % 5 == 0 or i == len(frames) - 1:
            print(f"  frame {i + 1}/{len(frames)} — {stored} embeddings stored")

    print(f"\nEnrolled '{args.name}' ({args.category}): {stored} stored, {skipped} skipped.")
    if stored == 0:
        print("No embeddings stored. Verify the reference file contains visible detections.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Enroll or manage named subjects for recognition.'
    )
    parser.add_argument('--name', metavar='NAME', help='Subject name')
    parser.add_argument('--category', metavar='CAT',
                        choices=list(_CATEGORY_LABELS),
                        help='Category: people, vehicles, or animals')
    parser.add_argument('--from', dest='source', metavar='FILE',
                        help='Reference video (.mp4, .ts) or image file')
    parser.add_argument('--max-frames', type=int, default=30,
                        help='Max frames to sample from video (default: 30)')
    parser.add_argument('--list', action='store_true',
                        help='List all enrolled subjects')
    parser.add_argument('--remove', action='store_true',
                        help='Remove a subject from the gallery (requires --name --category)')

    args = parser.parse_args()
    config = load_config()
    ensure_schema(config)

    if args.list:
        rows = list_enrolled(config)
        if not rows:
            print("Gallery is empty.")
            return
        print(f"\n{'NAME':<20} {'CATEGORY':<12} {'MODEL':<30} {'SAMPLES':>7}  LAST ENROLLED")
        print('-' * 86)
        for r in rows:
            print(f"{r['name']:<20} {r['category']:<12} {r['embed_model']:<30} "
                  f"{r['count']:>7}  {r['last_enrolled']}")
        return

    if args.remove:
        if not args.name or not args.category:
            print("--remove requires --name and --category")
            sys.exit(1)
        n = remove_enrolled(args.name, args.category, config)
        print(f"Removed {n} embedding(s) for '{args.name}' ({args.category}).")
        return

    # Default action: enroll
    if not args.name or not args.category or not args.source:
        parser.print_help()
        sys.exit(1)

    _do_enroll(args, config)


if __name__ == '__main__':
    main()
