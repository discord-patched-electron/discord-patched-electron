"""Opt-in Equicord installation and a user-owned loader outside package files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stream.write(data)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def install(home: Path):
    request = urllib.request.Request('https://api.github.com/repos/Equicord/Equicord/releases/latest',
                                     headers={'User-Agent': 'discord-patched-electron'})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset, = [a for a in release['assets'] if a['name'] == 'desktop.asar']
    checksum = asset.get('digest', '')
    if not checksum.startswith('sha256:') or len(checksum) != 71:
        raise ValueError('Official release does not supply a SHA-256 digest')
    url = asset['browser_download_url']
    if not url.startswith('https://github.com/Equicord/Equicord/releases/download/'):
        raise ValueError('Unexpected Equicord release URL')
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != checksum[7:] or len(data) != asset['size']:
        raise ValueError('Equicord asset changed or failed checksum verification; retry discovery')
    atomic(home / 'desktop.asar', data)
    atomic(home / 'install.json', json.dumps({'repository': 'Equicord/Equicord',
        'release_id': release['id'], 'asset_id': asset['id'], 'url': url,
        'sha256': checksum[7:], 'upstream_updates_supported': True}, indent=2).encode())
    atomic(home / 'enabled', b'1\n')
    print(home / 'desktop.asar')


def prepare(home: Path, root: Path) -> Path:
    """Separate loaders per installation; updating a package cannot erase the mod."""
    root, home = root.resolve(), home.resolve()
    bundle = home / 'desktop.asar'
    original = root / 'resources/app.asar'
    if not bundle.is_file() or not original.is_file():
        raise ValueError('Missing Equicord bundle or Discord bootstrap')
    identity = hashlib.sha256(str(root).encode()).hexdigest()[:24]
    loader = home / 'loaders' / identity
    app = loader / 'app'
    atomic(app / 'package.json', b'{"name":"discord","main":"index.js"}\n')
    # Electron's default app may import the entry without making it the CJS
    # main module. Equicord uses require.main to locate the sibling bootstrap.
    atomic(app / 'index.js', ('process.mainModule = module; require.main = module;\nrequire('
        + json.dumps(str(bundle)) + ');\n').encode())
    copy = loader / '_app.asar'
    # Electron 46's ASAR realpath wrapper rejects archive-file symlinks. Copy
    # this small prepared bootstrap atomically; refresh it on package updates.
    if copy.is_symlink():
        copy.unlink()
    atomic(copy, original.read_bytes())
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['install', 'prepare'])
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    if args.action == 'install':
        install(args.home.expanduser())
    else:
        if args.root is None:
            parser.error('--root is required for prepare')
        print(prepare(args.home, args.root))
