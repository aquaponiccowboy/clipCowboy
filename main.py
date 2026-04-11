import yaml
import logging
import json
from src.ingestion import get_target_keys
from src.router import get_camera_context
from src.processor import analyze_video
from src.persistence import is_processed, commit_result

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config(path="config.yml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

if __name__ == "__main__":
    logging.info("Booting SecurityCowboy Smart Pipeline...")
    config = load_config()
    
    targets = get_target_keys(config)
    
    for target in targets:
        if is_processed(target, config):
            continue

        context = get_camera_context(target, config)
        if not context:
            continue
            
        # Run the AI
        result = analyze_video(context, config)
        
        if result['success']:
            if result['motion']:
                logging.warning(f"🚨 Motion Detected in {target}!")
                # Print the timestamp log nicely formatted
                print(json.dumps(result['events'], indent=2))
            
            # Persist intelligently based on motion
            commit_result(target, config, result['motion'])
        else:
            logging.error(f"Skipping persistence for {target} due to crash.")

    logging.info("Pipeline run complete.")
