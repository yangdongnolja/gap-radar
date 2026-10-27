"""Authenticated Streamable HTTP entry point; existing stdio stays unchanged."""
import os
from urllib.parse import urlsplit, parse_qs

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from adapter import load_settings
from https_auth import OwnerOAuth, SCOPE
from server import create_server


class RequestLimits:
    """Bound auth request bodies without logging headers, codes or passwords."""
    def __init__(self, app, resource):
        self.app = app
        self.resource = resource

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['method'] == 'POST':
            limit = 65536 if scope['path'] == '/mcp' else 16384
            messages, size = [], 0
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                size += len(message.get('body', b''))
                if size > limit:
                    await JSONResponse({'error': 'request_too_large'}, status_code=413)(scope, receive, send)
                    return
                messages.append(message)
                if not message.get('more_body'):
                    break
            if scope['path'] == '/token':
                try:
                    form = parse_qs(b''.join(m.get('body', b'') for m in messages).decode('utf-8'))
                except UnicodeDecodeError:
                    form = {'resource': ['invalid']}
                if 'resource' in form and form['resource'] != [self.resource]:
                    await JSONResponse({'error': 'invalid_target'}, status_code=400)(scope, receive, send)
                    return
            async def replay():
                return messages.pop(0) if messages else await receive()
            await self.app(scope, replay, send)
            return
        await self.app(scope, receive, send)


def create_app(provider=None, adapter=None):
    load_settings()
    provider = provider or OwnerOAuth(os.environ.get('MCP_PUBLIC_URL', ''),
        os.environ.get('MCP_TOKEN_SECRET', ''), os.environ.get('MCP_LOGIN_PASSWORD_HASH', ''))
    host = urlsplit(provider.origin).hostname
    mcp = create_server(adapter, auth_provider=provider,
        auth=AuthSettings(issuer_url=provider.issuer, resource_server_url=provider.resource,
                          required_scopes=[SCOPE], validate_token_resource=True,
                          client_registration_options=ClientRegistrationOptions(
                              enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE])),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[host, host + ':443'], allowed_origins=[provider.origin]))
    app = mcp.streamable_http_app()
    async def health(_request):
        return JSONResponse({'status': 'ok', 'transport': 'streamable-http', 'authentication': 'oauth'})
    app.routes.insert(0, Route('/login', provider.login, methods=['GET', 'POST']))
    app.routes.insert(0, Route('/health', health, methods=['GET']))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host])
    app.add_middleware(RequestLimits, resource=provider.resource)
    return app
