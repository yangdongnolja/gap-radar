"""GAP-RADAR 로컬 MCP 서버. 기존 REST/Streamlit을 import하거나 수정하지 않는다."""
from __future__ import annotations

import argparse
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field
from adapter import RestAdapter, load_settings

load_settings()


def create_server(adapter: RestAdapter | None = None) -> FastMCP:
    upstream = adapter or RestAdapter()
    mcp = FastMCP('GAP-RADAR', host='127.0.0.1', port=8001,
                  stateless_http=True, json_response=True,
                  instructions='서울 실거래 읽기 전용 조회. 가격 단위 만원, 면적 ㎡. 집계는 get_complex_analysis, 개별 검증은 get_transactions. truncated=true이면 부분 결과다.')
    annotation = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @mcp.tool(name='get_transactions', annotations=annotation,
              description='서울 아파트 한 달의 매매 실거래 조회. 가격 만원(6억=60000), 면적 전용㎡. gu 생략은 서울 전체로 느릴 수 있음. year/month 중 하나라도 생략하면 기존 API가 지난달로 처리. limit 이전 count와 returned_count/truncated를 보존. 최신 계약일 순 반환.')
    async def get_transactions(
        sido: str | None = '서울특별시', gu: str | None = None,
        year: int | None = None,
        month: Annotated[int | None, Field(ge=1, le=12)] = None,
        min_price: Annotated[int | None, Field(ge=0)] = None,
        max_price: Annotated[int | None, Field(ge=0)] = None,
        min_area: Annotated[float | None, Field(ge=0)] = None,
        max_area: Annotated[float | None, Field(ge=0)] = None,
        complex_name: str | None = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 200,
    ) -> CallToolResult:
        return await upstream.call('get_transactions', locals())

    @mcp.tool(name='get_complex_analysis', annotations=annotation,
              description='서울 아파트 기간별 단지·법정동·반올림 전용면적 통계. YYYY-MM 시작/종료월, 최대 3개월. 원시 거래 limit을 적용하지 않은 전체 집계. 가격 만원, 면적㎡. 첫 달/마지막 달 비교, 소표본 및 필터 편향 주의. 기존 API를 그대로 호출한다.')
    async def get_complex_analysis(
        from_year_month: str, to_year_month: str,
        sido: str | None = '서울특별시', gu: str | None = None,
        min_price: Annotated[int | None, Field(ge=0)] = None,
        max_price: Annotated[int | None, Field(ge=0)] = None,
        min_area: Annotated[float | None, Field(ge=0)] = None,
        max_area: Annotated[float | None, Field(ge=0)] = None,
        min_transactions: Annotated[int, Field(ge=1)] = 1,
    ) -> CallToolResult:
        return await upstream.call('get_complex_analysis', locals())

    return mcp


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--transport', choices=('stdio','streamable-http'), default='stdio')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    # HTTP는 로컬 테스트/인증된 공식 터널용으로만 루프백에 바인딩한다.
    # 공개 인증 서버(OAuth)가 없는 상태로 0.0.0.0에 노출하지 않는다.
    server = create_server()
    server.settings.port = args.port
    server.run(transport=args.transport)
