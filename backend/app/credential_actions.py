"""Private, command-scoped credential writes; model values never enter this path."""
from uuid import uuid4
from typing import Literal

from playwright.async_api import TimeoutError as BrowserTimeout

from .contracts import ActionResult, LocatorSpec
from .credential_intents import CredentialIntent
from .credentials import credential_origin


def authorize_credentials(session, intent, observation):
    session.credential_authorization = {
        'intent': intent, 'origin': credential_origin(observation.url),
        'revision': session.control_revision,
        'nodes': {e['node_id']: e.get('form_id') for e in observation.elements if e.get('node_id')},
        'filled': {},
    }
    if not getattr(session, 'credential_draft_id', None):
        session.credential_draft_id = 'session-' + uuid4().hex


# The source, uniqueness, visibility, node identity and type checks run in the
# same browser JS turn as the native value setter (also used by LoginController).
FILL = r'''(elements, args) => {
 if(location.origin!==new URL(args.origin).origin || elements.length!==1) return null;
 const el=elements[0], ids=window.__credentialNodes;
 const visible=e=>e.isConnected && !e.closest('[hidden],[aria-hidden="true"]') && e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
 if(!ids || !Object.hasOwn(args.nodes,ids.nodes.get(el)) || !(el instanceof HTMLInputElement) || !visible(el) || el.disabled || el.readOnly) return null;
 const form=el.form || document.documentElement;
 const passwords=[...form.querySelectorAll('input[type=password]')].filter(visible);
 if(passwords.length!==1) return null;
 if(args.field==='password' ? el.type!=='password' :
    !['text','email','tel'].includes(el.type) || !/username|user|account|login|email|账号|帐号|账户|用户名/i.test([el.name,el.id,el.autocomplete,el.getAttribute('aria-label'),...[...el.labels||[]].map(x=>x.textContent)].join(' '))) return null;
 const formId=ids.nodes.get(form);
 if(!formId || args.nodes[ids.nodes.get(el)]!==formId) return null;
 el.focus();
 Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el,args.value);
 el.dispatchEvent(new Event('input',{bubbles:true}));
 el.dispatchEvent(new Event('change',{bubbles:true}));
 return {form_id:formId};
}'''


async def execute_credential_fill(session, intent: CredentialIntent, field: Literal['username', 'password'], target: LocatorSpec) -> ActionResult:
    auth=getattr(session, 'credential_authorization', None)
    if (field not in ('username','password') or not auth or auth['intent'] is not intent
            or auth['revision'] != session.control_revision or session.control_mode != 'agent'
            or getattr(intent, field) is None or field in auth['filled']):
        return ActionResult(status='ambiguous', message='凭据授权已失效或字段未授权，请重新明确指令')
    try:
        value=getattr(intent, field).get_secret_value()
        data=await session.locator(target).evaluate_all(FILL, {'origin':auth['origin'], 'nodes':auth['nodes'], 'field':field, 'value':value})
        if not data:
            return ActionResult(status='ambiguous', message='登录字段来源、类型或定位不明确，请重新观察后明确目标')
        # Prevent an automatic retry even when persistence fails after the write.
        auth['filled'][field]=data['form_id']
        key=(session.credential_draft_id, auth['origin'], data['form_id'])
        tracker = getattr(session, 'login_recording', None)
        if tracker:
            tracker.prepare_field(session, field, key)
        binding=getattr(session, 'credential_binding', None)
        ref=binding['credential_ref'] if binding and binding['key']==key else None
        try:
            ref=session.settings.credentials.save_field(ref, auth['origin'], field, value)
        except Exception:
            return ActionResult(status='failed', message='凭据已填写但未保存，请检查本机安全存储；不会自动重填', effect_started=True)
        session.credential_binding={'key':key, 'credential_ref':ref}
        record={'field':field, 'target':target.model_dump(), 'origin':auth['origin'], 'form_id':data['form_id'], 'draft_id':key[0], 'credential_ref':ref}
        return ActionResult(status='success', message='凭据字段已填写并保存', effect_started=True, credential_record=record)
    except Exception:
        return ActionResult(status='failed', message='凭据填写失败，请重新观察页面后明确目标')


async def execute_credential_submit(session, intent: CredentialIntent, target: LocatorSpec) -> ActionResult:
    auth=getattr(session, 'credential_authorization', None)
    required={f for f in ('username','password') if getattr(intent,f) is not None}
    if (not auth or auth.get('submitted') or auth['intent'] is not intent or not intent.submit or set(auth['filled'])!=required
            or len(set(auth['filled'].values()))!=1
            or auth['revision']!=session.control_revision or session.control_mode!='agent'):
        return ActionResult(status='ambiguous', message='当前指令未授权提交或填写尚未完成')
    try:
        old_scope = session.flow_scope
        session.flow_scope = auth['origin']
        blocked = {'value': False}
        try:
            async with session.navigation_scope() as blocked:
                try:
                    async with session.page.expect_navigation(wait_until='domcontentloaded', timeout=session.settings.action_timeout_ms):
                        ok=await session.locator(target).evaluate_all(r'''(els,a)=>{
          if(location.origin!==new URL(a.origin).origin || els.length!==1)return false;
          const el=els[0], ids=window.__credentialNodes;
          if(!ids || !Object.hasOwn(a.nodes,ids.nodes.get(el)) || !el.isConnected || el.disabled || !el.getClientRects().length || getComputedStyle(el).visibility==='hidden' || el.closest('[hidden],[aria-hidden="true"]'))return false;
          if(!el.matches('button,input[type=submit],input[type=button]') || !el.form || ids.nodes.get(el.form)!==a.form || a.nodes[ids.nodes.get(el)]!==a.form)return false;
          if(new URL(el.form.action || location.href,location.href).origin!==new URL(a.origin).origin || (el.formTarget && el.formTarget!=='_self') || (el.form.target && el.form.target!=='_self') || (el.formAction && new URL(el.formAction,location.href).origin!==new URL(a.origin).origin))return false;
          const name=el.getAttribute('aria-label')||el.textContent||el.value||'';
          if(!a.names.includes(name.trim()))return false;
          el.click();return true;
        }''', {'origin':auth['origin'],'nodes':auth['nodes'], 'form':next(iter(auth['filled'].values())), 'names':[intent.submit,intent.submit.removesuffix('按钮')]})
                except Exception as exc:
                    if not isinstance(exc, BrowserTimeout) and not blocked['value']:
                        raise
        finally:
            session.flow_scope = old_scope
        if blocked['value'] or credential_origin(session.page.url)!=auth['origin']:
            return ActionResult(status='failed', message='登录提交跳转超出授权来源', scope_violation=True)
        if not ok:
            return ActionResult(status='ambiguous', message='提交目标不属于已授权登录表单')
        auth['submitted'] = True
        binding = session.credential_binding
        record = {'field':'submit', 'target':target.model_dump(), 'origin':auth['origin'],
                  'form_id':next(iter(auth['filled'].values())), 'draft_id':session.credential_draft_id,
                  'credential_ref':binding['credential_ref']}
        return ActionResult(status='success', message='登录提交已执行，结果待确认', credential_record=record)
    except Exception:
        return ActionResult(status='failed',message='登录提交失败，请检查当前页面')
