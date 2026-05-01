import cv2
import numpy as np
import logging
import os
from ultralytics import YOLO
from src import gallery as gallery_mod
from src import recognizer

_model = None
_gallery_cache: dict = {}   # (category, embed_model_key) → {name: unit_emb}


def _build_label_category_map(config) -> dict:
    """Reverse-map YOLO label → sort category name, e.g. 'person' → 'people'."""
    result = {}
    for cat, cfg in config.get('sort', {}).get('categories', {}).items():
        for label in cfg.get('labels', []):
            result[label.lower()] = cat
    return result


def _get_gallery(category: str, config: dict) -> dict:
    rec = config.get('recognition', {})
    cat_cfg = rec.get('categories', {}).get(category, {})
    model_key = cat_cfg.get('model', 'clip')
    embed_model = (
        'insightface/buffalo_l' if model_key == 'insightface'
        else 'open_clip/ViT-B-32'
    )
    cache_key = (category, embed_model)
    if cache_key not in _gallery_cache:
        _gallery_cache[cache_key] = gallery_mod.load_gallery(category, embed_model, config)
        logging.info(f"Gallery loaded: {category} — {len(_gallery_cache[cache_key])} subject(s)")
    return _gallery_cache[cache_key]


def _get_model(model_path: str = 'models/yolov8n.pt'):
    global _model
    if _model is None:
        logging.info(f"Loading YOLO model from {model_path}...")
        _model = YOLO(model_path)
    return _model


def _box_in_polygon(box_xyxy, polygon) -> bool:
    """Return True if box center is inside polygon, or always True if polygon is None (full frame)."""
    if polygon is None:
        return True
    x1, y1, x2, y2 = box_xyxy
    cx = int((x1 + x2) / 2)
    cy = int((y1 + y2) / 2)
    return cv2.pointPolygonTest(polygon, (cx, cy), False) >= 0


def detect_objects(mp4_path: str, mask_config: dict, config: dict) -> dict:
    """
    Run YOLO on a local MP4. YOLO sees the full frame; the mask is applied
    as a post-filter on detection centers so hard black borders don't
    confuse the model.
    """
    result = {"has_objects": False, "events": [], "local_video_path": None}

    model_cfg = config.get('model', {})
    model = _get_model(model_cfg.get('path', 'models/yolov8n.pt'))
    # global_parameters.confidence_threshold takes precedence when set;
    # falls back to model.confidence, then the hardcoded default.
    conf = (
        config.get('global_parameters', {}).get('confidence_threshold')
        or model_cfg.get('confidence', 0.25)
    )
    debug = model_cfg.get('debug', False)
    debug_dir = model_cfg.get('debug_dir', 'debug')
    save_annotated = model_cfg.get('save_annotated', False)

    rec = config.get('recognition', {})
    recognition_enabled = rec.get('enabled', False)
    rec_threshold = float(rec.get('threshold', 0.50))
    rec_cat_cfgs = rec.get('categories', {})
    label_category_map = _build_label_category_map(config) if recognition_enabled else {}

    annotated_path = mp4_path.replace('.mp4', '_annotated.mp4')

    cap = cv2.VideoCapture(mp4_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {mp4_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps == 0 or np.isnan(fps):
        fps = 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logging.info(
        f"{os.path.basename(mp4_path)}: {width}x{height} @ {fps:.1f}fps, "
        f"~{total_frames} frames, conf={conf}"
    )

    vertices_raw = mask_config.get('vertices')
    vertices = np.array(vertices_raw, np.int32) if vertices_raw is not None else None
    allowed_classes = {c.lower() for c in model_cfg.get('object_sensitivity', {}).get('classes', [])}

    out = None
    if save_annotated:
        for codec in ('avc1', 'mp4v', 'XVID'):
            fourcc = cv2.VideoWriter_fourcc(*codec)
            out = cv2.VideoWriter(annotated_path, fourcc, fps, (width, height))
            if out.isOpened():
                logging.info(f"Annotated writer opened with codec={codec}")
                break
            out = None
        if out is None:
            logging.warning("Could not open VideoWriter for annotated output — skipping annotation")

    frame_count = 0
    last_logged_frame = -999

    # Diagnostics
    total_raw_detections = 0    # all YOLO boxes before mask filter
    total_kept_detections = 0   # boxes whose centers fall inside mask
    max_conf_seen = 0.0
    all_labels_seen = set()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1

        # Debug: dump a mid-video sample so we can see what YOLO is seeing
        if debug and frame_count == max(1, total_frames // 2):
            os.makedirs(debug_dir, exist_ok=True)
            dbg_name = os.path.splitext(os.path.basename(mp4_path))[0]
            cv2.imwrite(os.path.join(debug_dir, f"{dbg_name}_raw.jpg"), frame)
            overlay = frame.copy()
            if vertices is not None:
                cv2.polylines(overlay, [vertices], True, (0, 255, 0), 2)
            cv2.imwrite(os.path.join(debug_dir, f"{dbg_name}_mask_overlay.jpg"), overlay)
            logging.info(f"Debug frames saved to {debug_dir}/{dbg_name}_*.jpg")

        # Run YOLO on the FULL frame — no pre-masking
        yolo_results = model(frame, conf=conf, verbose=False)

        kept_boxes = []
        for r in yolo_results:
            if len(r.boxes) == 0:
                continue
            total_raw_detections += len(r.boxes)

            for box in r.boxes:
                box_conf = float(box.conf[0])
                if box_conf > max_conf_seen:
                    max_conf_seen = box_conf

                label = model.names[int(box.cls[0])]
                all_labels_seen.add(label)

                xyxy = box.xyxy[0].tolist()
                if _box_in_polygon(xyxy, vertices):
                    if not allowed_classes or label.lower() in allowed_classes:
                        kept_boxes.append((label, box_conf, xyxy))

        if kept_boxes:
            total_kept_detections += len(kept_boxes)
            result["has_objects"] = True

            if (frame_count - last_logged_frame) >= fps:
                time_sec = round(frame_count / fps, 1)
                det_list = []
                for lbl, det_conf, xyxy in kept_boxes:
                    det = {
                        "label":      lbl,
                        "confidence": round(float(det_conf), 3),
                        "box":        [round(v, 1) for v in xyxy],
                    }
                    if recognition_enabled:
                        cat = label_category_map.get(lbl.lower())
                        if cat:
                            gal = _get_gallery(cat, config)
                            emb, _ = recognizer.get_embedding(
                                frame, xyxy, cat, rec_cat_cfgs.get(cat, {})
                            )
                            if emb is not None:
                                name, sim = gallery_mod.match(emb, gal, rec_threshold)
                                if name:
                                    det['name'] = name
                                    det['similarity'] = sim
                    det_list.append(det)
                result["events"].append({
                    "time":       f"{time_sec}s",
                    "frame":      frame_count,
                    "detections": det_list,
                })
                last_logged_frame = frame_count
                logging.info(f"Objects at {time_sec}s: {[b[0] for b in kept_boxes]}")

            # Draw kept boxes on the output frame
            for label, bconf, (x1, y1, x2, y2) in kept_boxes:
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                cv2.putText(frame, f"{label} {bconf:.2f}",
                            (int(x1), int(y1) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        if out is not None:
            out.write(frame)

    cap.release()
    if out is not None:
        out.release()

    logging.info(
        f"Processed {frame_count} frames — "
        f"raw_detections={total_raw_detections}, "
        f"in_mask={total_kept_detections}, "
        f"max_conf={max_conf_seen:.2f}, "
        f"labels_seen={sorted(all_labels_seen) or 'none'}"
    )

    if result["has_objects"] and save_annotated:
        result["local_video_path"] = annotated_path
    else:
        if os.path.exists(annotated_path):
            os.remove(annotated_path)

    return result
