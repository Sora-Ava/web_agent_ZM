import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..contracts import BrowserAction
from .models import ConditionSpec, WorkflowDefinition, WorkflowParameter, WorkflowStep, clean_entry_url, site_key


class VersionConflict(ValueError):
    pass


class WorkflowRepository:
    def __init__(self, path: Path, scope: str, secrets=()):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.scope = scope
        self.secrets = tuple(secret for secret in secrets if secret)
        with self.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, site_key TEXT NOT NULL, active INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0, warnings TEXT NOT NULL DEFAULT '[]');
              CREATE TABLE IF NOT EXISTS draft_steps(id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT NOT NULL, action TEXT NOT NULL, command_id TEXT NOT NULL, instruction TEXT NOT NULL, url_before TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS workflows(id TEXT PRIMARY KEY, site_key TEXT NOT NULL, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS workflow_versions(flow_id TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(flow_id,version));
              CREATE UNIQUE INDEX IF NOT EXISTS active_draft ON drafts(site_key) WHERE active=1;
            ''')
            if not db.execute('SELECT id FROM drafts WHERE site_key=? AND active=1', (scope,)).fetchone():
                db.execute('INSERT INTO drafts(id,site_key,active) VALUES(?,?,1)', (uuid4().hex, scope))
            if 'deleted_at' not in {r[1] for r in db.execute('PRAGMA table_info(workflows)')}:
                db.execute('ALTER TABLE workflows ADD COLUMN deleted_at TEXT')

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                yield db

    def _no_secrets(self, value):
        encoded = json.dumps(value, ensure_ascii=False)
        if any(secret in encoded or json.dumps(secret, ensure_ascii=False)[1:-1] in encoded for secret in self.secrets):
            raise ValueError('流程内容包含配置凭据，不能保存')

    def draft(self):
        with self.connection() as db:
            row = db.execute('SELECT * FROM drafts WHERE site_key=? AND active=1', (self.scope,)).fetchone()
            steps = [dict(r) for r in db.execute('SELECT * FROM draft_steps WHERE draft_id=? ORDER BY id', (row['id'],))]
        for step in steps:
            step['action'] = json.loads(step['action'])
        return {'id': row['id'], 'revision': f'{row["id"]}:{row["revision"]}', 'steps': steps, 'warnings': json.loads(row['warnings'])}

    def reset_draft(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE drafts SET active=0 WHERE site_key=?', (self.scope,))
            db.execute('INSERT INTO drafts(id,site_key,active) VALUES(?,?,1)', (uuid4().hex, self.scope))
        return self.draft()

    def record(self, action: BrowserAction, command_id: str, instruction: str, url_before: str, url_after: str | None = None):
        if site_key(url_before) != self.scope:
            raise ValueError('录制页面不属于当前系统')
        if url_after is not None and site_key(url_after) != self.scope:
            raise ValueError('跨站操作暂不支持保存为标准流程')
        data = action.model_dump()
        if action.kind == 'navigate':
            if site_key(action.value) != self.scope:
                raise ValueError('跨站导航暂不支持保存为标准流程')
            data['value'] = clean_entry_url(data['value'])
        url = clean_entry_url(url_before)
        self._no_secrets([data, instruction, url])
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            draft = db.execute('SELECT id FROM drafts WHERE site_key=? AND active=1', (self.scope,)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM draft_steps WHERE draft_id=?', (draft['id'],)).fetchone()[0]
            if count >= 180:
                raise ValueError('录制步骤过多，请拆分流程')
            db.execute('INSERT INTO draft_steps(draft_id,action,command_id,instruction,url_before) VALUES(?,?,?,?,?)',
                       (draft['id'], json.dumps(data, ensure_ascii=False), command_id, instruction, url))
            db.execute('UPDATE drafts SET revision=revision+1 WHERE id=?', (draft['id'],))

    def warn(self, message: str):
        self._no_secrets(message)
        draft = self.draft()
        warnings = (draft['warnings'] + [message])[-20:]
        with self.connection() as db:
            db.execute('UPDATE drafts SET warnings=?,revision=revision+1 WHERE id=?', (json.dumps(warnings, ensure_ascii=False), draft['id']))

    def proposal(self):
        draft = self.draft()
        if not draft['steps']:
            raise ValueError('请先成功执行至少一个操作')
        parameters, steps = [], []
        verbs = {'click': '点击', 'fill': '填写', 'select': '选择', 'wait': '等待', 'download': '导出', 'navigate': '打开页面', 'press': '回车提交'}
        for record in draft['steps']:
            action = BrowserAction.model_validate(record['action'])
            identity = str(record['id'])
            label = action.target.value if action.target else '页面'
            parameter = None
            if action.kind in ('fill', 'select'):
                parameter = f'param_{identity}'
                parameters.append(WorkflowParameter(key=parameter, label=label[:80], default=action.value))
            if action.kind == 'download':
                steps.append(WorkflowStep(id='condition_' + identity, name='暂无数据则结束',
                    condition=ConditionSpec(target={'kind': 'text', 'value': '暂无数据'})))
            steps.append(WorkflowStep(id='action_' + identity, name=(verbs[action.kind] + label)[:120], action=action, value_parameter=parameter))
        definition = WorkflowDefinition(name='新标准流程', site_key=self.scope, entry_url=draft['steps'][0]['url_before'], parameters=parameters, steps=steps)
        return {'definition': definition.model_dump(), 'revision': draft['revision'], 'warnings': draft['warnings'], 'suggestion_source': 'recording', 'model_calls': 0}

    def _validated(self, definition, confirmed):
        if confirmed is not True:
            raise ValueError('请确认流程步骤与参数后保存')
        definition = WorkflowDefinition.model_validate(definition.model_dump())
        if definition.site_key != self.scope:
            raise ValueError('流程不属于当前系统')
        self._no_secrets(definition.model_dump())
        return definition

    def create(self, definition, confirmed=False, source_revision=None):
        definition = self._validated(definition, confirmed)
        identity = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if source_revision:
                draft = db.execute('SELECT id,revision FROM drafts WHERE site_key=? AND active=1', (self.scope,)).fetchone()
                if source_revision != f'{draft["id"]}:{draft["revision"]}':
                    raise VersionConflict('录制草稿已变化，请重新生成并检查')
            db.execute('INSERT INTO workflows(id,site_key,created_at) VALUES(?,?,?)', (identity, self.scope, now))
            db.execute('INSERT INTO workflow_versions VALUES(?,?,?,?)', (identity, 1, definition.model_dump_json(), now))
        return self.get(identity)

    def update(self, flow_id, definition, confirmed=False, expected_version=None):
        definition = self._validated(definition, confirmed)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT w.site_key,MAX(v.version) AS version FROM workflows w JOIN workflow_versions v ON w.id=v.flow_id WHERE w.id=? AND w.deleted_at IS NULL GROUP BY w.id', (flow_id,)).fetchone()
            if not row:
                raise KeyError(flow_id)
            if row['site_key'] != definition.site_key:
                raise ValueError('不能更换已有流程所属系统')
            if row['version'] != expected_version:
                raise VersionConflict('流程已有新版本，请重新打开编辑')
            db.execute('INSERT INTO workflow_versions VALUES(?,?,?,?)', (flow_id, row['version'] + 1, definition.model_dump_json(), datetime.now(timezone.utc).isoformat()))
        return self.get(flow_id)

    def get(self, flow_id, version=None):
        with self.connection() as db:
            if not db.execute('SELECT id FROM workflows WHERE id=? AND deleted_at IS NULL', (flow_id,)).fetchone():
                raise KeyError(flow_id)
            if version is None:
                row = db.execute('SELECT * FROM workflow_versions WHERE flow_id=? ORDER BY version DESC LIMIT 1', (flow_id,)).fetchone()
            else:
                row = db.execute('SELECT * FROM workflow_versions WHERE flow_id=? AND version=?', (flow_id, version)).fetchone()
            versions = [r[0] for r in db.execute('SELECT version FROM workflow_versions WHERE flow_id=? ORDER BY version', (flow_id,))]
        if not row:
            raise KeyError(flow_id)
        return {'id': flow_id, 'version': row['version'], 'versions': versions, 'definition': json.loads(row['definition']), 'created_at': row['created_at']}

    def list(self):
        with self.connection() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM workflows WHERE deleted_at IS NULL ORDER BY created_at DESC')]
        return [{'id': record['id'], 'version': record['version'], 'name': record['definition']['name'],
                 'site_key': record['definition']['site_key']} for record in (self.get(identity) for identity in ids)]

    def delete(self, identity, expected_version):
        from ..sessions import BusyError
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id FROM workflows WHERE id=? AND deleted_at IS NULL', (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            latest = db.execute('SELECT MAX(version) FROM workflow_versions WHERE flow_id=?', (identity,)).fetchone()[0]
            if latest != expected_version:
                raise VersionConflict('流程已有更新，请重新打开后确认删除全部版本')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                if db.execute("SELECT 1 FROM tasks WHERE flow_id=? AND status NOT IN ('completed','failed','stopped','interrupted') LIMIT 1", (identity,)).fetchone():
                    raise BusyError('流程仍有未结束任务，请先停止任务')
            db.execute('UPDATE workflows SET deleted_at=? WHERE id=?', (datetime.now(timezone.utc).isoformat(), identity))
