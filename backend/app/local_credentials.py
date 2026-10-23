"""Current-user DPAPI credential records; complete references are immutable."""
import ctypes
from ctypes import wintypes
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Literal
from uuid import uuid4

from pydantic import SecretStr
from .credentials import Credential, CredentialStore, credential_origin


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool) -> bytes:
    if sys.platform != 'win32':
        raise RuntimeError('本机凭据仅支持 Windows DPAPI')
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN, without LOCAL_MACHINE: protection belongs to the current user.
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError('Windows DPAPI operation failed')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def protect(data: bytes) -> bytes:
    return _crypt(data, False)


def unprotect(data: bytes) -> bytes:
    return _crypt(data, True)


class LocalCredentialStore:
    def __init__(self, path: Path, legacy: CredentialStore):
        self.path = Path(path)
        self.legacy = legacy

    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS local_credentials(id TEXT PRIMARY KEY, origin TEXT NOT NULL, payload BLOB NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, field_mask INTEGER NOT NULL DEFAULT 0)')
        if 'field_mask' not in {row[1] for row in db.execute('PRAGMA table_info(local_credentials)')}:
            db.execute('ALTER TABLE local_credentials ADD COLUMN field_mask INTEGER NOT NULL DEFAULT 0')
        db.commit()
        return db

    def _rows(self):
        if not self.path.exists():
            return []
        with closing(self._connection()) as db:
            return db.execute('SELECT * FROM local_credentials ORDER BY rowid').fetchall()

    def _decode(self, row):
        try:
            data = json.loads(unprotect(row['payload']))
            if (data['version'] != 1 or data['origin'] != row['origin'] or
                    data['scope'] != 'windows-current-user' or
                    not isinstance(data['fields'], dict) or
                    not set(data['fields']) <= {'username', 'password'} or
                    any(not isinstance(v, str) or not v for v in data['fields'].values())):
                raise ValueError()
            return data['fields']
        except Exception:
            raise ValueError('本机凭据无法解密，请重新录入') from None

    def metadata(self):
        # Metadata never requires decryption, allowing authenticated-session reuse.
        return self.legacy.metadata() + [
            {'name': row['id'], 'origin': row['origin'], 'ready': row['field_mask'] == 3}
            for row in self._rows()]

    def resolve(self, name, scope):
        row = next((row for row in self._rows() if row['id'] == name), None)
        if row is None:
            return self.legacy.resolve(name, scope)
        if row['origin'] != credential_origin(scope):
            raise ValueError('凭据允许来源与流程不一致')
        fields = self._decode(row)
        if set(fields) != {'username', 'password'}:
            raise ValueError('凭据字段不完整，请完成账号密码录入')
        return Credential(origin=row['origin'], **fields)

    def secrets(self):
        values = self.legacy.secrets()
        for row in self._rows():
            try:
                values.extend(SecretStr(v) for v in self._decode(row).values())
            except ValueError:
                # Unavailable DPAPI rows cannot reveal plaintext; resolving still fails.
                continue
        return values

    def save_field(self, ref: str | None, scope: str, field: Literal['username', 'password'], value: str) -> str:
        if field not in ('username', 'password') or not isinstance(value, str) or not value:
            raise ValueError('凭据字段无效')
        origin = credential_origin(scope)
        try:
            with closing(self._connection()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM local_credentials WHERE id=?', (ref,)).fetchone() if ref else None
                if ref and row is None:
                    raise ValueError('凭据引用不存在，请检查本机配置')
                if row and row['origin'] != origin:
                    raise ValueError('凭据允许来源与流程不一致')
                fields = self._decode(row) if row else {}
                identity = ref if row and set(fields) != {'username', 'password'} else uuid4().hex
                fields[field] = value
                try:
                    payload = protect(json.dumps({'version': 1, 'scope': 'windows-current-user', 'origin': origin, 'fields': fields}, ensure_ascii=False).encode('utf-8'))
                except Exception:
                    raise ValueError('本机凭据无法保存，请检查本机安全存储') from None
                now = datetime.now(timezone.utc).isoformat()
                db.execute('INSERT INTO local_credentials(id,origin,payload,created_at,updated_at,field_mask) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at, field_mask=excluded.field_mask',
                           (identity, origin, payload, now, now, (1 if 'username' in fields else 0) | (2 if 'password' in fields else 0)))
                return identity
        except ValueError:
            raise
        except Exception:
            raise ValueError('本机凭据无法保存，请检查本机安全存储') from None
