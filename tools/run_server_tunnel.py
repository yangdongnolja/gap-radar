"""Free Render web service: public wake page, private MCP via existing tunnel."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def build_command(environment):
    tunnel_id = environment.get('CONTROL_PLANE_TUNNEL_ID', '')
    if not re.fullmatch(r'tunnel_[A-Za-z0-9]+', tunnel_id):
        raise ValueError('CONTROL_PLANE_TUNNEL_ID is required')
    for key in ('CONTROL_PLANE_API_KEY', 'GAP_RADAR_API_KEY'):
        if not environment.get(key, '').strip():
            raise ValueError(key + ' is required')
    return [str(ROOT / '.local/server-tunnel/tunnel-client'), 'run',
            '--control-plane.tunnel-id', tunnel_id,
            '--control-plane.base-url', 'https://api.openai.com',
            '--control-plane.api-key', 'env:CONTROL_PLANE_API_KEY',
            '--mcp.command', f'command="{sys.executable}" "{ROOT / "tools/run_gap_radar_mcp.py"}"',
            '--mcp.stdio-send-initialized-notification',
            '--health.listen-addr', '127.0.0.1:8765']


def ready():
    try:
        with urlopen('http://127.0.0.1:8765/readyz', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/health', '/ready'):
            self.send_error(404)
            return
        is_ready = ready()
        data = json.dumps({'status': 'running', 'tunnel_ready': is_ready,
                           'note': 'Readiness is not proof of a successful ChatGPT tool call.'}).encode()
        self.send_response(503 if self.path == '/ready' and not is_ready else 200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass  # Do not log user URLs, headers or credentials.


def main():
    environment = os.environ.copy()
    environment['LOG_HTTP_RAW_UNSAFE'] = 'false'
    environment['ALLOW_REMOTE_UI'] = 'false'
    environment['OPEN_WEB_UI'] = 'false'
    command = build_command(environment)
    child = subprocess.Popen(command, env=environment)
    http = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '10000'))), Handler)
    http.timeout = 1

    def terminate(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        while child.poll() is None:
            http.handle_request()
        return child.returncode or 1
    except KeyboardInterrupt:
        return 0
    finally:
        http.server_close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except ValueError as error:
        print(str(error), file=sys.stderr)  # Fixed configuration names only.
        sys.exit(1)
