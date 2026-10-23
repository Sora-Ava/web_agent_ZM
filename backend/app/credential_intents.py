"""Constrained, local credential syntax; raw values never become model instructions."""
import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr

CLARIFICATION = '请明确账号或密码：使用 账号 "值"、密码 "值"，可追加 点击登录；含空格或分隔符的值必须使用完整引号。'


class CredentialClarification(ValueError):
    def __init__(self):
        super().__init__(CLARIFICATION)


class CredentialIntent(BaseModel):
    model_config = ConfigDict(frozen=True)
    username: SecretStr | None = Field(default=None, repr=False, exclude=True)
    password: SecretStr | None = Field(default=None, repr=False, exclude=True)
    display_instruction: str
    submit: str | None = None

    def secret_values(self) -> list[str]:
        return [value.get_secret_value() for value in (self.username, self.password) if value is not None]


_LABEL = re.compile(r'(?:在)?(账号|帐号|账户|用户名|密码|口令|username|password|account)(?:框(?:中)?(?:输入|填写)|\s*(?:是|为|[:：=])|\s+|(?=[\"\'“‘「『]))\s*', re.I)
_SEPARATOR = re.compile(r'[\s，,；;]*(?:然后|并且|并)?\s*')


def parse_credential_intent(instruction: str) -> CredentialIntent | None:
    text = instruction.strip()
    # Navigation and clicks on credential-related controls contain no supplied values.
    if re.fullmatch(r'(?:(?:打开|访问|前往|navigate)\s*)?https?://[^\s]+', text, re.I):
        return None
    if re.fullmatch(r'(?:点击|单击|打开|click\s+)\s*[^\s，,；;:=：\"\']+(?:按钮)?', text, re.I):
        return None
    if not _LABEL.match(text):
        if re.search(r'(?:账号|帐号|账户|用户名|密码|口令|username|password|account)', text, re.I):
            raise CredentialClarification()
        return None
    # Only a terminal sentence wrapper is removed; quoted punctuation stays intact.
    text = text.removesuffix('。').rstrip()
    values = {}
    submit = None
    position = 0
    while position < len(text):
        match = _LABEL.match(text, position)
        if not match:
            raise CredentialClarification()
        key = 'password' if match[1].lower() in ('密码', '口令', 'password') else 'username'
        if key in values:
            raise CredentialClarification()
        position = match.end()
        if position == len(text):
            raise CredentialClarification()
        quote = text[position]
        if quote in ('"', "'", '“', '‘', '「', '『'):
            closing = {'“': '”', '‘': '’', '「': '」', '『': '』'}.get(quote, quote)
            end = text.find(closing, position + 1)
            if end == -1:
                raise CredentialClarification()
            value = text[position + 1:end]
            position = end + 1
        else:
            match = re.match(r'[^\s，,；;"\'“”‘’「」『』。]+', text[position:])
            if not match:
                raise CredentialClarification()
            value = match[0]
            # Unquoted interrogatives are requests for help, not literal fill authority.
            # Quote these strings explicitly when they are the intended credential.
            if re.search(r'什么|哪里|哪儿|怎么|如何|是否|忘记|[吗？?]', value):
                raise CredentialClarification()
            position += len(value)
        if not value:
            raise CredentialClarification()
        values[key] = SecretStr(value)
        position = _SEPARATOR.match(text, position).end()
        if position == len(text):
            break
        click = re.fullmatch(r'(?:填写后)?(?:点击|单击)\s*([^\s，,；;"\'“”‘’「」『』。]+)', text[position:])
        if click:
            submit = click[1]
            # Do not allow arbitrary appended text (which could itself contain secrets).
            if submit not in ('登录', '登入', '提交', '登录按钮', '提交按钮'):
                raise CredentialClarification()
            break
        if not _LABEL.match(text, position):
            raise CredentialClarification()
    display = '；'.join(('填写账号 [REDACTED]' if key == 'username' else '填写密码 [REDACTED]') for key in values)
    if submit:
        display += '；点击' + submit
    return CredentialIntent(**values, display_instruction=display, submit=submit)
