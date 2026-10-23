import json
import sqlite3
import pytest
from pydantic import SecretStr
from app.credentials import CredentialStore
from app.config import Settings
from app.workflows.repository import WorkflowRepository


def test_roundtrip_immutable_complete_scope_and_no_plaintext(tmp_path):
    from app.local_credentials import LocalCredentialStore
    path = tmp_path / 'local.sqlite3'
    store = LocalCredentialStore(path, CredentialStore('{}'))
    user, password = '合成-user-"\\', '合成-password-"\\'
    ref = store.save_field(None, 'https://example.test/login', 'username', user)
    with pytest.raises(ValueError, match='凭据字段不完整'):
        store.resolve(ref, 'https://example.test')
    assert store.save_field(ref, 'https://example.test', 'password', password) == ref
    assert store.metadata()[0]['ready'] is True
    new_ref = store.save_field(ref, 'https://example.test', 'password', 'replacement-secret')
    assert new_ref != ref
    restarted = LocalCredentialStore(path, CredentialStore('{}'))
    assert restarted.resolve(ref, 'https://example.test').username.get_secret_value() == user
    assert restarted.resolve(ref, 'https://example.test').password.get_secret_value() == password
    assert restarted.resolve(new_ref, 'https://example.test').password.get_secret_value() == 'replacement-secret'
    with pytest.raises(ValueError, match='凭据允许来源与流程不一致'):
        restarted.resolve(ref, 'https://wrong.test')
    for value in (user, password, 'replacement-secret'):
        assert value.encode() not in path.read_bytes()
        assert value not in json.dumps(store.metadata(), ensure_ascii=False)


def test_corrupt_payload_does_not_break_metadata_or_startup(tmp_path):
    from app.local_credentials import LocalCredentialStore
    path = tmp_path / 'local.sqlite3'
    store = LocalCredentialStore(path, CredentialStore('{}'))
    ref = store.save_field(None, 'https://example.test', 'username', 'synthetic-user')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE local_credentials SET payload=?', (b'broken-encryption',))
    store = LocalCredentialStore(path, CredentialStore('{}'))
    assert store.metadata()[0]['name'] == ref
    assert store.metadata()[0]['ready'] is False
    assert store.secrets() == []
    with pytest.raises(ValueError, match='本机凭据无法解密') as error:
        store.resolve(ref, 'https://example.test')
    assert 'broken-encryption' not in str(error.value)


def test_dynamic_secrets_before_save_and_repository_guard(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path/'agent.sqlite3', credentials_json=SecretStr(json.dumps({'legacy': {'origin':'https://example.test','username':'old-user','password':'old-password'}})))
    repo = WorkflowRepository(settings.database_path, secrets=settings.secret_values)
    secret = '新-secret-"\\'
    settings.register_secrets([secret])
    escaped = secret
    for _ in range(4):
        assert secret not in settings.scrub(secret)
        assert '[REDACTED]' in settings.scrub(escaped)
        with pytest.raises(ValueError, match='流程内容包含配置凭据'):
            repo.warn(escaped)
        escaped = json.dumps(escaped, ensure_ascii=True)[1:-1]
    assert settings.credentials.resolve('legacy', 'https://example.test').password.get_secret_value() == 'old-password'


def test_protection_failure_is_safe_and_never_persists(tmp_path, monkeypatch):
    import app.local_credentials as module
    store = module.LocalCredentialStore(tmp_path/'local.sqlite3', CredentialStore('{}'))
    def fail(data):
        raise OSError('synthetic-sensitive-value')
    monkeypatch.setattr(module, 'protect', fail)
    with pytest.raises(ValueError, match='本机凭据无法保存') as error:
        store.save_field(None, 'https://example.test', 'username', 'synthetic-sensitive-value')
    assert 'synthetic-sensitive-value' not in str(error.value)
    assert store.metadata() == []


def test_non_windows_never_falls_back_to_plaintext(monkeypatch):
    import app.local_credentials as module
    monkeypatch.setattr(module.sys, 'platform', 'linux')
    with pytest.raises(RuntimeError, match='仅支持 Windows DPAPI'):
        module.protect(b'synthetic-test')


def test_origin_errors_do_not_echo_input(tmp_path):
    from app.local_credentials import LocalCredentialStore
    store = LocalCredentialStore(tmp_path/'local.sqlite3', CredentialStore('{}'))
    with pytest.raises(ValueError) as error:
        store.save_field(None, 'https://example.test:synthetic-sensitive-value', 'username', 'synthetic-user')
    assert 'synthetic-sensitive-value' not in str(error.value)


def test_complete_corrupt_record_metadata_without_decryption(tmp_path, monkeypatch):
    import app.local_credentials as module
    path = tmp_path/'local.sqlite3'
    store = module.LocalCredentialStore(path, CredentialStore('{}'))
    ref = store.save_field(None, 'https://example.test', 'username', 'synthetic-user')
    store.save_field(ref, 'https://example.test', 'password', 'synthetic-password')
    def forbidden(data):
        raise AssertionError('metadata must not decrypt')
    monkeypatch.setattr(module, 'unprotect', forbidden)
    settings = Settings(_env_file=None, database_path=path)
    assert settings.credentials.metadata() == [{'name':ref, 'origin':'https://example.test:443', 'ready':True}]
    assert settings.scrub('ordinary text') == 'ordinary text'
    with pytest.raises(ValueError, match='本机凭据无法解密'):
        settings.credentials.resolve(ref, 'https://example.test')
