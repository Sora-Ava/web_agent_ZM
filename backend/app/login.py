"""Deterministic composite login, with secrets confined to this module."""
from .contracts import ActionResult
from .credentials import credential_origin, validate_credential_reference
from .workflows.models import LoginSpec, clean_entry_url


class LoginController:
    def __init__(self, session):
        self.session = session

    async def is_login_page(self, spec):
        try:
            if credential_origin(self.session.page.url) != credential_origin(spec.url):
                return False
            fields = [self.session.locator(spec.username), self.session.locator(spec.password)]
            return all([await f.count() == 1 and await f.is_visible() for f in fields])
        except Exception:
            return False

    async def execute(self, spec: LoginSpec, values, scope, force=False):
        s = self.session
        scope = credential_origin(scope)
        try:
            spec = LoginSpec.model_validate(spec.model_dump())
            url = values[spec.url_parameter] if spec.url_parameter else spec.url
            ref = values[spec.credential_parameter] if spec.credential_parameter else spec.credential_ref
            if credential_origin(url) != scope or clean_entry_url(url) != url:
                raise ValueError('登录网址与流程来源不一致')
            validate_credential_reference(ref, scope, s.settings.credentials)
            await s.start()
            async def success():
                try:
                    current_scope = credential_origin(s.page.url)
                except ValueError:
                    return False
                if current_scope != scope:
                    return False
                loc = s.locator(spec.success)
                return await loc.count() == 1 and await loc.is_visible()
            if spec.reuse and not force and await success():
                return ActionResult(status='success', message='复用当前已登录会话')
            previous_scope = s.flow_scope
            s.flow_scope = scope
            try:
                async with s.navigation_scope() as blocked:
                    await s.page.goto(url, wait_until='domcontentloaded')
                    s.validate_flow_page(blocked)
                    if credential_origin(s.page.url) != scope:
                        raise ValueError('登录页面跳转到其他来源，未填写凭据')
                    if await success():
                        if spec.reuse and not force:
                            return ActionResult(status='success', message='复用当前已登录会话')
                        if not await self.is_login_page(spec):
                            raise ValueError('当前已登录且没有登录表单，无法重新登录')
                    fields = [s.locator(x) for x in (spec.username, spec.password, spec.submit)]
                    for field in fields:
                        if await field.count() != 1:
                            raise ValueError('登录表单定位不唯一或不存在')
                        await field.wait_for(state='visible')
                    if not await fields[1].evaluate('(el)=>el.matches("input[type=password]")'):
                        raise ValueError('密码目标必须是密码输入框')
                    credential = s.settings.credentials.resolve(ref, scope)
                    # A fresh credential attempt invalidates the previous identity.
                    s.authenticated.pop(scope, None)
                    # Check origin in the same JS turn that writes each secret, avoiding
                    # a check/fill navigation race. No model-supplied code is evaluated.
                    for field, secret in zip(fields[:2], (credential.username, credential.password)):
                        await field.evaluate('''(el, args) => {
                            if(location.origin !== new URL(args.scope).origin)throw Error('origin changed');
                            if(!(el instanceof HTMLInputElement))throw Error('input required');
                            el.focus();
                            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,args.value);
                            el.dispatchEvent(new Event('input',{bubbles:true}));
                            el.dispatchEvent(new Event('change',{bubbles:true}));
                        }''', {'scope': scope, 'value': secret.get_secret_value()})
                    await fields[2].click()
                    s.validate_flow_page(blocked)
                    await s.locator(spec.success).wait_for(state='visible')
                    s.validate_flow_page(blocked)
                    if not await success():
                        raise ValueError('登录成功标识不唯一或来源变化')
                    s.authenticated[scope] = ref
                    return ActionResult(status='success', message='登录成功并已验证')
            finally:
                s.flow_scope = previous_scope
        except ValueError as exc:
            return ActionResult(status='failed', message=s.settings.redact(str(exc)))
        except Exception:
            return ActionResult(status='failed', message='登录失败，请检查凭据、定位或网页验证要求')
