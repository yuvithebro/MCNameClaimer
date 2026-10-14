"""Encrypted credential storage using Windows DPAPI (CryptProtectData).

DPAPI encrypts with a key tied to the signed-in Windows user, so the file is
useless if copied to another account or machine, and no master password is
needed. On other platforms credentials are kept in memory only (you sign in
again each launch); they are never written to disk in plain text.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from .logging_setup import LOGGER_NAME

logger = logging.getLogger(f"{LOGGER_NAME}.store")

# Extra secret mixed into the encryption so other apps running as the same
# user can't decrypt the blob by calling CryptUnprotectData without it.
_ENTROPY = b"MCNameClaimer/credentials/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01
DPAPI_AVAILABLE = sys.platform == "win32"

if DPAPI_AVAILABLE:
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _to_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        # The buffer is returned too: it must stay referenced while DPAPI reads it.
        buffer = ctypes.create_string_buffer(data, len(data))
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        return blob, buffer

    def _take_output(blob: _DataBlob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def dpapi_encrypt(plaintext: bytes) -> bytes:
        data_blob, _data_buffer = _to_blob(plaintext)
        entropy_blob, _entropy_buffer = _to_blob(_ENTROPY)
        output = _DataBlob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(data_blob),
            "MCNameClaimer credentials",
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "CryptProtectData failed")
        return _take_output(output)

    def dpapi_decrypt(ciphertext: bytes) -> bytes:
        data_blob, _data_buffer = _to_blob(ciphertext)
        entropy_blob, _entropy_buffer = _to_blob(_ENTROPY)
        output = _DataBlob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(data_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")
        return _take_output(output)


class SecureStore:
    """Saves one small JSON document of credentials, encrypted at rest."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._memory: dict | None = None

    @property
    def persists_to_disk(self) -> bool:
        return DPAPI_AVAILABLE

    def save(self, data: dict) -> None:
        if not DPAPI_AVAILABLE:
            self._memory = dict(data)
            return
        ciphertext = dpapi_encrypt(json.dumps(data).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(ciphertext)
        os.replace(temporary, self.path)

    def load(self) -> dict | None:
        if not DPAPI_AVAILABLE:
            return dict(self._memory) if self._memory else None
        try:
            ciphertext = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Could not read saved credentials (%s); please sign in again.", type(exc).__name__)
            return None
        try:
            data = json.loads(dpapi_decrypt(ciphertext).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            logger.warning("Saved credentials could not be decrypted; please sign in again.")
            return None
        return data if isinstance(data, dict) else None

    def clear(self) -> None:
        self._memory = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
