import json
from uuid import uuid4

from ..contracts import LocatorSpec
from ..tasks.repository import TERMINAL, now
from ..workflows.models import WorkflowDefinition
from ..workflows.repository import VersionConflict


class RepairRepository:
    def __init__(self, tasks):
        self.tasks = tasks
        self.connection = tasks.connection
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS repairs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                row_index INTEGER NOT NULL, step_id TEXT NOT NULL, detail TEXT NOT NULL, replacement TEXT NOT NULL,
                created_at TEXT NOT NULL, approved_version INTEGER, UNIQUE(task_id,step_id,replacement));
                CREATE INDEX IF NOT EXISTS repairs_task ON repairs(task_id);''')

    def list(self, identity):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM repairs WHERE task_id=? ORDER BY rowid', (identity,)).fetchall()
        return [{**dict(row), **json.loads(row['detail'])} for row in rows]

    def record(self, identity, index, detail):
        task = self.tasks.get(identity)
        step = next((s for s in task['definition']['steps'] if s['id'] == detail['step_id']), None)
        replacement = LocatorSpec.model_validate(detail['replacement'])
        if not step or not step['action'] or step['action']['target'] != detail['original'] or replacement.kind == 'css':
            raise ValueError('修复必须仅变更任务快照中的动作语义定位')
        self.tasks.workflows._no_secrets(detail)
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO repairs VALUES(?,?,?,?,?,?,?,NULL)',
                (uuid4().hex, identity, index, detail['step_id'], json.dumps(detail, ensure_ascii=False), replacement.model_dump_json(), now()))
        return next(r for r in self.list(identity) if r['step_id'] == detail['step_id'] and r['replacement'] == detail['replacement'])

    def approve(self, identity, ids, expected_version, confirmed):
        if confirmed is not True or not ids or len(ids) != len(set(ids)):
            raise ValueError('请选择修复并明确确认保存')
        task = self.tasks.get(identity)
        if task['status'] not in TERMINAL:
            raise VersionConflict('请等待任务结束后审核修复')
        selected = [r for r in self.list(identity) if r['id'] in ids]
        if len(selected) != len(ids) or any(r['approved_version'] for r in selected):
            raise VersionConflict('修复不存在或已经审核，请刷新')
        if len({r['step_id'] for r in selected}) != len(selected):
            raise ValueError('同一步骤只能选择一个定位修复')
        definition = WorkflowDefinition.model_validate(task['definition'])
        for repair in selected:
            step = next(s for s in definition.steps if s.id == repair['step_id'])
            step.action.target = LocatorSpec.model_validate(repair['replacement'])
        definition = self.tasks.workflows._validated(definition, True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT MAX(version) FROM workflow_versions WHERE flow_id=?', (task['flow_id'],)).fetchone()[0]
            if latest != expected_version or latest != task['version']:
                raise VersionConflict('流程已有新版本，不能覆盖；请检查最新版本后手动编辑')
            # Recheck approvals under the same lock as the version insertion.
            for repair in selected:
                approved = db.execute('SELECT approved_version FROM repairs WHERE id=?', (repair['id'],)).fetchone()[0]
                if approved:
                    raise VersionConflict('修复已经审核，请刷新')
            version = latest + 1
            db.execute('INSERT INTO workflow_versions VALUES(?,?,?,?)', (task['flow_id'], version, definition.model_dump_json(), now()))
            db.executemany('UPDATE repairs SET approved_version=? WHERE id=?', [(version, r['id']) for r in selected])
        return self.tasks.workflows.get(task['flow_id'])
