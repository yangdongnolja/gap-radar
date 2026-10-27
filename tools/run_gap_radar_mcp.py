"""터널이 실행할 기존 MCP 진입점. 불필요한 OpenAI 제어 키를 제거한다."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT/'plugins'/'gpt-18c748cd216b10824cc670dd93d6903d'/'server'/'server.py'


def prepare_environment():
    for name in ('CONTROL_PLANE_API_KEY','OPENAI_API_KEY','OPENAI_ADMIN_KEY'):
        os.environ.pop(name,None)
    os.environ.setdefault('GAP_RADAR_ENV_FILE',str(ROOT/'.env'))
    os.environ['GAP_RADAR_API_BASE_URL']='https://gap-radar-api-vbg8.onrender.com'


if __name__ == '__main__':
    prepare_environment()
    sys.path.insert(0,str(SERVER.parent))
    sys.argv = [str(SERVER)]  # 터널용 stdio만 실행, HTTP 옵션은 전달하지 않는다.
    runpy.run_path(str(SERVER),run_name='__main__')
