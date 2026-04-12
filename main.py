import yaml
import logging
import time
from src.ingestion import get_raw_keys, get_converted_keys
from src.router import get_camera_context
from src.processor import process_ts, process_mp4
from src.persistence import is_processed, commit_result
from src.database import ensure_schema

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config(path="config.yml"):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

if __name__ == "__main__":
    logging.info("Booting SecurityCowboy Pipeline...")
    config = load_config()
    ensure_schema(config)

    try:
        while True:
            # ── Phase 1: Transcode raw .TS files ─────────────────────────────
            raw = get_raw_keys(config)
            for ts_key in raw:
                context = get_camera_context(ts_key, config)
                if not context:
                    continue
                mp4_key = ts_key.rsplit('.', 1)[0] + '.mp4'
                if is_processed(mp4_key, config):
                    logging.info(f"Already analyzed: {mp4_key} — skipping transcode.")
                    continue
                result = process_ts(ts_key, context, config)
                if result:
                    commit_result(mp4_key, config, result, camera_id=context['id'])

            # ── Phase 2: Analyze any converted .MP4s not yet in DB ───────────
            converted = get_converted_keys(config)
            queue = [k for k in converted if not is_processed(k, config)]

            if not queue and not raw:
                logging.info("Nothing to do. Sleeping for 60 seconds...")
                time.sleep(60)
                continue

            for mp4_key in queue:
                context = get_camera_context(mp4_key, config)
                if not context:
                    continue
                result = process_mp4(mp4_key, context, config)
                if result:
                    commit_result(mp4_key, config, result, camera_id=context['id'])

            logging.info("Batch complete. Re-scanning...")

    except KeyboardInterrupt:
        logging.info("\nCaught shutdown signal. Halting pipeline safely.")
