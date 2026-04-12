import yaml
import logging
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
    logging.info("Booting SecurityCowboy Smart Pipeline...")
    config = load_config()

    ensure_schema(config)

    targets = get_target_keys(config)

    for target in targets:
        if is_processed(target, config):
            continue

        context = get_camera_context(target, config)
        if not context:
            continue

        result = analyze_video(context, config)

        if result['success']:
            if result['motion']:
                logging.warning(f"Motion Detected in {target}!")

            commit_result(
                target,
                config,
                has_motion=result['motion'],
                camera_id=context['id'],
                events=result['events']
            )
        else:
            logging.error(f"Skipping persistence for {target} due to crash.")

    logging.info("Pipeline run complete.")
