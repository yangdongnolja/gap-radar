# 무료 서버 이전 (2026-10-27)

## 배포 완료 및 실제 사용

- 서버 깨우기/준비상태: https://gap-radar-mcp-free.onrender.com/ready
- Render 서비스: https://dashboard.render.com/web/srv-dbg9itvlk1mc73b4eoug
- Git 브랜치 `codex/mcp-free-server`, 실행 코드 커밋 `c5b115f`.
- Render Live / Free ($0 월 인스턴스) 확인. 유료 업그레이드는 하지 않았다.
- 기존 ChatGPT GAP-RADAR 실거래 도구를 그대로 사용한다. 새 플러그인 등록은 필요 없다.
- 기존 로컬 터널을 종료하고 실행 개수 0 확인 후, 연결된 MCP 도구로 실제 조회 성공.
  노원6월 전체680건; 가격6~8억/면적59~85㎡ 조건6월135건·7월145건;
  분석134그룹 및 월별 합계 일치. 같은 시점 직접 REST 결과와 일치했다.
- `/health` 200, `/ready` 200/tunnel_ready=true;
  공개 `/mcp`, `/ui`, `/.env` 모두404. 기존 REST 미인증401, Streamlit HTTP200.
- 회귀23개+추가 공개경로 검사1개 통과. Python 구문 검사 통과.
- 데이터가 없는 월의 집계값은 null일 수 있으므로 합계 검사에서는0으로 처리했다.
  첫 수동 검사 스크립트의 null 합산 오류는 검사식 수정 후 재검증했다. 운영 API는 변경하지 않았다.
- 15분 유휴 후 실제 기동 시간은 이번 작업에서 대기 측정하지 않았다.
- 키 복사: 시스템 클립보드 방식은 보안 자동검사에서 차단되어 실행하지 않았다.
  사용자 승인 후 Render의 기존 키를 브라우저 메모리에서 새 서비스 필드로 직접 전달했다.
  키 값은 채팅·로그·Git·시스템 클립보드에 출력/기록하지 않았다.

사용 순서: 위 준비상태 URL 열기 → tunnel_ready=true 확인 → 기존 ChatGPT에서 질의.
컴퓨터/검은 창은 필요 없다. 무료 유휴 중단 때문에 사용 중 재기동이 필요할 수 있다.

## 선택한 방식

기존 ChatGPT의 GAP-RADAR 실거래 도구와 Tunnel ID를 유지하고,
터널 클라이언트와 기존 stdio MCP를 별도 Render Free Web Service로 옮긴다.
`app.py`, `api/main.py`, 기존 `render.yaml`은 변경하지 않는다.
기존 API가 배포된 서비스도 변경하지 않는다.

무료 제약: 15분 동안 외부 접속이 없으면 Render가 서비스를 중단한다.
ChatGPT 호출은 OpenAI 터널로 가므로 잠든 Render 서비스를 직접 깨우지 못한다.
따라서 사용 전 새 서비스 URL을 브라우저에서 열어 서버를 깨우고 약 1분 기다린다.
사용 중에도 다시 잠들 수 있다. 자동 새로고침/무료 정책 우회는 추가하지 않았다.
컴퓨터는 꺼도 되지만 무료 상시 접속을 보장하는 구성은 아니다.
기존 REST도 무료라면 별도로 시작 지연이 있을 수 있다.

## Render 입력값

GitHub 배포 전용 브랜치: `codex/mcp-free-server` (승인 후 생성/커밋/push)

- 유형: Web Service
- 이름: `gap-radar-mcp-free`
- Runtime: Python 3
- Root Directory: 공란 (저장소 루트)
- Branch: 위 배포 전용 브랜치
- Build Command:
  `pip install -r plugins/gpt-18c748cd216b10824cc670dd93d6903d/server/requirements.txt && python tools/install_server_tunnel.py`
- Start Command: `python tools/run_server_tunnel.py`
- Instance: Free
- Health Check Path: `/health`
- Auto Deploy: Off
- Python: 환경변수 `PYTHON_VERSION=3.12.14`

별도 Blueprint 후보는 `deploy/render-mcp-free.yaml`이다.
기존 Blueprint/render.yaml을 대체하지 않는다.

환경변수는 Render 대시보드에 직접 입력한다:

| 이름 | 입력할 내용 |
|---|---|
| CONTROL_PLANE_TUNNEL_ID | 지금 ChatGPT에 등록한 기존 Tunnel ID |
| CONTROL_PLANE_API_KEY | 같은 조직의 Tunnels Read/Use 런타임 키 |
| GAP_RADAR_API_KEY | 기존 REST 운영 키 그대로 |

키는 채팅, Git, 명령행, ZIP에 기록하지 않는다. 국토부 키는 이 서버에 필요 없다.
Windows `.env`나 `.state` 프로필도 업로드하지 않는다.

## 서버가 하는 일

`tools/install_server_tunnel.py`는 v0.0.16 공식 Linux 실행파일을 SHA256 검증해 설치한다.
`tools/run_server_tunnel.py`는 기존 MCP 실행기를 stdio로 연결한다.
비밀값 대신 환경변수 참조를 CLI에 전달한다.
공개 경로는 `/`, `/health`, `/ready`뿐이며 상태 boolean만 반환한다.
MCP 관리 UI는 127.0.0.1에만 바인딩한다. 공개 `/mcp`는 없다.
`/health` 성공은 프로세스 실행 확인이며 실제 ChatGPT 호출 성공을 뜻하지 않는다.

## 전환 및 검증 순서

1. 로컬 회귀 검사와 배포 파일 검토.
2. 승인된 파일만 새 브랜치에 커밋/push. master는 유지해서 기존 자동배포를 피한다.
3. 무료 서비스 생성, 위 환경변수 입력. API 키를 배포 로그에 출력하지 않는다.
4. 실제 서비스 URL 확인. 서버 시작이 확인될 때 로컬 검은 창은 Ctrl+C로 종료한다.
   같은 Tunnel ID의 두 클라이언트를 장시간 동시에 실행하지 않는다.
5. 새 서비스 URL에서 `/ready` 확인 후 기존 ChatGPT 대화에서 실제 조회한다.
6. 컴퓨터의 로컬 터널을 끈 상태에서 get_transactions, get_complex_analysis를 호출한다.
7. 직접 REST와 같은 조건으로 전체 건수 및 집계 합계를 비교한다.
8. 무료 서버가 잠든 뒤 URL로 깨워 다시 호출하는 절차를 검증한다.

실패하면 새 서비스만 중단하고 기존 ChatGPT연결.bat을 다시 실행하면 된다.
기존 Streamlit과 REST는 그대로다. 서버 배포 전에는 검은 창을 종료하지 않는다.

## 검증 범위

Linux 실행 파일을 실제 Render에서 실행해 연결 도구 호출까지 확인했다.
Windows 로컬 터널 종료 후 Codex의 연결된 GAP-RADAR MCP를 통해 검증했으며,
사용자가 별도의 ChatGPT 웹 대화에서 전환 후 재호출하는 확인은 추가로 할 수 있다.
컴퓨터 자체를 끄고 이 작업을 수행한 것은 아니다. 로컬 터널 미실행으로 의존성을 분리했다.

공식 근거:
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://render.com/docs/free
