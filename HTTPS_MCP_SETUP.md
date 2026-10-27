# ChatGPT HTTPS 직접 연결 준비

## 변경 범위

기존 두 MCP 도구와 RestAdapter를 재사용한다. create_server에 선택적인 인증
설정 인자만 추가하여 stdio·기존 터널 동작은 유지한다. Streamlit, REST API,
국토부 파서, 기존 Render 서비스와 기존 API 키는 변경하지 않는다.

별도 무료 HTTPS MCP 서비스 후보는 deploy/render-mcp-https.yaml이다.
빌드: `pip install -r plugins/gpt-18c748cd216b10824cc670dd93d6903d/server/requirements-https.txt`
시작: `python tools/run_https_mcp.py`, Root Directory 공란.
기존 터널 서비스는 새 연결의 실제 도구 호출 검증 전까지 유지한다.

## 인증

OpenAI 공식 MCP SDK의 OAuth 처리기를 사용하며, 단일 소유자용 로그인
및 토큰 제공자를 https_auth.py에 추가했다.

- 인증 없는 /mcp 호출은401이고 기존 REST에 접근하지 않는다.
- OAuth authorization code + PKCE S256, CSRF cookie, 짧은 수명의1회용코드.
- 콜백은 공식 ChatGPT callback 경로만 허용한다. 로그인 시 조회 권한을 승인한다.
- 서명된 클라이언트 등록과 토큰은 재시작 후에도 유지된다. 별도 DB가 없다.
- 접근 토큰1시간. 비밀키를 갖는 OAuth 클라이언트는30일 갱신 토큰 사용.
  공개 클라이언트는 갱신 토큰을 발급하지 않아 만료 후 재연결이 필요하다.
- 갱신 토큰 재사용 차단 목록은 메모리이므로 재시작 후 초기화된다.
  갱신에는 해당 OAuth client secret도 필요하다. 완전한 영구 토큰 폐기와
  다중사용자 인증은 이번 범위가 아니다. 전체 연결 폐기에는 전용 서명키를
  교체해야 하며 이는 별도 승인 대상이다.
- 전용 암호는 scrypt 해시만 서버에 저장한다. 원래 GAP_RADAR_API_KEY는
  내부 REST 요청 헤더로만 사용하고 클라이언트 토큰에 넣지 않는다.
- 관리UI/환경파일을 공개하지 않는다. Uvicorn access log를 끄므로
  인증 URL의 코드·state·로그인 요청ID를 요청 로그에 기록하지 않는다.

## Render 환경변수

| 변수 | 내용 |
|---|---|
| PYTHON_VERSION | 3.12.14 |
| GAP_RADAR_API_KEY | 기존 운영 REST 키 그대로 |
| MCP_PUBLIC_URL | 생성 후 확인된 실제 서비스의 HTTPS origin (경로 없음) |
| MCP_TOKEN_SECRET | 새 전용 랜덤 서명키, 기존 키와 별개 |
| MCP_LOGIN_PASSWORD_HASH | 전용 연결 암호의 scrypt 해시 |

`python tools/prepare_https_secrets.py`는 승인 후 사용할 준비 도구다.
비밀값은 stdout에 출력하지 않고 Git 제외 .state/https-mcp 안에 저장한다.
render.env에는 새 서명키/암호 해시만 있으며 기존 키나 국토부 키는 없다.
connection-password.txt는 최초 연결할 때 소유자가 직접 입력할 암호다.
기존 .env를 변경하거나 ZIP/Git에 비밀값을 추가하지 않는다.

## ChatGPT 설정 (서버 URL 확인 후)

플러그인 추가 → 사용자 지정 MCP 서버 → 서버 URL → 실제URL/mcp → OAuth.
먼저 자동 OAuth/DCR을 사용하고 로그인 화면에서 전용 암호로 승인한다.
기존 플러그인의 callback URL은 직접 관찰해 허용 경로와 일치하는지 확인한다.
개인 계정 등록·로그인·동의는 실제 계정에서 추가 검증해야 한다.
새 도구 호출이 확인된 뒤 기존 터널 도구와 중복 활성화하지 않는다.
기존 자산플랜 스킬은 그대로 함께 사용할 수 있다.

## 자동 깨우기의 한계

HTTPS 요청은 Render를 직접 깨우지만 첫 요청의 대기시간까지 제거하지는 않는다.
별도 REST 서버도 무료여서 시작 지연이 겹치면 첫 호출은 재시도가 필요할 수 있다.
상시 ping·무료 정책 우회·유료 전환은 추가하지 않는다. 링크를 수동으로
열지 않고 호출이 시작되는 구조를 만들되, 무조건 첫 호출 성공으로 보고하지 않는다.

## 검증 기준

- 로컬 OAuth 로그인·PKCE·인증 오류·resource/scope·재시작 후 갱신 테스트.
- 두 도구 발견 및 JSON의 count/truncated/만원 단위 유지.
- 실제 REST와 HTTPS MCP 결과 대조.
- 배포 후 공개 HTTPS metadata와401 확인.
- ChatGPT에서 실제 로그인 후 도구 호출까지 확인하기 전에는 설치 완료로 표시하지 않는다.

공식 문서:
- https://developers.openai.com/plugins/build/auth
- https://developers.openai.com/plugins/deploy/connect-chatgpt
