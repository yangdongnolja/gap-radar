import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unittest
from urllib.parse import urlsplit, parse_qs

import httpx
import jwt
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/gpt-18c748cd216b10824cc670dd93d6903d/server'))
from https_auth import OwnerOAuth, SCOPE, password_hash
from https_server import create_app
from adapter import RestAdapter

ORIGIN = 'https://mcp.example.test'
CALLBACK = 'https://chatgpt.com/connector/oauth/test-callback'
PASSWORD = 'test-only-long-owner-password'
KEY = 'test-only-signing-key-' * 4
OWNER_HASH = password_hash(PASSWORD)


class HttpsMcpTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def upstream(request):
            self.calls.append(request)
            return httpx.Response(200, json={'count': 2, 'returned_count': 1, 'truncated': True,
                'filters': {'gu': '노원구'}, 'results': [{'price': 68000, 'area': 59.4}]})
        self.adapter = RestAdapter('https://rest.example.test', 'fixture-upstream-secret', httpx.MockTransport(upstream))
        self.provider = OwnerOAuth(ORIGIN, KEY, OWNER_HASH)
        self.client = TestClient(create_app(self.provider, self.adapter), base_url=ORIGIN)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def register(self, method='client_secret_post', callback=CALLBACK):
        return self.client.post('/register', json={'redirect_uris': [callback],
            'token_endpoint_auth_method': method, 'grant_types': ['authorization_code','refresh_token'],
            'response_types': ['code'], 'scope': SCOPE, 'client_name': 'Test ChatGPT'})

    def begin(self, registration):
        self.verifier = 'p' * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        r = self.client.get('/authorize', params={'client_id': registration['client_id'],
            'redirect_uri': CALLBACK, 'response_type': 'code', 'scope': SCOPE, 'state': 'fixture-state',
            'resource': ORIGIN + '/mcp', 'code_challenge': challenge, 'code_challenge_method': 'S256'}, follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
        form = self.client.get(r.headers['location'])
        self.assertEqual(form.status_code, 200)
        return dict(re.findall(r'name="(request_id|csrf)" value="([^"]+)"', form.text))

    def login_code(self, registration):
        fields = self.begin(registration)
        r = self.client.post('/login', data={**fields, 'password': PASSWORD}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)
        query = parse_qs(urlsplit(r.headers['location']).query)
        self.assertEqual(query['state'], ['fixture-state'])
        self.assertEqual(query['iss'], [self.provider.issuer])
        return query['code'][0]

    def exchange(self, registration, code, **overrides):
        data = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': CALLBACK,
                'client_id': registration['client_id'], 'code_verifier': self.verifier,
                'resource': ORIGIN + '/mcp'}
        if registration.get('client_secret'):
            data['client_secret'] = registration['client_secret']
        return self.client.post('/token', data={**data, **overrides})

    def test_discovery_and_unauthorized_never_call_rest(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        r = self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        self.assertEqual(r.status_code, 401)
        self.assertIn('resource_metadata=', r.headers['www-authenticate'])
        metadata_url = re.search('resource_metadata="([^"]+)"', r.headers['www-authenticate']).group(1)
        resource = self.client.get(metadata_url).json()
        self.assertEqual(resource['resource'], ORIGIN + '/mcp')
        metadata = self.client.get('/.well-known/oauth-authorization-server').json()
        self.assertEqual(metadata['issuer'], self.provider.issuer)
        self.assertIn('S256', metadata['code_challenge_methods_supported'])
        self.assertEqual(self.calls, [])

    def test_complete_oauth_and_real_mcp_tool_protocol(self):
        registered = self.register().json()
        self.assertNotIn(registered['client_secret'], registered['client_id'])
        code = self.login_code(registered)
        r = self.exchange(registered, code)
        self.assertEqual(r.status_code, 200, r.text)
        tokens = r.json()
        headers = {'Authorization': 'Bearer ' + tokens['access_token'], 'Accept': 'application/json, text/event-stream'}
        for method, params in [('initialize', {'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}),
                               ('tools/list', {}),
                               ('tools/call', {'name':'get_transactions','arguments':{'gu':'노원구','year':2026,'month':6,'limit':1}})]:
            response = self.client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
            self.assertEqual(response.status_code, 200, response.text)
            if method == 'tools/list':
                self.assertEqual({x['name'] for x in response.json()['result']['tools']}, {'get_transactions','get_complex_analysis'})
            if method == 'tools/call':
                d = response.json()['result']['structuredContent']
                self.assertEqual((d['count'], d['returned_count'], d['truncated']), (2,1,True))
                self.assertEqual(d['results'][0]['price'], 68000)
                self.assertNotIn('fixture-upstream-secret', response.text)
        self.assertEqual(self.calls[-1].headers['X-API-Key'], 'fixture-upstream-secret')
        self.assertEqual(self.exchange(registered, code).status_code, 400)

    def test_restart_keeps_client_access_and_refresh(self):
        registered = self.register().json()
        token = self.exchange(registered, self.login_code(registered)).json()
        restarted = TestClient(create_app(OwnerOAuth(ORIGIN, KEY, OWNER_HASH), self.adapter), base_url=ORIGIN)
        with restarted as client:
            r = client.post('/token', data={'grant_type':'refresh_token','client_id':registered['client_id'],
                'client_secret':registered['client_secret'],'refresh_token':token['refresh_token'],'resource':ORIGIN+'/mcp'})
            self.assertEqual(r.status_code, 200, r.text)
            r = client.post('/mcp', headers={'Authorization':'Bearer '+token['access_token'], 'Accept':'application/json, text/event-stream'},
                            json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            self.assertEqual(r.status_code, 200)

    def test_bad_redirect_csrf_password_pkce_resource_and_secret(self):
        self.assertEqual(self.register(callback='https://attacker.test/callback').status_code, 400)
        reg = self.register().json()
        fields = self.begin(reg)
        self.assertEqual(self.client.post('/login', data={**fields,'csrf':'wrong','password':PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post('/login', data={**fields,'csrf':'잘못된값','password':PASSWORD}).status_code, 403)
        self.assertEqual(self.client.post('/login', data={**fields,'password':'wrong'}).status_code, 401)
        code = self.login_code(reg)
        self.assertEqual(self.exchange(reg, code, code_verifier='wrong').status_code, 400)
        self.assertEqual(self.exchange(reg, code, resource='https://attacker.test/mcp').status_code, 400)
        self.assertEqual(self.exchange(reg, code, client_secret='wrong').status_code, 401)
        self.assertEqual(self.exchange(reg, code).status_code, 200)

    def test_expired_foreign_audience_and_tampered_tokens(self):
        for changes in ({'aud':'https://other.test/mcp'}, {'exp':int(time.time())-10}, {'iss':'https://other.test/'}):
            payload={'iss':self.provider.issuer,'aud':self.provider.resource,'kind':'access','iat':int(time.time()),
                     'exp':int(time.time())+100,'jti':'test','client_id':'x','scopes':[SCOPE], **changes}
            token = jwt.encode(payload, KEY, algorithm='HS256')
            r = self.client.post('/mcp', headers={'Authorization':'Bearer '+token}, json={})
            self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.post('/mcp',headers={'Authorization':'Bearer invalid'},json={}).status_code,401)
        self.assertEqual(self.calls, [])

    def test_public_client_has_no_refresh_and_request_limits(self):
        reg = self.register(method='none').json()
        tokens = self.exchange(reg, self.login_code(reg)).json()
        self.assertIsNone(tokens.get('refresh_token'))
        self.assertEqual(self.client.post('/login',content=b'x'*17000).status_code,413)
        self.assertEqual(self.client.get('/.env').status_code,404)
        self.assertEqual(self.client.get('/health',headers={'Host':'attacker.test'}).status_code,400)


if __name__ == '__main__':
    unittest.main()
