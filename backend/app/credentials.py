"""Local-only credential parsing. No API or workflow serializes resolved values."""
import json
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr


def secret_variants(raw):
    variants = {raw}
    for _ in range(3):
        variants |= {json.dumps(item, ensure_ascii=ascii_only)[1:-1]
                     for item in tuple(variants) for ascii_only in (False, True)}
    return variants


def credential_origin(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username is not None or p.password is not None:
        raise ValueError('凭据来源必须是无账号密码的 HTTP(S) 地址')
    host = f'[{p.hostname}]' if ':' in p.hostname else p.hostname
    return f'{p.scheme}://{host}:{p.port or (443 if p.scheme == "https" else 80)}'


class Credential(BaseModel):
    model_config = ConfigDict(extra='forbid')
    origin: str
    username: SecretStr
    password: SecretStr


class CredentialStore:
    def __init__(self, raw):
        try:
            data = json.loads(raw or '{}')
            if not isinstance(data, dict):
                raise ValueError()
            self._items = {}
            for name, value in data.items():
                if not isinstance(name, str) or not name or len(name) > 80:
                    raise ValueError()
                item = Credential.model_validate(value)
                item.origin = credential_origin(item.origin)
                if not item.username.get_secret_value() or not item.password.get_secret_value():
                    raise ValueError()
                self._items[name] = item
        except Exception:
            raise ValueError('CREDENTIALS_JSON 凭据配置格式错误，请检查本机 .env') from None

    def metadata(self):
        return [{'name': name, 'origin': c.origin, 'ready': True} for name, c in self._items.items()]

    def resolve(self, name, scope):
        item = self._items.get(name)
        if item is None:
            raise ValueError('凭据引用不存在，请检查本机配置')
        if item.origin != credential_origin(scope):
            raise ValueError('凭据允许来源与流程不一致')
        return item

    def secrets(self):
        return [s for c in self._items.values() for s in (c.username, c.password)]
