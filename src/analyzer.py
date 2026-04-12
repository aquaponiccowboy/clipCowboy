import cv2
import numpy as np
import logging
import os
from ultralytics import YOLO

# Load model once at import time
_model = None

def _get_model(model_path: str = 'models/yolov8n.pt'):
    global _model
    if _model is None:
        logging.info(f"Loading YOLO model from {model_path}...")
        _model = YOLO(model_path)
    return _model


def detect_objects(mp4_path: str, mask_config: dict, config: dict) -> dict:
    """
    Run YOLO on a local MP4 file.
    Returns a result dict with has_objects, events, and local_video_path.
    """
    result = {"has_objects": False, "events": [], "local_video_path": None}
    model = _get_model(config.get('model', {}).get('path', 'models/yolov8n.pt'))
    conf = config.get('model', {}).get('confidence', 0.4)

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
    logging.info(f"{mp4_path}: {width}x{height} @ {fps:.1f}fps, ~{total_frames} frames")

    vertices = np.array(mask_config['vertices'], np.int32)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [vertices], 255)

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(annotated_path, fourcc, fps, (width, height))

    frame_count = 0
    last_logged_frame = -999

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_count += 1
        masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
        yolo_results = model(masked_frame, stream=True, conf=conf, verbose=False)

        for r in yolo_results:
            if len(r.boxes) > 0:
                result["has_objects"] = True

                if (frame_count - last_logged_frame) >= fps:
                    time_sec = round(frame_count / fps, 1)
                    labels = [model.names[int(b.cls[0])] for b in r.boxes]
                    result["events"].append({"time": f"{time_sec}s", "objects": labels})
                    last_logged_frame = frame_count
                    logging.warning(f"Objects at {time_sec}s: {labels}")

                frame = r.plot()

        out.write(frame)

    cap.release()
    out.release()

    logging.info(f"Processed {frame_count} frames — has_objects={result['has_objects']}")

    if result["has_objects"]:
        result["local_video_path"] = annotated_path
    else:
        if os.path.exists(annotated_path):
            os.remove(annotated_path)

    return result
