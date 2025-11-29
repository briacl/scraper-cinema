#!/usr/bin/env python3
"""Petit serveur HTTP pour lancer `scrape.py` depuis le navigateur.

Usage:
  python scrape_server.py [--port 8000]

Endpoints:
  - Static files: served like `python -m http.server` (serves current folder)
  - GET /api/scrape?url=...&film=...&salle_name=... -> lance `scrape.py` (bloquant), lit le dernier run
    et renvoie le JSON film-level correspondant (application/json).

Implémentation: utilise http.server pour servir fichiers et un handler personnalisé
pour l'API. Ne nécessite pas d'installation externe.
"""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import subprocess
import sys
import os
from pathlib import Path
import json
import shlex


DATA_DIR = Path("searching_film_data")


def sanitize_for_filename(s: str) -> str:
    if not s:
        return "unknown"
    import re
    safe = re.sub(r"[^0-9A-Za-z\-]+", "_", s)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/scrape':
            qs = parse_qs(parsed.query)
            url = qs.get('url', [None])[0]
            film = qs.get('film', [None])[0]
            salle_name = qs.get('salle_name', [None])[0]
            if not url or not film:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'url and film parameters required'}).encode('utf-8'))
                return

            # run scrape.py
            python = sys.executable or 'python'
            cmd = [python, 'scrape.py', url, '--film', film]
            if salle_name:
                cmd.extend(['--salle-name', salle_name])

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'scrape timeout'}).encode('utf-8'))
                return

            if proc.returncode != 0:
                # return stderr for debugging
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'scrape failed', 'stdout': proc.stdout, 'stderr': proc.stderr}).encode('utf-8'))
                return

            # read latest_run.txt to find run dir
            latest_file = DATA_DIR / 'latest_run.txt'
            if not latest_file.exists():
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'latest_run.txt not found'}).encode('utf-8'))
                return
            ts = latest_file.read_text(encoding='utf-8').strip()
            run_dir = DATA_DIR / ts
            if not run_dir.exists():
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'run dir not found', 'run_dir': str(run_dir)}).encode('utf-8'))
                return

            # attempt to find film file
            safe = sanitize_for_filename(film)
            candidates = list(run_dir.glob(f"{safe}*_data_by_*_by_allocine.json")) + list(run_dir.glob(f"{safe}_data_by_*_by_allocine.json"))
            # if none, fallback to any *_data_by_* file containing safe in name
            if not candidates:
                candidates = [p for p in run_dir.glob('*_data_by_*_by_allocine.json') if safe in p.name]

            if not candidates:
                self.send_response(404)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'film file not found', 'search': safe}).encode('utf-8'))
                return

            # pick newest candidate by mtime
            candidates.sort(key=lambda p: p.stat().st_mtime)
            chosen = candidates[-1]
            data = chosen.read_text(encoding='utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(data.encode('utf-8'))
            return

        # otherwise fallback to default handler (serves static files)
        return super().do_GET()


def run(port=8000):
    addr = ('', port)
    with ThreadingHTTPServer(addr, Handler) as httpd:
        print(f"Serving on http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('Stopping')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8001)
    args = p.parse_args()
    run(args.port)
