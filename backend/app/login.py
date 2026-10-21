"""Deterministic composite login, with secrets confined to this module."""
from .contracts import ActionResult
from .credentials import credential_origin
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
            credential = s.settings.credentials.resolve(ref, scope)
            await s.start()
            if scope in s.authenticated and s.authenticated[scope] != ref:
                raise ValueError('当前已有其他凭据会话，请先关闭浏览器并重新启动后切换账号')
            async def success():
                try:
                    current_scope = credential_origin(s.page.url)
                except ValueError:
                    return False
                if current_scope != scope:
                    return False
                loc = s.locator(spec.success)
                return await loc.count() == 1 and await loc.is_visible()
            if await success():
                if s.authenticated.get(scope) != ref:
                    raise ValueError('当前账号会话无法确认，请先关闭浏览器并重新启动后切换账号')
                if spec.reuse and not force:
                    return ActionResult(status='success', message='复用已验证登录会话')
            async def guard(route):
                request = route.request
                if request.is_navigation_request() and request.frame == s.page.main_frame:
                    try:
                        if credential_origin(request.url) != scope:
                            return await route.abort()
                    except ValueError:
                        return await route.abort()
                await route.fallback()
            await s.page.route('**/*', guard)
            try:
                await s.page.goto(url, wait_until='domcontentloaded')
                if credential_origin(s.page.url) != scope:
                    raise ValueError('登录页面跳转到其他来源，未填写凭据')
                if await success():
                    if s.authenticated.get(scope) == ref:
                        return ActionResult(status='success', message='复用已验证登录会话')
                    raise ValueError('当前已有未知账号会话，请先关闭浏览器并重新启动后切换账号')
                fields = [s.locator(x) for x in (spec.username, spec.password, spec.submit)]
                for field in fields:
                    if await field.count() != 1:
                        raise ValueError('登录表单定位不唯一或不存在')
                    await field.wait_for(state='visible')
                if not await fields[1].evaluate('(el)=>el.matches("input[type=password]")'):
                    raise ValueError('密码目标必须是密码输入框')
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
                await s.locator(spec.success).wait_for(state='visible')
                if not await success():
                    raise ValueError('登录成功标识不唯一或来源变化')
                s.authenticated[scope] = ref
                return ActionResult(status='success', message='登录成功并已验证')
            finally:
                await s.page.unroute('**/*', guard)
        except ValueError as exc:
            return ActionResult(status='failed', message=s.settings.redact(str(exc)))
        except Exception:
            return ActionResult(status='failed', message='登录失败，请检查凭据、定位或网页验证要求')
