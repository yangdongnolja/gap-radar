"""Read-only public deployment check. Never print credentials or OAuth codes."""
import base64
import hashlib
import logging
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import parse_qs, urlsplit

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://gap-radar-mcp-https.onrender.com'


def verify():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    password = (ROOT / '.state/credential-recovery-final/connection-password.txt').read_text().strip()
    key = dotenv_values(ROOT / '.env')['GAP_RADAR_API_KEY']
    callback = 'https://chatgpt.com/connector/oauth/deployment-verification'
    with httpx.Client(base_url=ORIGIN, timeout=120, follow_redirects=False) as client:
        health = client.get('/health')
        assert health.status_code == 200, 'health failed'
        assert client.post('/mcp', json={}).status_code == 401, 'authentication missing'
        metadata = client.get('/.well-known/oauth-authorization-server').json()
        assert metadata['issuer'] == ORIGIN + '/', 'issuer mismatch'
        registered = client.post('/register', json={'redirect_uris':[callback],
            'grant_types':['authorization_code','refresh_token'],'response_types':['code'],
            'token_endpoint_auth_method':'client_secret_post','scope':'transactions:read',
            'client_name':'GAP-RADAR deployment verification'})
        assert registered.status_code == 201, 'registration failed'
        reg = registered.json()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        auth = client.get('/authorize', params={'client_id':reg['client_id'],'redirect_uri':callback,
            'response_type':'code','scope':'transactions:read','resource':ORIGIN+'/mcp',
            'code_challenge':challenge,'code_challenge_method':'S256','state':'deployment-check'})
        assert auth.status_code == 302, 'authorization failed'
        form = client.get(auth.headers['location'])
        assert form.status_code == 200, 'login page failed'
        fields = dict(re.findall(r'name="(request_id|csrf)" value="([^"]+)"', form.text))
        login = client.post('/login', data={**fields,'password':password})
        assert login.status_code == 303, 'owner login failed'
        query = parse_qs(urlsplit(login.headers['location']).query)
        assert query['state'] == ['deployment-check'], 'state mismatch'
        result = client.post('/token', data={'grant_type':'authorization_code','client_id':reg['client_id'],
            'client_secret':reg['client_secret'],'code':query['code'][0],'code_verifier':verifier,
            'redirect_uri':callback,'resource':ORIGIN+'/mcp'})
        assert result.status_code == 200, 'token exchange failed'
        token = result.json()
        headers = {'Authorization':'Bearer '+token['access_token'],'Accept':'application/json, text/event-stream'}
        listing = client.post('/mcp', headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
        assert listing.status_code == 200, 'tool discovery failed'
        assert {x['name'] for x in listing.json()['result']['tools']} == {'get_transactions','get_complex_analysis'}
        print('Public HTTPS health/401/OAuth/PKCE/tool discovery PASS')
        for name, params, endpoint in [
            ('get_transactions',dict(gu='노원구',year=2026,month=6,limit=1),'/api/v1/transactions'),
            ('get_complex_analysis',dict(gu='노원구',from_year_month='2026-06',to_year_month='2026-07',
                min_price=60000,max_price=80000,min_area=59,max_area=85),'/api/v1/complex-analysis')]:
            r = client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':name,'arguments':params}})
            assert r.status_code == 200, 'tool HTTP failure'
            body = r.json()['result']
            assert not body.get('isError'), 'upstream tool error'
            d = body['structuredContent']
            direct = httpx.get('https://gap-radar-api-vbg8.onrender.com'+endpoint, params=params,
                              headers={'X-API-Key':key},timeout=120).json()
            assert d == direct, 'REST response mismatch'
            print(name, 'public HTTPS/live REST comparison PASS; count:', d['count'])
        refreshed = client.post('/token',data={'grant_type':'refresh_token','client_id':reg['client_id'],
            'client_secret':reg['client_secret'],'refresh_token':token['refresh_token'],'resource':ORIGIN+'/mcp'})
        assert refreshed.status_code == 200, 'refresh failed'
        print('Public refresh PASS; actual ChatGPT installation: not verified by this script')


if __name__ == '__main__':
    try:
        verify()
    except Exception as error:
        # Exceptions from HTTP clients may include URLs; do not print their text.
        print('Public verification failed:', type(error).__name__)
        sys.exit(1)
