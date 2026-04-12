import yaml
import logging
import time
from src.ingestion import get_target_keys
from src.router import get_camera_context
from src.processor import analyze_video
from src.persistence import is_processed, commit_result
from src.database import ensure_schema

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config(path="config.yml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

if __name__ == "__main__":
    logging.info("Booting SecurityCowboy Continuous Batch Processor (YOLO Edition)...")
    config = load_config()
    ensure_schema(config)

    try:
        while True:
            # 1. Check the queue — filter already-processed files before anything else
            all_targets = get_target_keys(config)
            targets = [t for t in all_targets if not is_processed(t, config)]

            if not targets:
                logging.info("No new files to process. Sleeping for 60 seconds...")
                time.sleep(60)
                continue

            logging.info(f"Found {len(targets)} files in queue. Starting batch processing...")

            # 2. Process the batch
            for target in targets:
                context = get_camera_context(target, config)
                if not context:
                    continue
                    
                result = analyze_video(context, config)
                
                # Check for success and the new 'has_objects' key
                if result.get('success'):
                    if result.get('has_objects'):
                        logging.warning(f"🚨 Objects Detected in {target}! Committing annotated video.")
                    else:
                        logging.info(f"💤 No objects in {target}. Moving to quarantine.")
                    
                    # We now pass the ENTIRE result dictionary to persistence
                    commit_result(target, config, result, camera_id=context['id'])
                else:
                    logging.error(f"Skipping persistence for {target} due to processing crash.")
            
            logging.info("Batch complete. Re-scanning bucket...")
            
    except KeyboardInterrupt:
        logging.info("\nCaught shutdown signal. Halting SecurityCowboy pipeline safely.")
