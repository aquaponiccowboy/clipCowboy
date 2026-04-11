import cv2
import numpy as np
import logging
import boto3

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
    result = {"success": False, "motion": False, "events": []}

    try:
        video_url = s3.generate_presigned_url(
            'get_object', Params={'Bucket': config['storage']['buckets']['input'], 'Key': file_key}, ExpiresIn=3600
        )

        cap = cv2.VideoCapture(video_url)
        if not cap.isOpened():
            return result

        FPS = 30.0 
        WARMUP_FRAMES = 30      # Let the AI learn the background for 1 second
        MIN_PIXELS = 300       # Ignore tiny bugs
        MAX_PIXELS = 2000000     # Ignore full-screen flashes/shadows
        
        back_sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=True)
        vertices = np.array(context['mask_config']['vertices'], np.int32)
        
        ret, frame = cap.read()
        if not ret:
            return result

        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, [vertices], 255)

        frame_count = 1
        last_logged_frame = -999

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            frame_count += 1
            
            masked_frame = cv2.bitwise_and(frame, frame, mask=mask)
            gray = cv2.cvtColor(masked_frame, cv2.COLOR_BGR2GRAY)
            fg_mask = back_sub.apply(gray)
            
            # Skip motion checking during the warm-up period
            if frame_count < WARMUP_FRAMES:
                continue
                
            _, fg_mask = cv2.threshold(fg_mask, 254, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                
                # Check if the motion is within our valid size parameters
                if MIN_PIXELS < area < MAX_PIXELS: 
                    result["motion"] = True
                    
                    if (frame_count - last_logged_frame) >= FPS:
                        x, y, w, h = cv2.boundingRect(contour)
                        time_sec = round(frame_count / FPS, 1)
                        
                        # Save a physical Snapshot image with a Red Box
                        snapshot_name = f"ALERT_{file_key}_{time_sec}s.jpg"
                        # Draw a rectangle on the original frame (BGR format: 0,0,255 is Red, 2 is thickness)
                        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 2)
                        cv2.imwrite(snapshot_name, frame)
                        logging.warning(f"📸 Saved visual proof to {snapshot_name}")
                        
                        event_data = {
                            "time": f"{time_sec}s",
                            "size": int(area),
                            "location": f"x:{x} y:{y}"
                        }
                        result["events"].append(event_data)
                        last_logged_frame = frame_count

        cap.release()
        result["success"] = True
        return result

    except Exception as e:
        logging.error(f"Processing error: {e}")
        return result
