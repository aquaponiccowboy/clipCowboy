#!/usr/bin/env python3
"""
reset_pipeline.py — wipe processed-file history so test clips re-run.
Gallery (enrolled recognition data) is always preserved.

By default, commands are sent to the running pipeline's HTTP control server
(http://localhost:8765). Use --direct to bypass HTTP and connect to the DB
directly (useful when the pipeline container is stopped).

Usage:
  python3 reset_pipeline.py                          # dry-run: show row counts
  python3 reset_pipeline.py --confirm                # wipe processing history + DLQ
  python3 reset_pipeline.py --confirm --scores       # also clear scores/highlights/scenes
  python3 reset_pipeline.py --confirm --minio        # also clear MinIO output buckets
  python3 reset_pipeline.py --url http://host:8765   # target a remote pipeline
  python3 reset_pipeline.py --direct                 # bypass HTTP, connect to DB directly
  python3 reset_pipeline.py --serve                  # run HTTP control server on port 8765
"""
import argparse
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from src.database import ensure_schema
from src.logging_setup import init_logging, discord_notify
from src.config import load_config

DEFAULT_URL = os.getenv('PIPELINE_CONTROL_URL', 'http://localhost:8765')

CONTROL_PORT = int(os.getenv('PIPELINE_CONTROL_PORT', '8765'))

PIPELINE_TABLES = [
    'processed_files',
    'dlq_files',
]
SCORE_TABLES = [
    'clip_scores',
    'highlights',
    'scene_labels',
]
MINIO_BUCKETS = ['converted', 'archive', 'annotated', 'quarantine', 'highlights']




def _get_conn(config):
    from src.database import get_connection
    return get_connection(config)


def row_counts(config) -> dict:
    conn = _get_conn(config)
    cur = conn.cursor()
    counts = {}
    for t in PIPELINE_TABLES + SCORE_TABLES + ['gallery']:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        except Exception:
            counts[t] = None
    conn.close()
    return counts


def do_reset(config, scores=False, minio=False) -> dict:
    conn = _get_conn(config)
    cur = conn.cursor()
    cleared = {}

    tables = PIPELINE_TABLES + (SCORE_TABLES if scores else [])
    for t in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            n = cur.fetchone()[0]
            cur.execute(f"DELETE FROM {t}")
            conn.commit()
            cleared[t] = n
        except Exception as e:
            cleared[t] = f'error: {e}'
    conn.close()

    if minio:
        cleared['minio'] = _clear_minio(config)

    return cleared


def _clear_minio(config) -> dict:
    from src.persistence import _get_s3_client
    client = _get_s3_client(config)
    s = config.get('storage', {})
    result = {}
    for bucket_key in MINIO_BUCKETS:
        bucket = s.get('buckets', {}).get(bucket_key)
        if not bucket:
            continue
        n = 0
        try:
            paginator = client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=bucket):
                objects = [{'Key': o['Key']} for o in page.get('Contents', [])]
                if objects:
                    client.delete_objects(Bucket=bucket, Delete={'Objects': objects})
                    n += len(objects)
            result[bucket] = n
        except Exception as e:
            result[bucket] = f'error: {e}'
    return result


def _format_summary(cleared: dict) -> str:
    lines = ['Pipeline reset complete:']
    for k, v in cleared.items():
        if isinstance(v, dict):
            for bk, bv in v.items():
                lines.append(f'  minio/{bk}: {bv} objects deleted')
        else:
            lines.append(f'  {k}: {v} rows cleared')
    return '\n'.join(lines)


class _Handler(BaseHTTPRequestHandler):
    config = None

    def log_message(self, *args):
        pass  # suppress access log noise

    def _respond(self, code, body: dict):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlparse(self.path).path == '/status':
            self._respond(200, {'counts': row_counts(self.config)})
        else:
            self._respond(404, {'error': 'not found'})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/reset':
            self._respond(404, {'error': 'not found'})
            return

        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length) or b'{}')
        scores = bool(body.get('scores', False))
        minio  = bool(body.get('minio', False))

        cleared = do_reset(self.config, scores=scores, minio=minio)
        summary = _format_summary(cleared)
        logging.info(summary)
        discord_notify(f"🔄 **[reset]** {summary}")
        self._respond(200, {'cleared': cleared, 'summary': summary})


def serve(config):
    _Handler.config = config
    server = HTTPServer(('0.0.0.0', CONTROL_PORT), _Handler)
    logging.info(f"Pipeline control server listening on port {CONTROL_PORT}")
    server.serve_forever()


def _http_status(url: str) -> None:
    req = urllib.request.Request(f'{url}/status')
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    counts = data.get('counts', {})
    print('\nCurrent row counts (gallery preserved):')
    for t, n in counts.items():
        marker = '  [protected]' if t == 'gallery' else ''
        print(f'  {t}: {n}{marker}')


def _http_reset(url: str, scores: bool, minio: bool) -> None:
    body = json.dumps({'scores': scores, 'minio': minio}).encode()
    req = urllib.request.Request(
        f'{url}/reset', data=body,
        headers={'Content-Type': 'application/json'}, method='POST'
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    print('\n' + data.get('summary', json.dumps(data, indent=2)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirm', action='store_true',
                        help='Actually perform the reset (default is dry-run)')
    parser.add_argument('--scores', action='store_true',
                        help='Also clear clip_scores, highlights, scene_labels')
    parser.add_argument('--minio', action='store_true',
                        help='Also delete objects in MinIO output buckets')
    parser.add_argument('--url', default=DEFAULT_URL,
                        help=f'Pipeline control server URL (default: {DEFAULT_URL})')
    parser.add_argument('--direct', action='store_true',
                        help='Bypass HTTP and connect to DB directly (pipeline stopped)')
    parser.add_argument('--serve', action='store_true',
                        help='Run HTTP control server on port 8765')
    args = parser.parse_args()

    if args.serve:
        config = load_config()
        init_logging('reset')
        ensure_schema(config)
        serve(config)
        return

    if args.direct:
        config = load_config()
        init_logging('reset')
        ensure_schema(config)
        counts = row_counts(config)
        print('\nCurrent row counts (gallery preserved):')
        for t, n in counts.items():
            marker = '  [protected]' if t == 'gallery' else ''
            print(f'  {t}: {n}{marker}')
        if not args.confirm:
            print('\nDry-run. Pass --confirm to execute.')
            return
        cleared = do_reset(config, scores=args.scores, minio=args.minio)
        print('\n' + _format_summary(cleared))
        return

    # Default: talk to the running pipeline's control server via HTTP
    try:
        _http_status(args.url)
    except urllib.error.URLError as e:
        print(f'\nCannot reach pipeline at {args.url}: {e.reason}')
        print('Is the pipeline running? Try --direct to connect to the DB directly.')
        sys.exit(1)

    if not args.confirm:
        print('\nDry-run. Pass --confirm to execute.')
        return

    try:
        _http_reset(args.url, scores=args.scores, minio=args.minio)
    except urllib.error.URLError as e:
        print(f'\nReset failed: {e.reason}')
        sys.exit(1)


if __name__ == '__main__':
    main()
