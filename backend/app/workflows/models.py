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
    kind: Literal['text', 'number'] = 'text'
    required: bool = True
    default: str | None = Field(default=None, max_length=4000)


class ConditionSpec(StrictModel):
    target: LocatorSpec
    state: Literal['visible', 'hidden'] = 'visible'
    when_true: Literal['continue', 'no_data', 'finish'] = 'no_data'
    when_false: Literal['continue', 'no_data', 'finish'] = 'continue'


class WorkflowStep(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    action: BrowserAction | None = None
    value_parameter: str | None = None
    condition: ConditionSpec | None = None

    @model_validator(mode='after')
    def check_step(self):
        if (self.action is None) == (self.condition is None):
            raise ValueError('步骤必须是操作或条件之一')
        if self.value_parameter and (not self.action or self.action.kind not in ('fill', 'select')):
            raise ValueError('参数只能绑定输入或选择步骤')
        return self


class WorkflowDefinition(StrictModel):
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
        return self


def validate_parameters(definition: WorkflowDefinition, values: dict[str, str]) -> dict[str, str]:
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
        resolved[parameter.key] = value
    return resolved
