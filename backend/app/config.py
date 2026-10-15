from pathlib import Path
import json

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', env_prefix='')

    site_url: str = 'http://127.0.0.1:8000/demo/login'
    site_username: SecretStr = SecretStr('demo')
    site_password: SecretStr = SecretStr('demo-password')
    login_username_selector: str = '#username'
    login_password_selector: str = '#password'
    login_submit_selector: str = '#login'
    logged_in_selector: str = '#logged-in'
    loading_selector: str = '#loading'
    result_selector: str = '#results'
    llm_base_url: str = ''
    llm_api_key: SecretStr = SecretStr('')
    llm_model: str = ''
    download_dir: Path = ROOT / 'downloads'
    headless: bool = True
    action_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    download_timeout_ms: int = Field(default=60000, ge=100, le=300000)
    query_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    model_timeout_s: float = Field(default=60, gt=0, le=300)
    max_tool_actions: int = Field(default=8, ge=1, le=32)
    frontend_origin: str = 'http://127.0.0.1:5173'

    def redact(self, value: str) -> str:
        for secret in (self.site_username, self.site_password, self.llm_api_key):
            raw = secret.get_secret_value()
            if raw:
                # Also cover string values containing nested JSON tool/observation messages.
                variants = {raw}
                for _ in range(3):
                    variants |= {json.dumps(item, ensure_ascii=ascii_only)[1:-1]
                                 for item in tuple(variants) for ascii_only in (False, True)}
                for variant in sorted(variants, key=len, reverse=True):
                    value = value.replace(variant, '[REDACTED]')
        return value

    def scrub(self, value):
        """Redact leaf strings before JSON encoding; preserve structured API types."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        return value
