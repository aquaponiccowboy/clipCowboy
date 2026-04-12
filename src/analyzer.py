import cv2
import numpy as np
import logging
import os
from ultralytics import YOLO

_model = None


def _get_model(model_path: str = 'models/yolov8n.pt'):
    global _model
    if _model is None:
        logging.info(f"Loading YOLO model from {model_path}...")
        _model = YOLO(model_path)
    return _model


def _box_in_polygon(box_xyxy, polygon) -> bool:
    """Return True if the center of the bounding box is inside the polygon."""
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
    conf = model_cfg.get('confidence', 0.25)
    debug = model_cfg.get('debug', False)

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

    vertices = np.array(mask_config['vertices'], np.int32)

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(annotated_path, fourcc, fps, (width, height))

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
            dbg_name = os.path.splitext(os.path.basename(mp4_path))[0]
            cv2.imwrite(f"debug_{dbg_name}_raw.jpg", frame)
            overlay = frame.copy()
            cv2.polylines(overlay, [vertices], True, (0, 255, 0), 2)
            cv2.imwrite(f"debug_{dbg_name}_mask_overlay.jpg", overlay)
            logging.info(f"Debug frames saved: debug_{dbg_name}_*.jpg")

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
                    kept_boxes.append((label, box_conf, xyxy))

        if kept_boxes:
            total_kept_detections += len(kept_boxes)
            result["has_objects"] = True

            if (frame_count - last_logged_frame) >= fps:
                time_sec = round(frame_count / fps, 1)
                labels = [b[0] for b in kept_boxes]
                result["events"].append({"time": f"{time_sec}s", "objects": labels})
                last_logged_frame = frame_count
                logging.warning(f"Objects at {time_sec}s: {labels}")

            # Draw kept boxes on the output frame
            for label, bconf, (x1, y1, x2, y2) in kept_boxes:
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                cv2.putText(frame, f"{label} {bconf:.2f}",
                            (int(x1), int(y1) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        out.write(frame)

    cap.release()
    out.release()

    logging.info(
        f"Processed {frame_count} frames — "
        f"raw_detections={total_raw_detections}, "
        f"in_mask={total_kept_detections}, "
        f"max_conf={max_conf_seen:.2f}, "
        f"labels_seen={sorted(all_labels_seen) or 'none'}"
    )

    if result["has_objects"]:
        result["local_video_path"] = annotated_path
    else:
        if os.path.exists(annotated_path):
            os.remove(annotated_path)

    return result
