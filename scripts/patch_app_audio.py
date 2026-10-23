"""Install an opt-in module adapter; reject an unknown native wrapper layout."""
from pathlib import Path
import shutil

from common import sha256

ANCHOR = 'function bindConnectionInstance(instance) {\n'
INSERT = "    instance = require('./dpe-app-audio.cjs').wrap(instance);\n"


def patch(directory: Path, adapter: Path) -> dict:
    wrapper = directory / 'index.js'
    before = sha256(wrapper)
    source = wrapper.read_text()
    if source.count(ANCHOR) != 1:
        raise ValueError('Unknown voice connection wrapper; application audio adapter not installed')
    if INSERT not in source:
        source = source.replace(ANCHOR, ANCHOR + INSERT, 1)
        wrapper.write_text(source)
    elif source.count(ANCHOR + INSERT) != 1:
        raise ValueError('Unexpected existing application audio adapter')
    shutil.copy2(adapter, directory / 'dpe-app-audio.cjs')
    return {'input_sha256': before, 'output_sha256': sha256(wrapper),
            'adapter_sha256': sha256(adapter), 'enabled_by_default': False,
            'requires_environment': 'DPE_APP_AUDIO=1',
            'trace_requires_environment': 'DPE_APP_AUDIO_TRACE=1', 'publishable': False}
