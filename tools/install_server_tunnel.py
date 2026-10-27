"""Install the pinned official Linux client; never download secrets."""
import hashlib
import io
from pathlib import Path, PurePosixPath
import platform
import stat
import zipfile

import httpx

VERSION = 'v0.0.16'
ROOT = Path(__file__).resolve().parents[1]


def install():
    arch = {'x86_64': 'amd64', 'AMD64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if not arch:
        raise RuntimeError('Unsupported architecture')
    name = f'tunnel-client-{VERSION}-linux-{arch}.zip'
    base = f'https://github.com/openai/tunnel-client/releases/download/{VERSION}/'
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        checks = client.get(base + 'SHA256SUMS.txt')
        checks.raise_for_status()
        digest = next((line.split()[0] for line in checks.text.splitlines()
                       if len(line.split()) == 2 and line.split()[1].lstrip('*') == name), None)
        response = client.get(base + name)
        response.raise_for_status()
    if not digest or hashlib.sha256(response.content).hexdigest() != digest:
        raise RuntimeError('Official checksum verification failed')
    destination = ROOT / '.local' / 'server-tunnel'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        candidates = [entry for entry in archive.infolist()
                      if PurePosixPath(entry.filename).name == 'tunnel-client' and not entry.is_dir()]
        if len(candidates) != 1:
            raise RuntimeError('Expected exactly one executable')
        entry = candidates[0]
        if entry.file_size > 256 * 1024 * 1024 or stat.S_ISLNK(entry.external_attr >> 16):
            raise RuntimeError('Unsafe executable entry')
        destination.mkdir(parents=True, exist_ok=True)
        executable = destination / 'tunnel-client'
        executable.write_bytes(archive.read(entry))
        executable.chmod(0o755)
    print('Official Linux tunnel-client installed; checksum verified:', VERSION)


if __name__ == '__main__':
    install()
