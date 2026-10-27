"""Render start command: python tools/run_https_mcp.py. No tunnel runtime."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/gpt-18c748cd216b10824cc670dd93d6903d/server'))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('https_server:create_app', factory=True, host='0.0.0.0',
                port=int(os.environ.get('PORT', '8002')), access_log=False,
                proxy_headers=False)
