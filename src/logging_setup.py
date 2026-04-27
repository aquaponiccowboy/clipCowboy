import logging
import os

from src.discord_handler import DiscordHandler


def init_logging(worker_name: str, level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    token = os.getenv('DISCORD_BOT_TOKEN', '').strip()
    channel_id = os.getenv('DISCORD_LOG_CHANNEL_ID', '').strip()
    if token and channel_id:
        handler = DiscordHandler(token, channel_id, worker_name)
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
