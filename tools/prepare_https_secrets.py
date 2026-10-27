"""Generate NEW dedicated OAuth credentials to ignored files, never stdout."""
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/gpt-18c748cd216b10824cc670dd93d6903d/server'))
from https_auth import password_hash


def prepare():
    folder = ROOT / '.state' / 'https-mcp'
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / 'render.env'
    password_file = folder / 'connection-password.txt'
    if config.exists() or password_file.exists():
        raise RuntimeError('Existing setup files were preserved; not overwritten')
    password = secrets.token_urlsafe(24)
    # Deliberately excludes the existing upstream key and all OpenAI keys.
    with password_file.open('x', encoding='utf-8') as f:
        f.write(password)
    with config.open('x', encoding='utf-8') as f:
        f.write('MCP_TOKEN_SECRET=' + secrets.token_urlsafe(64) + '\n')
        f.write('MCP_LOGIN_PASSWORD_HASH=' + password_hash(password) + '\n')
    print('Dedicated OAuth configuration prepared in ignored .state/https-mcp/. Values hidden.')


if __name__ == '__main__':
    prepare()
