import logging
import os
from pathlib import Path

from src.discord_handler import DiscordHandler

_handler: DiscordHandler | None = None


def _load_dotenv() -> None:
    env_path = Path('.env')
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def init_logging(worker_name: str, level: int = logging.INFO) -> None:
    global _handler
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )
    _load_dotenv()
    token = os.getenv('DISCORD_BOT_TOKEN', '').strip()
    channel_id = os.getenv('DISCORD_LOG_CHANNEL_ID', '').strip()
    if token and channel_id:
        _handler = DiscordHandler(token, channel_id, worker_name)
        _handler.setLevel(logging.WARNING)          # auto-forward warnings + errors
        logging.getLogger().addHandler(_handler)


def discord_notify(message: str) -> None:
    """Send a one-off pipeline milestone message to Discord."""
    if _handler is not None:
        _handler._q.put_nowait(message)
