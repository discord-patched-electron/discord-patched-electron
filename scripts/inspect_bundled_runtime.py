"""Identify Discord's shipped Electron bytes without running the downloaded app."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile

from common import download, digest


def inspect_archive(archive: Path, channel: str) -> dict:
    executable = {'stable':'Discord', 'ptb':'DiscordPTB', 'canary':'DiscordCanary'}[channel]
    hashes, versions = {}, set()
    # Only read regular members; never extract archive-controlled paths.
    process = subprocess.Popen(['brotli', '--decompress', '--stdout', str(archive)], stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as tar:
            for member in tar:
                name = member.name.removeprefix('./')
                if not member.isfile() or not name.startswith('files/'):
                    continue
                name = name.removeprefix('files/')
                if name != executable and not (name.endswith(('.pak', '.bin', '.so', '.dat'))
                    or name in ('chrome-sandbox', 'chrome_crashpad_handler', 'libvulkan.so.1')):
                    continue
                source = tar.extractfile(member)
                checksum, tail = hashlib.sha256(), b''
                while chunk := source.read(1024 * 1024):
                    checksum.update(chunk)
                    if name == executable:
                        versions.update(m.decode() for m in re.findall(
                            rb'Electron/(\d+\.\d+\.\d+(?:-[a-z]+\.\d+)?)\x00', tail + chunk))
                        tail = chunk[-128:]
                # Branding names differ between channels; normalize the main binary.
                hashes['executable' if name == executable else name] = checksum.hexdigest()
        if process.wait(timeout=10) != 0 or 'executable' not in hashes:
            raise ValueError('Incomplete Discord runtime archive')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    return {'executable_sha256': hashes['executable'], 'payload_sha256': digest(hashes),
            'files': hashes, 'embedded_electron_version': next(iter(versions)) if len(versions) == 1 else None}


def inspect_clients(state: dict, output: Path):
    observations = state.setdefault('bundled_runtimes', {})
    errors = {}
    for channel, client in state['clients'].items():
        core = client['sources']['core']
        old = observations.get(channel)
        if old and old['core_sha256'] == core['sha256']:
            continue
        try:
            archive = download(core['url'], output / (core['sha256'] + '.tar.br'), core['sha256'])
            result = inspect_archive(archive, channel)
            changed = bool(old and old['payload_sha256'] != result['payload_sha256'])
            observations[channel] = {**result, 'core_sha256': core['sha256'],
                'client_version': client['version'], 'changed_since_previous_core': changed,
                'previous_payload_sha256': old['payload_sha256'] if old else None}
        except (ValueError, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
            errors[channel] = str(error)
    state['bundled_runtime_errors'] = errors
