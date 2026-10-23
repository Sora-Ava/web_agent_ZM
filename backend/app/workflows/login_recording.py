"""Durable, secret-free login attempts and live success-marker confirmation."""
import json

from ..contracts import LocatorSpec
from .models import LoginSpec, clean_entry_url, site_key


def build_login_proposal(records, success: LocatorSpec | None) -> LoginSpec | None:
    if success is None or len(records) != 3:
        return None
    keys = {(r['draft_id'], r['origin'], r['form_id'], r['credential_ref']) for r in records}
    fields = {r['field']: r for r in records}
    if len(keys) != 1 or set(fields) != {'username', 'password', 'submit'}:
        return None
    if records[-1]['field'] != 'submit':
        return None
    return LoginSpec(url=records[0]['url'], credential_ref=records[0]['credential_ref'],
                     username=fields['username']['target'], password=fields['password']['target'],
                     submit=fields['submit']['target'], success=success, reuse=True)


class LoginRecordingTracker:
    def __init__(self, repository):
        self.repository = repository

    def attempts(self):
        attempts = []
        for row in self.repository.draft()['steps']:
            data = row['action'].get('credential')
            if not data:
                if attempts and attempts[-1]['status'] == 'incomplete' and row['action'].get('kind') == 'navigate':
                    attempts[-1]['interrupted'] = True
                continue
            key = (data['draft_id'], data['origin'], data['form_id'], data['credential_ref'])
            if not attempts or attempts[-1]['key'] != key:
                # A fresh binding explicitly replaces an obsolete unconfirmed attempt.
                if attempts:
                    attempts[-1]['status'] = 'superseded'
                attempts.append({'id': str(row['id']), 'key': key, 'records': [], 'row_ids': [], 'status': 'incomplete'})
            attempt = attempts[-1]
            attempt['records'].append(data)
            attempt['row_ids'].append(row['id'])
            fields = {r['field'] for r in attempt['records']}
            attempt['status'] = 'incomplete'
            if len(attempt['records']) == 3 and fields == {'username', 'password', 'submit'} and data['field'] == 'submit' and not attempt.get('interrupted'):
                attempt['status'] = 'awaiting_confirmation'
        return attempts

    def prepare_field(self, session, field, key):
        """Never resurrect a binding cleared by manual control or a new document."""
        binding = getattr(session, 'credential_binding', None)
        if not binding or binding['key'] != key:
            return
        matching = [a for a in self.attempts() if a['key'] == (*key, binding['credential_ref'])]
        if not matching or matching[-1].get('interrupted') or matching[-1]['status'] != 'incomplete' or any(r['field'] == field for r in matching[-1]['records']):
            session.credential_binding = None

    def record(self, data, command_id, url):
        repo = self.repository
        draft = repo.draft()
        if data['draft_id'] != draft['id'] or data['field'] not in ('username', 'password', 'submit'):
            raise ValueError('登录录制草稿已变化，请重新填写')
        origin = site_key(data['origin'])
        if site_key(url) != origin:
            raise ValueError('登录录制来源不一致')
        repo.bind_scope(url)
        safe = {key: data[key] for key in ('field','origin','form_id','draft_id','credential_ref')}
        safe.update(origin=origin, target=LocatorSpec.model_validate(data['target']).model_dump(), url=clean_entry_url(url))
        repo._no_secrets(safe)
        with repo.connection() as db:
            db.execute('INSERT INTO draft_steps(draft_id,action,command_id,instruction,url_before) VALUES(?,?,?,?,?)',
                       (draft['id'], json.dumps({'credential': safe}, ensure_ascii=False), command_id, '登录凭据操作', safe['url']))
            db.execute('UPDATE drafts SET revision=revision+1 WHERE id=?', (draft['id'],))

    def pending_metadata(self):
        attempts = self.attempts()
        if not attempts:
            return None
        active = [a for a in attempts if a['status'] != 'superseded']
        if not active:
            return None
        return {'attempts': [{'id': a['id'], 'status': a['status'],
                              'fields': list(dict.fromkeys(r['field'] for r in a['records'])),
                              'origin': a['records'][0]['origin'], 'candidates': []} for a in attempts],
                'message': '凭据已保存到本机；请确认登录成功标识' if active[-1]['status'] == 'awaiting_confirmation'
                else '登录记录未完成，请在同一表单重新明确填写账号、密码并提交登录'}

    def complete(self, attempt_id, spec, revision):
        from .repository import VersionConflict
        repo = self.repository
        draft = repo.draft()
        if draft['revision'] != revision:
            raise VersionConflict('录制草稿已变化，请重新确认登录标识')
        attempt = next((a for a in self.attempts() if a['id'] == attempt_id and a['status'] == 'awaiting_confirmation'), None)
        if not attempt or build_login_proposal(attempt['records'], spec.success) != spec:
            raise ValueError('登录记录不完整或已被新尝试替代')
        repo._no_secrets(spec.model_dump())
        with repo.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM drafts WHERE id=? AND active=1', (draft['id'],)).fetchone()
            if not row or f'{draft["id"]}:{row["revision"]}' != revision:
                raise VersionConflict('录制草稿已变化，请重新确认登录标识')
            # Older unconfirmed attempts are explicitly superseded; ordinary actions stay.
            all_attempts = self.attempts()
            for old in all_attempts:
                for row_id in old['row_ids']:
                    if row_id == int(attempt_id):
                        db.execute('UPDATE draft_steps SET action=? WHERE id=?', (json.dumps({'login': spec.model_dump()}, ensure_ascii=False), row_id))
                    elif old['status'] == 'superseded' or old['id'] == attempt_id:
                        db.execute('DELETE FROM draft_steps WHERE id=?', (row_id,))
            warnings = draft['warnings']
            if any(a['status'] == 'superseded' for a in all_attempts):
                warnings = [*warnings, '旧的未确认登录尝试已由本次完整登录替代；普通业务操作保留，请检查顺序'][-20:]
            db.execute('UPDATE drafts SET revision=revision+1,warnings=? WHERE id=?', (json.dumps(warnings, ensure_ascii=False), draft['id']))

    async def before_click(self, session, action):
        binding = getattr(session, 'credential_binding', None)
        if action.kind != 'click' or not binding or binding['key'][0] != self.repository.draft()['id']:
            return None
        if site_key(session.page.url) != binding['key'][1]:
            return None
        form = await session.locator(action.target).evaluate_all('''els=>{
          if(els.length!==1)return null;
          const el=els[0];
          if(!el.matches('button,input[type=submit],input[type=button]') || !el.form || el.disabled || !el.getClientRects().length)return null;
          const name=(el.getAttribute('aria-label')||el.textContent||el.value||'').trim();
          if(el.type!=='submit' && !/^(登录|登陆|登入|登錄|登入系統|log in|login|sign in)$/i.test(name))return null;
          if(el.closest('[hidden],[aria-hidden="true"]') || getComputedStyle(el).visibility==='hidden')return null;
          return window.__credentialNodes?.nodes.get(el.form) || null;
        }''')
        if form != binding['key'][2]:
            return None
        return dict(field='submit', target=action.target.model_dump(), draft_id=binding['key'][0],
                    origin=binding['key'][1], form_id=form, credential_ref=binding['credential_ref'])

    async def _live_ready(self, session, attempt):
        if not session.page or session.page.is_closed() or site_key(session.page.url) != attempt['records'][0]['origin']:
            raise ValueError('请回到同一来源的登录后页面再确认')
        # Any visible password form is conservatively treated as still awaiting login.
        active = await session.page.locator('input[type=password]').evaluate_all('''els=>els.some(el=>
          el.isConnected && !el.closest('[hidden],[aria-hidden="true"]') && el.getClientRects().length && getComputedStyle(el).visibility!=='hidden')''')
        if active:
            raise ValueError('登录表单仍然可见，登录结果尚未确认；请完成登录后重试')

    async def candidates(self, session):
        metadata = self.pending_metadata()
        if not metadata:
            return None
        for item in metadata['attempts']:
            if item['status'] != 'awaiting_confirmation':
                continue
            attempt = next(a for a in self.attempts() if a['id'] == item['id'])
            try:
                await self._live_ready(session, attempt)
                observation = await session.observe(generic=True)
                for element in observation.elements:
                    name = element.get('name')
                    role = element.get('role')
                    if not name or role not in ('link','button','heading','navigation'):
                        continue
                    target = LocatorSpec(kind='role', role=role, value=name)
                    loc = session.locator(target)
                    if await loc.count() == 1 and await loc.is_visible():
                        candidate = {'label': name, 'target': target.model_dump()}
                        if candidate not in item['candidates']:
                            item['candidates'].append(candidate)
                item['candidates'] = item['candidates'][:20]
                if not item['candidates']:
                    metadata['message'] = '未找到唯一可见的登录成功标识，请打开有业务导航的登录后页面再重试'
            except ValueError as exc:
                metadata['message'] = str(exc)
        return metadata

    async def confirm(self, session, attempt_id, target, revision):
        attempt = next((a for a in self.attempts() if a['id'] == attempt_id and a['status'] == 'awaiting_confirmation'), None)
        if not attempt:
            raise ValueError('登录记录不完整或已被替代，请重新生成')
        await self._live_ready(session, attempt)
        loc = session.locator(target)
        valid = await loc.evaluate_all('''(els,origin)=>{
          const visible=el=>el.isConnected && !el.closest('[hidden],[aria-hidden="true"]') && el.getClientRects().length && getComputedStyle(el).visibility!=='hidden';
          return location.origin===new URL(origin).origin && els.length===1 && visible(els[0]) && ![...document.querySelectorAll('input[type=password]')].some(visible);
        }''', attempt['records'][0]['origin'])
        if not valid:
            raise ValueError('成功标识必须唯一且可见')
        spec = build_login_proposal(attempt['records'], target)
        self.complete(attempt_id, spec, revision)
