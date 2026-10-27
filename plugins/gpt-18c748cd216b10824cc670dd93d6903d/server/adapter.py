"""기존 REST를 호출하는 읽기 전용 어댑터. 집계는 REST 서버만 수행한다."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from mcp.types import CallToolResult, TextContent
import json

PRODUCTION_URL = 'https://gap-radar-api-vbg8.onrender.com'
ENDPOINTS = {'get_transactions': '/api/v1/transactions',
             'get_complex_analysis': '/api/v1/complex-analysis'}
PARAMETERS = {
    'get_transactions': {'sido','gu','year','month','min_price','max_price','min_area','max_area','complex_name','limit'},
    'get_complex_analysis': {'sido','gu','from_year_month','to_year_month','min_price','max_price','min_area','max_area','min_transactions'},
}


def load_settings():
    explicit = os.getenv('GAP_RADAR_ENV_FILE')
    if explicit:
        load_dotenv(explicit, override=False)
    else:
        # 저장소 개발 실행에서만 프로젝트 루트 .env를 읽는다. 설치 캐시에는 복사하지 않는다.
        candidate = Path(__file__).resolve().parents[3] / '.env'
        if (candidate.parent / 'api' / 'main.py').exists():
            load_dotenv(candidate, override=False)


def failure(status: int, code: str, message: str) -> CallToolResult:
    # HTTP 상태 코드를 MCP 오류 결과에 명시; 성공 count=0으로 오인하지 않도록 isError 사용.
    data = {'status_code': status, 'detail': {'error': code, 'message': message}}
    return CallToolResult(isError=True, content=[TextContent(type='text', text=json.dumps(data, ensure_ascii=False))], structuredContent=data)


class RestAdapter:
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = (base_url or os.getenv('GAP_RADAR_API_BASE_URL', PRODUCTION_URL)).rstrip('/')
        parsed = urlsplit(self.base_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('API 주소에는 인증정보·쿼리·경로를 넣을 수 없습니다.')
        if not (parsed.scheme == 'https' or
                (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('HTTPS 또는 로컬 루프백 주소만 지원합니다.')
        self.api_key = api_key if api_key is not None else os.getenv('GAP_RADAR_API_KEY', '').strip()
        self.transport = transport

    async def call(self, tool_name: str, parameters: dict) -> CallToolResult:
        if tool_name not in ENDPOINTS:
            return failure(400, 'unknown_tool', '지원하지 않는 도구입니다.')
        if not self.api_key:
            return failure(503, 'server_misconfigured', 'GAP_RADAR_API_KEY 환경변수가 필요합니다.')
        params = {k: v for k, v in parameters.items() if k in PARAMETERS[tool_name] and v is not None}
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(120, connect=15),
                                         follow_redirects=False) as client:
                response = await client.get(self.base_url + ENDPOINTS[tool_name], params=params,
                                            headers={'X-API-Key': self.api_key})
        except httpx.TimeoutException:
            return failure(502, 'upstream_timeout', 'REST API 응답 시간이 초과되었습니다. 잠시 후 다시 조회하세요.')
        except httpx.HTTPError:
            return failure(502, 'upstream_unavailable', 'REST API에 연결하지 못했습니다.')
        try:
            data = response.json()
        except ValueError:
            return failure(502, 'invalid_upstream_response', 'REST API가 JSON을 반환하지 않았습니다.')
        if self.api_key in json.dumps(data, ensure_ascii=False):
            return failure(502, 'unsafe_upstream_response', 'REST API의 안전하지 않은 응답을 차단했습니다.')
        if response.status_code != 200:
            if response.status_code in (400,401,422,500,502,503) and isinstance(data,dict) and 'detail' in data:
                body = {'status_code': response.status_code, **data}
                return CallToolResult(isError=True, content=[TextContent(type='text',text=json.dumps(body,ensure_ascii=False))], structuredContent=body)
            return failure(502, 'upstream_http_error', f'REST API 요청 실패 (HTTP {response.status_code}).')
        if not isinstance(data,dict) or not isinstance(data.get('results'),list) or not isinstance(data.get('count'),int):
            return failure(502, 'invalid_upstream_response', 'REST 응답 구조가 올바르지 않습니다.')
        # 재집계·반올림·잘림 없이 전체 응답 보존. 원래 필터/단위/월별 통계를 유지한다.
        return CallToolResult(isError=False, content=[TextContent(type='text',text=json.dumps(data,ensure_ascii=False))], structuredContent=data)
