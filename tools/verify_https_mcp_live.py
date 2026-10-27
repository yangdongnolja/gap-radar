"""Local OAuth HTTP protocol + live REST comparison, no production changes."""
import base64
import hashlib
import logging
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit, parse_qs

import httpx
from dotenv import dotenv_values
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/gpt-18c748cd216b10824cc670dd93d6903d/server'))
from adapter import RestAdapter, PRODUCTION_URL
from https_auth import OwnerOAuth, SCOPE, password_hash
from https_server import create_app


def verify():
    key = dotenv_values(ROOT / '.env')['GAP_RADAR_API_KEY']
    origin = 'https://local-verification.example.test'
    password = secrets.token_urlsafe(32)
    provider = OwnerOAuth(origin, secrets.token_urlsafe(64), password_hash(password))
    app = create_app(provider, RestAdapter(api_key=key))
    logging.getLogger('httpx').setLevel(logging.WARNING)
    callback = 'https://chatgpt.com/connector/oauth/local-verification'
    with TestClient(app, base_url=origin) as client:
        registered = client.post('/register', json={'redirect_uris':[callback], 'token_endpoint_auth_method':'client_secret_post',
            'grant_types':['authorization_code','refresh_token'], 'response_types':['code'],'scope':SCOPE}).json()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        authorize = client.get('/authorize', params={'client_id':registered['client_id'],'redirect_uri':callback,
            'response_type':'code','scope':SCOPE,'resource':provider.resource,
            'code_challenge':challenge,'code_challenge_method':'S256'}, follow_redirects=False)
        login = client.get(authorize.headers['location'])
        fields = dict(re.findall(r'name="(request_id|csrf)" value="([^"]+)"', login.text))
        completed = client.post('/login', data={**fields,'password':password}, follow_redirects=False)
        code = parse_qs(urlsplit(completed.headers['location']).query)['code'][0]
        tokens = client.post('/token', data={'grant_type':'authorization_code','client_id':registered['client_id'],
            'client_secret':registered['client_secret'],'redirect_uri':callback,'code':code,
            'code_verifier':verifier,'resource':provider.resource}).json()
        headers = {'Authorization':'Bearer '+tokens['access_token'],'Accept':'application/json, text/event-stream'}
        for name, params, endpoint in [
            ('get_transactions',dict(gu='노원구',year=2026,month=6,limit=1),'/api/v1/transactions'),
            ('get_complex_analysis',dict(gu='노원구',from_year_month='2026-06',to_year_month='2026-07',
                min_price=60000,max_price=80000,min_area=59,max_area=85),'/api/v1/complex-analysis')]:
            r = client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':name,'arguments':params}})
            result = r.json()['result']
            assert not result.get('isError'), 'MCP tool returned an error'
            actual = result['structuredContent']
            direct = httpx.get(PRODUCTION_URL+endpoint, params=params, headers={'X-API-Key':key}, timeout=120).json()
            assert actual == direct, 'REST and MCP responses differ'
            print(name, 'OAuth HTTP/live REST exact comparison PASS; count:', actual['count'])
    print('Actual public deployment / ChatGPT registration: NOT TESTED')


if __name__ == '__main__':
    try:
        verify()
    except Exception as error:
        print('Verification failed:', type(error).__name__)
        sys.exit(1)
