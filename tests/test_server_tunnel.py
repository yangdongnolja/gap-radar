import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError
import json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('server_tunnel', ROOT / 'tools/run_server_tunnel.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ServerTunnelTests(unittest.TestCase):
    def test_public_surface_is_status_only(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        try:
            with patch.object(module, 'ready', return_value=False):
                with urlopen(base + '/health') as response:
                    self.assertFalse(json.load(response)['tunnel_ready'])
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + '/ready')
                self.assertEqual(caught.exception.code, 503)
            for path in ('/mcp', '/ui', '/.env', '/logs', '/api/v1/transactions'):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(base + path)
                self.assertEqual(caught.exception.code, 404)
        finally:
            http.shutdown()
            http.server_close()
            thread.join()

    def test_fail_closed_without_configuration(self):
        for environment in ({}, {'CONTROL_PLANE_TUNNEL_ID': 'tunnel_test'},
                            {'CONTROL_PLANE_TUNNEL_ID': 'wrong', 'CONTROL_PLANE_API_KEY': 'secret'}):
            with self.assertRaises(ValueError):
                module.build_command(environment)

    def test_secrets_never_in_arguments(self):
        environment = {'CONTROL_PLANE_TUNNEL_ID': 'tunnel_test',
                       'CONTROL_PLANE_API_KEY': 'control-secret-fixture',
                       'GAP_RADAR_API_KEY': 'upstream-secret-fixture'}
        command = module.build_command(environment)
        text = ' '.join(command)
        self.assertNotIn(environment['CONTROL_PLANE_API_KEY'], text)
        self.assertNotIn(environment['GAP_RADAR_API_KEY'], text)
        self.assertIn('env:CONTROL_PLANE_API_KEY', command)
        self.assertIn('127.0.0.1:8765', command)
        self.assertNotIn('--allow-remote-ui', command)


if __name__ == '__main__':
    unittest.main()
