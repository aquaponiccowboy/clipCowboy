import cv2
import numpy as np
import logging
import boto3
import os
from ultralytics import YOLO

def _get_s3_client(config: dict):
    storage_cfg = config.get('storage', {})
    return boto3.client('s3',
        endpoint_url=storage_cfg.get('endpoint_url'),
        aws_access_key_id=storage_cfg.get('access_key'),
        aws_secret_access_key=storage_cfg.get('secret_key'),
        region_name=storage_cfg.get('region_name', 'texas-coast')
    )

def analyze_video(context: dict, config: dict) -> dict:
    s3 = _get_s3_client(config)
    file_key = context['file_key']
    result = {"success": False, "has_objects": False, "events": [], "local_video_path": None}

    try:
        # Load YOLO model
        model = YOLO('models/yolov8n.pt')

        video_url = s3.generate_presigned_url(
            'get_object', Params={'Bucket': config['storage']['buckets']['input'], 'Key': file_key}, ExpiresIn=3600
        )

        cap = cv2.VideoCapture(video_url)
        if not cap.isOpened():
            return result

        # Get video properties for the VideoWriter
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps == 0 or np.isnan(fps): fps = 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Prepare the local temporary file for the re-encoded video
        temp_output_path = f"annotated_{file_key.replace('.TS', '.mp4')}"
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
            
            # Blank out the ignored areas
            masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
            
            # Run YOLO on the masked frame
            # conf=0.4 means it only tags things it is 40%+ sure about
            yolo_results = model(masked_frame, stream=True, conf=0.4, verbose=False)
            
            objects_in_frame = False
            
            for r in yolo_results:
                boxes = r.boxes
                if len(boxes) > 0:
                    objects_in_frame = True
                    result["has_objects"] = True
                    
                    # Log the event once per second
                    if (frame_count - last_logged_frame) >= fps:
                        time_sec = round(frame_count / fps, 1)
                        detected_labels = [model.names[int(box.cls[0])] for box in boxes]
                        
                        event_data = {
                            "time": f"{time_sec}s",
                            "objects": detected_labels
                        }
                        result["events"].append(event_data)
                        last_logged_frame = frame_count
                        logging.warning(f"🎯 Objects detected at {time_sec}s: {detected_labels}")

                    # Draw the bounding boxes directly onto the frame
                    frame = r.plot() 
            
            # Write the frame (either normal or with boxes) to the new video file
            out.write(frame)

        cap.release()
        out.release()
        
        result["success"] = True
        
        if result["has_objects"]:
            result["local_video_path"] = temp_output_path
        else:
            # If nothing was found, delete the local temp file to save space
            if os.path.exists(temp_output_path):
                os.remove(temp_output_path)

        return result

    except Exception as e:
        logging.error(f"Processing error: {e}")
        return result
