"""Adapt the pinned desktop module's storage IPC to modern Electron APIs."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from common import sha256

BEFORE = (
    "electron.ipcMain.on(SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE,event=>{event.returnValue=null!=electron.safeStorage&&electron.safeStorage.isEncryptionAvailable()}),"
    'electron.ipcMain.on(SAFE_STORAGE_ENCRYPT_STRING,(event,plainText)=>{null!=plainText?event.returnValue=electron.safeStorage.encryptString(plainText).toString("base64"):event.returnValue=null}),'
    'electron.ipcMain.on(SAFE_STORAGE_DECRYPT_STRING,(event,encrypted)=>{if(null!=encrypted)try{event.returnValue=electron.safeStorage.decryptString(Buffer.from(encrypted,"base64"))}catch{event.returnValue=null}else event.returnValue=null})'
)
AFTER = 'require("./dpe-safe-storage.cjs").install(electron,{SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE,SAFE_STORAGE_ENCRYPT_STRING,SAFE_STORAGE_DECRYPT_STRING})'


def patch(directory: Path, adapter: Path, asar: str) -> dict:
    archive = directory / "core.asar"
    before = sha256(archive)
    with tempfile.TemporaryDirectory(prefix="desktop-", dir=directory) as work:
        extracted = Path(work) / "core"
        subprocess.run([asar, "extract", str(archive), str(extracted)], check=True)
        bundle = extracted / "bundle.js"
        source = bundle.read_text()
        if source.count(BEFORE) == 1 and AFTER not in source:
            bundle.write_text(source.replace(BEFORE, AFTER))
        elif BEFORE in source or source.count(AFTER) != 1:
            raise ValueError("Unknown desktop safeStorage IPC layout")
        shutil.copy2(adapter, extracted / "dpe-safe-storage.cjs")
        result = Path(work) / "core.asar"
        subprocess.run([asar, "pack", str(extracted), str(result)], check=True)
        result.replace(archive)
    return {
        "before_sha256": before,
        "after_sha256": sha256(archive),
        "adapter_sha256": sha256(adapter),
        "plaintext_fallback": False,
    }
