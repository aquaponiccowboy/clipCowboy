import cv2
import numpy as np
import logging
import boto3
import os
from ultralytics import YOLO

# Load model once at import time, not on every call
_model = None

def _get_model():
    global _model
    if _model is None:
        _model = YOLO('models/yolov8n.pt')
    return _model

def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'us-east-1')
    )

def analyze_video(context: dict, config: dict) -> dict:
    s3 = _get_s3_client(config)
    file_key = context['file_key']
    result = {"success": False, "has_objects": False, "events": [], "local_video_path": None}
    temp_input_path = f"temp_input_{file_key.replace('/', '_')}"
    temp_output_path = f"annotated_{file_key.replace('.TS', '.mp4').replace('/', '_')}"

    try:
        # Download to local disk first — streaming .TS over HTTP is unreliable with OpenCV
        logging.info(f"Downloading {file_key} for processing...")
        s3.download_file(config['storage']['buckets']['input'], file_key, temp_input_path)

        model = _get_model()
        cap = cv2.VideoCapture(temp_input_path)
        if not cap.isOpened():
            logging.error(f"OpenCV could not open {temp_input_path}")
            return result

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps == 0 or np.isnan(fps):
            fps = 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        logging.info(f"{file_key}: {width}x{height} @ {fps:.1f}fps, ~{total_frames} frames")

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(temp_output_path, fourcc, fps, (width, height))

        vertices = np.array(context['mask_config']['vertices'], np.int32)
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [vertices], 255)

        frame_count = 0
        last_logged_frame = -999

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_count += 1
            masked_frame = cv2.bitwise_and(frame, frame, mask=mask)

            yolo_results = model(masked_frame, stream=True, conf=0.4, verbose=False)

            for r in yolo_results:
                if len(r.boxes) > 0:
                    result["has_objects"] = True

                    if (frame_count - last_logged_frame) >= fps:
                        time_sec = round(frame_count / fps, 1)
                        detected_labels = [model.names[int(box.cls[0])] for box in r.boxes]
                        result["events"].append({"time": f"{time_sec}s", "objects": detected_labels})
                        last_logged_frame = frame_count
                        logging.warning(f"Objects at {time_sec}s: {detected_labels}")

                    frame = r.plot()

            out.write(frame)

        cap.release()
        out.release()

        logging.info(f"{file_key}: read {frame_count} frames, has_objects={result['has_objects']}")
        result["success"] = True

        if result["has_objects"]:
            result["local_video_path"] = temp_output_path
        else:
            if os.path.exists(temp_output_path):
                os.remove(temp_output_path)

        return result

    except Exception as e:
        logging.error(f"Processing error for {file_key}: {e}")
        return result

    finally:
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)
