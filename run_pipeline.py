#!/usr/bin/env python3
"""
run_pipeline.py — start all three pipeline workers with one command

    python3 run_pipeline.py

Each worker's output is prefixed so you can tell them apart in a single
terminal.  Ctrl+C shuts all three down cleanly.

To watch pipeline progress in a second terminal while this is running:
    watch -n 5 python3 pipeline_status.py
"""
import os
import subprocess
import sys
import signal
import threading
import time

ANALYZE_WORKERS = int(os.getenv('ANALYZE_WORKERS', '1'))

WORKERS = [
    ('watcher',    ['watcher.py']),
    ('transcode',  ['worker_transcode.py']),
    ('control',    ['reset_pipeline.py', '--serve']),
]
for i in range(ANALYZE_WORKERS):
    label = f'analyze-{i+1}' if ANALYZE_WORKERS > 1 else 'analyze'
    WORKERS.append((label, ['worker_analyze.py']))

# Pad labels so columns line up
_MAX_LEN = max(len(label) for label, _ in WORKERS)


def _stream(proc, label):
    """Read a subprocess's stdout+stderr and print each line with a prefix."""
    padded = label.ljust(_MAX_LEN)
    for raw in iter(proc.stdout.readline, b''):
        line = raw.decode(errors='replace').rstrip()
        print(f"[{padded}]  {line}", flush=True)


def main():
    procs = []

    for label, script in WORKERS:
        p = subprocess.Popen(
            [sys.executable] + script,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into stdout
            bufsize=1,
        )
        t = threading.Thread(target=_stream, args=(p, label), daemon=True)
        t.start()
        procs.append((label, p))
        print(f"[run_pipeline]  started {label} (pid {p.pid})", flush=True)

    def shutdown(sig, _frame):
        print('\n[run_pipeline]  shutting down...', flush=True)
        for label, p in procs:
            if p.poll() is None:
                p.terminate()
        for label, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print(f"[run_pipeline]  {label} did not stop — killing", flush=True)
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Monitor for unexpected exits
    while True:
        time.sleep(5)
        for label, p in procs:
            code = p.poll()
            if code is not None:
                print(
                    f"[run_pipeline]  {label} exited unexpectedly (code {code}) — "
                    f"stopping all workers",
                    flush=True,
                )
                shutdown(None, None)


if __name__ == '__main__':
    main()
