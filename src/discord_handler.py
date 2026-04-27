import logging
import os
import queue
import threading
import time

try:
    import requests as _requests
except ImportError:
    _requests = None

_DRAIN_INTERVAL = 1.5   # seconds between Discord POSTs
_MAX_BATCH = 15          # lines per message
_CHAR_LIMIT = 2000

_LEVEL_ICON = {
    logging.DEBUG:    '🔍',
    logging.INFO:     '▸',
    logging.WARNING:  '⚠️',
    logging.ERROR:    '❌',
    logging.CRITICAL: '🚨',
}


class DiscordHandler(logging.Handler):
    def __init__(self, webhook_url: str, worker_name: str):
        super().__init__()
        self._url = webhook_url
        self._worker = worker_name
        self._q: queue.Queue = queue.Queue()
        t = threading.Thread(target=self._drain_loop, daemon=True)
        t.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            icon = _LEVEL_ICON.get(record.levelno, '▸')
            line = f"**[{self._worker}]** {icon} {record.getMessage()}"
            self._q.put_nowait(line)
        except Exception:
            pass

    def _drain_loop(self) -> None:
        while True:
            time.sleep(_DRAIN_INTERVAL)
            self._flush()

    def _flush(self) -> None:
        if _requests is None:
            return
        lines = []
        try:
            while len(lines) < _MAX_BATCH:
                lines.append(self._q.get_nowait())
        except queue.Empty:
            pass
        if not lines:
            return
        text = '\n'.join(lines)[:_CHAR_LIMIT]
        try:
            r = _requests.post(self._url, json={'content': text}, timeout=5)
            if r.status_code == 429:
                retry_after = r.json().get('retry_after', 2)
                time.sleep(retry_after)
                # put lines back for next drain cycle
                for line in lines:
                    self._q.put_nowait(line)
        except Exception:
            pass
