from pathlib import Path
import json
import re
from functools import cached_property
from .credentials import secret_variants
from .secret_context import ARIA_ROLES, data_leaves

from pydantic import Field, PrivateAttr, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', env_prefix='')

    _registered_secrets: set[str] = PrivateAttr(default_factory=set)

    loading_selector: str = '#loading'
    result_selector: str = '#results'
    llm_base_url: str = ''
    llm_api_key: SecretStr = SecretStr('')
    llm_model: str = ''
    credentials_json: SecretStr = SecretStr('{}')
    download_dir: Path = ROOT / 'downloads'
    database_path: Path = ROOT / 'data' / 'agent-v2.sqlite3'
    headless: bool = False
    action_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    download_timeout_ms: int = Field(default=60000, ge=100, le=300000)
    query_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    model_timeout_s: float = Field(default=60, gt=0, le=300)
    max_tool_actions: int = Field(default=8, ge=1, le=32)
    frontend_origin: str = 'http://127.0.0.1:5173'

    @cached_property
    def credentials(self):
        from .credentials import CredentialStore
        from .local_credentials import LocalCredentialStore
        return LocalCredentialStore(self.database_path, CredentialStore(self.credentials_json.get_secret_value()))

    def register_secrets(self, values: list[str]) -> None:
        self._registered_secrets.update(value for value in values if value)

    def secret_values(self):
        return [self.llm_api_key, *self.credentials.secrets(),
                *[SecretStr(value) for value in self._registered_secrets]]

    def redact(self, value: str, *, tokenized=True) -> str:
        variants = set()
        for secret in self.secret_values():
            raw = secret.get_secret_value()
            if raw:
                # Also cover string values containing nested JSON tool/observation messages.
                variants.update(secret_variants(raw))
        for variant in sorted(variants, key=len, reverse=True):
            if tokenized and len(variant) <= 2 and variant.isalnum():
                value = re.sub(r'(?<![A-Za-z0-9_])' + re.escape(variant) + r'(?![A-Za-z0-9_])', '[REDACTED]', value)
            else:
                value = value.replace(variant, '[REDACTED]')
        return value

    def scrub(self, value):
        """Redact leaf strings before JSON encoding; preserve structured API types."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, dict):
            if 'elements' in value and 'text' in value:
                # Browser input types and document-local IDs are typed metadata.
                return {**value, **({'url': self.redact(value['url'], tokenized=True)} if 'url' in value else {}),
                    'text': self.redact(value['text']), 'elements': [
                    {**element, **{key: self.scrub(element[key]) for key in ('name', 'label') if key in element},
                     **({'role': self.redact(element['role'])} if element.get('role') and element['role'] not in ARIA_ROLES else {}),
                     **({'selector': self.scrub({'kind':'css','value':element['selector']})['value']} if element.get('selector') else {})}
                    for element in value['elements']]}
            if value.get('kind') in ('css', 'role', 'label', 'text') and 'value' in value:
                return {**value,
                    **({'role': self.redact(value['role'])} if value.get('role') and value['role'] not in ARIA_ROLES else {}),
                    **({'value': self.redact(value['value'])} if list(data_leaves(value)) else {})}
            structural = {'kind', 'status', 'field', 'credential_ref', 'form_id', 'draft_id'}
            def safe_item(key, item):
                if key in structural:
                    return item
                if key in ('file_id', 'file_ids'):
                    refs = item if isinstance(item, list) else [item]
                    if all(ref is None or isinstance(ref, str) and re.fullmatch('[0-9a-f]{32}',ref) for ref in refs):
                        return item
                return self.scrub(item)
            return {key: safe_item(key,item) for key, item in value.items()}
        return value
