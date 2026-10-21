from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import BrowserAction, LocatorSpec


def site_key(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('网站地址必须是 HTTP(S)，且不能包含凭据')
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    return f'{parsed.scheme}://{host}:{parsed.port or (443 if parsed.scheme == "https" else 80)}'


def clean_entry_url(url: str) -> str:
    site_key(url)
    p = urlsplit(url)
    denied = {'token', 'access_token', 'refresh_token', 'id_token', 'password', 'api_key', 'authorization', 'session', 'sessionid'}
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in denied])
    fragment = '' if any(key + '=' in p.fragment.lower() for key in denied) else p.fragment
    return urlunsplit((p.scheme, p.netloc, p.path, query, fragment))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class WorkflowParameter(StrictModel):
    key: str = Field(pattern=r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$')
    label: str = Field(min_length=1, max_length=80)
    kind: Literal['text', 'number', 'url', 'credential'] = 'text'
    required: bool = True
    default: str | None = Field(default=None, max_length=4000)


class ConditionSpec(StrictModel):
    target: LocatorSpec
    state: Literal['visible', 'hidden'] = 'visible'
    when_true: Literal['continue', 'no_data', 'finish'] = 'no_data'
    when_false: Literal['continue', 'no_data', 'finish'] = 'continue'


class LoginSpec(StrictModel):
    url: str = Field(min_length=1, max_length=2000)
    url_parameter: str | None = None
    credential_ref: str | None = Field(default=None, max_length=80)
    credential_parameter: str | None = None
    username: LocatorSpec
    password: LocatorSpec
    submit: LocatorSpec
    success: LocatorSpec
    reuse: bool = True

    @model_validator(mode='after')
    def check_login(self):
        if clean_entry_url(self.url) != self.url:
            raise ValueError('登录网址不能包含认证字段')
        if bool(self.credential_ref) == bool(self.credential_parameter):
            raise ValueError('登录必须选择固定凭据或凭据参数之一')
        return self


class WorkflowStep(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    action: BrowserAction | None = None
    value_parameter: str | None = None
    condition: ConditionSpec | None = None
    login: LoginSpec | None = None

    @model_validator(mode='after')
    def check_step(self):
        if sum(x is not None for x in (self.action, self.condition, self.login)) != 1:
            raise ValueError('步骤必须是操作、条件或登录之一')
        if self.value_parameter and (not self.action or self.action.kind not in ('fill', 'select')):
            raise ValueError('参数只能绑定输入或选择步骤')
        return self


class WorkflowDefinition(StrictModel):
    schema_version: Literal[2] = 2
    name: str = Field(min_length=1, max_length=80)
    site_key: str
    entry_url: str = Field(min_length=1, max_length=2000)
    parameters: list[WorkflowParameter] = Field(default_factory=list, max_length=32)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def check_definition(self):
        if not self.name.strip():
            raise ValueError('流程名称不能为空')
        if site_key(self.entry_url) != self.site_key or clean_entry_url(self.entry_url) != self.entry_url:
            raise ValueError('入口地址必须属于当前系统且不能携带认证字段')
        keys = [p.key for p in self.parameters]
        if len(keys) != len(set(keys)) or len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError('参数键和步骤 ID 必须唯一')
        for step in self.steps:
            if step.value_parameter and step.value_parameter not in keys:
                raise ValueError('步骤引用了不存在的参数')
            if step.action and step.action.kind == 'navigate':
                if site_key(step.action.value) != self.site_key or clean_entry_url(step.action.value) != step.action.value:
                    raise ValueError('导航必须属于当前系统且不能携带认证字段')
            kinds = {p.key: p.kind for p in self.parameters}
            if step.value_parameter and kinds[step.value_parameter] in ('credential', 'url'):
                raise ValueError('凭据和网址参数只能绑定登录配置')
            if step.login:
                if site_key(step.login.url) != self.site_key:
                    raise ValueError('登录网址必须属于流程来源')
                for key, kind in ((step.login.url_parameter, 'url'), (step.login.credential_parameter, 'credential')):
                    if key and kinds.get(key) != kind:
                        raise ValueError('登录参数引用类型不匹配')
        return self


def validate_parameters(definition: WorkflowDefinition, values: dict[str, str], credentials=None) -> dict[str, str]:
    allowed = {p.key for p in definition.parameters}
    if set(values) - allowed:
        raise ValueError('包含未知参数')
    resolved = {}
    for parameter in definition.parameters:
        value = values.get(parameter.key, parameter.default)
        if value is None:
            value = ''
        if not isinstance(value, str) or len(value) > 4000 or (parameter.required and not value.strip()):
            raise ValueError(f'请填写有效的“{parameter.label}”')
        if parameter.kind == 'number' and value:
            try:
                if not Decimal(value).is_finite():
                    raise InvalidOperation()
            except InvalidOperation:
                raise ValueError(f'“{parameter.label}”必须为有效数值')
        if parameter.kind == 'url' and value:
            if site_key(value) != definition.site_key or clean_entry_url(value) != value:
                raise ValueError('网址参数必须属于流程来源且不能携带认证字段')
        if parameter.kind == 'credential' and credentials is not None:
            credentials.resolve(value, definition.site_key)
        resolved[parameter.key] = value
    if credentials is not None:
        for step in definition.steps:
            if step.login:
                credentials.resolve(resolved.get(step.login.credential_parameter) if step.login.credential_parameter else step.login.credential_ref, definition.site_key)
    return resolved
