from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LocatorSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['role', 'label', 'text', 'css']
    value: str = Field(min_length=1, max_length=500)
    role: str | None = None

    @model_validator(mode='after')
    def check_role(self):
        if self.kind == 'role' and not self.role:
            raise ValueError('role locator requires role')
        return self


class BrowserAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['navigate', 'click', 'fill', 'select', 'wait', 'download']
    target: LocatorSpec | None = None
    value: str | None = Field(default=None, max_length=4000)
    wait_for_query: bool = False
    name_parts: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def check_action(self):
        if self.kind != 'navigate' and self.target is None:
            raise ValueError('target required')
        if self.kind in ('navigate', 'fill', 'select') and self.value is None:
            raise ValueError('value required')
        return self


class ActionResult(BaseModel):
    status: Literal['success', 'failed', 'ambiguous']
    message: str
    file_id: str | None = None


class PageObservation(BaseModel):
    url: str
    text: str
    elements: list[dict]


class SessionState(BaseModel):
    status: Literal['idle', 'starting', 'ready', 'running', 'failed', 'closed']
    current_url: str | None = None
