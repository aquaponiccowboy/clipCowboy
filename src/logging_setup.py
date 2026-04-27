import logging
import os

from src.discord_handler import DiscordHandler


def init_logging(worker_name: str, level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    url = os.getenv('DISCORD_LOG_WEBHOOK_URL', '').strip()
    if url:
        handler = DiscordHandler(url, worker_name)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
