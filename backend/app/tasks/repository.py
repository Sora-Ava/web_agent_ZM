import json
from datetime import datetime, timezone
from uuid import uuid4

from ..workflows.models import WorkflowDefinition, validate_parameters


def now():
    return datetime.now(timezone.utc).isoformat()


TERMINAL = {'stopped', 'completed', 'failed', 'interrupted'}


class TaskRepository:
    def __init__(self, workflows):
        self.workflows = workflows
        self.connection = workflows.connection
        with self.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, version INTEGER NOT NULL,
                definition TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS task_rows(task_id TEXT NOT NULL, row_index INTEGER NOT NULL,
                parameters TEXT NOT NULL, status TEXT NOT NULL, result TEXT, PRIMARY KEY(task_id,row_index));
              CREATE TABLE IF NOT EXISTS task_steps(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                row_index INTEGER NOT NULL, record TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS exported_files(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS task_steps_lookup ON task_steps(task_id,row_index,id);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(exported_files)')}
            if 'task_id' not in columns:
                db.execute('ALTER TABLE exported_files ADD COLUMN task_id TEXT')
                db.execute('ALTER TABLE exported_files ADD COLUMN row_index INTEGER')
                for row in db.execute('SELECT id,metadata FROM exported_files').fetchall():
                    metadata = json.loads(row['metadata'])
                    db.execute('UPDATE exported_files SET task_id=?,row_index=? WHERE id=?',
                        (metadata.get('task_id'), metadata.get('row_index'), row['id']))
            db.execute('CREATE INDEX IF NOT EXISTS task_files_lookup ON exported_files(task_id,row_index)')
            if 'model_calls' not in {row[1] for row in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0')
            for column in ('elapsed_s', 'model_elapsed_s'):
                if column not in {row[1] for row in db.execute('PRAGMA table_info(tasks)')}:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {column} REAL NOT NULL DEFAULT 0')
                    if column == 'model_elapsed_s':
                        totals = {}
                        for row in db.execute('SELECT task_id,record FROM task_steps'):
                            record = json.loads(row['record'])
                            if record.get('model_calls'):
                                totals[row['task_id']] = totals.get(row['task_id'], 0) + max(0, record.get('elapsed_s', 0))
                        db.executemany('UPDATE tasks SET model_elapsed_s=? WHERE id=?', [(value, identity) for identity, value in totals.items()])

    def create(self, record, rows):
        definition = WorkflowDefinition.model_validate(record['definition'])
        if definition.site_key != self.workflows.scope:
            raise ValueError('当前网站与流程所属系统不一致')
        if not 1 <= len(rows) <= 500:
            raise ValueError('每个任务需要 1 至 500 行参数')
        values = []
        for index, row in enumerate(rows):
            try:
                value = validate_parameters(definition, row)
                self.workflows._no_secrets(value)
                values.append(value)
            except ValueError as exc:
                raise ValueError(f'第 {index + 1} 行：{exc}') from None
        identity, stamp = uuid4().hex, now()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM workflows WHERE id=? AND deleted_at IS NULL', (record['id'],)).fetchone():
                raise KeyError(record['id'])
            db.execute('INSERT INTO tasks(id,flow_id,version,definition,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (identity, record['id'], record['version'], definition.model_dump_json(), 'queued', stamp, stamp))
            db.executemany('INSERT INTO task_rows VALUES(?,?,?,?,NULL)',
                [(identity, i, json.dumps(v, ensure_ascii=False), 'queued') for i, v in enumerate(values)])
        return self.get(identity)

    def get(self, identity):
        with self.connection() as db:
            record = db.execute('SELECT * FROM tasks WHERE id=?', (identity,)).fetchone()
            if not record:
                raise KeyError(identity)
            task = dict(record)
            task['definition'] = json.loads(task['definition'])
            task['name'] = task['definition']['name']
            task['rows'] = []
            steps, files, deleted_files = {}, {}, {}
            for step in db.execute('SELECT row_index,record FROM task_steps WHERE task_id=? ORDER BY id', (identity,)):
                steps.setdefault(step['row_index'], []).append(json.loads(step['record']))
            for file in db.execute('SELECT id,row_index,metadata FROM exported_files WHERE task_id=? ORDER BY rowid', (identity,)):
                metadata = json.loads(file['metadata'])
                if metadata.get('deleted'):
                    deleted_files.setdefault(file['row_index'], []).append({'id': file['id'], 'name': metadata['name']})
                else:
                    files.setdefault(file['row_index'], []).append(file['id'])
            for record in db.execute('SELECT * FROM task_rows WHERE task_id=? ORDER BY row_index', (identity,)):
                row = dict(record)
                row['parameters'] = json.loads(row['parameters'])
                row['result'] = json.loads(row['result']) if row['result'] else None
                row['steps'] = steps.get(row['row_index'], [])
                row['file_ids'] = files.get(row['row_index'], [])
                row['deleted_files'] = deleted_files.get(row['row_index'], [])
                task['rows'].append(row)
        return task

    def status(self, identity):
        with self.connection() as db:
            row = db.execute('SELECT status FROM tasks WHERE id=?', (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            return row[0]

    def list(self, limit=50):
        with self.connection() as db:
            records = db.execute('SELECT * FROM tasks ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
            result = []
            for record in records:
                counts = {r[0]: r[1] for r in db.execute('SELECT status,COUNT(*) FROM task_rows WHERE task_id=? GROUP BY status', (record['id'],))}
                result.append({k: record[k] for k in ('id', 'flow_id', 'version', 'status', 'created_at', 'updated_at')} |
                    {'name': json.loads(record['definition'])['name'], 'counts': counts, 'total': sum(counts.values()), 'model_calls': record['model_calls'],
                        'elapsed_s': record['elapsed_s'], 'model_elapsed_s': record['model_elapsed_s']})
        return result

    def next_queued(self):
        with self.connection() as db:
            row = db.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY rowid LIMIT 1").fetchone()
            return row[0] if row else None

    def update_task(self, identity, status):
        with self.connection() as db:
            db.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?', (status, now(), identity))

    def update_row(self, identity, index, *, status, result=None):
        with self.connection() as db:
            db.execute('UPDATE task_rows SET status=?,result=? WHERE task_id=? AND row_index=?',
                (status, json.dumps(result, ensure_ascii=False) if result is not None else None, identity, index))

    def add_step(self, identity, index, record):
        self.workflows._no_secrets(record)
        with self.connection() as db:
            db.execute('INSERT INTO task_steps(task_id,row_index,record) VALUES(?,?,?)',
                (identity, index, json.dumps(record, ensure_ascii=False)))
            if record.get('model_calls'):
                db.execute('UPDATE tasks SET model_calls=model_calls+? WHERE id=?', (record['model_calls'], identity))
                db.execute('UPDATE tasks SET model_elapsed_s=model_elapsed_s+? WHERE id=?', (max(0, record.get('elapsed_s', 0)), identity))

    def update_timing(self, identity, elapsed_s):
        with self.connection() as db:
            db.execute('UPDATE tasks SET elapsed_s=? WHERE id=?', (elapsed_s, identity))

    def finish_remaining(self, identity, status):
        with self.connection() as db:
            db.execute("UPDATE task_rows SET status=? WHERE task_id=? AND status IN ('queued','running')", (status, identity))

    def interrupt_unfinished(self):
        with self.connection() as db:
            db.execute("UPDATE task_rows SET status='interrupted' WHERE status IN ('queued','running')")
            db.execute("UPDATE tasks SET status='interrupted',updated_at=? WHERE status NOT IN ('stopped','completed','failed','interrupted')", (now(),))

    def register_file(self, metadata):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO exported_files(id,metadata,task_id,row_index) VALUES(?,?,?,?)',
                (metadata['id'], json.dumps(metadata, ensure_ascii=False), metadata.get('task_id'), metadata.get('row_index')))

    def files(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT metadata FROM exported_files ORDER BY rowid')]

    def get_file(self, identity):
        with self.connection() as db:
            row = db.execute('SELECT metadata FROM exported_files WHERE id=?', (identity,)).fetchone()
            return json.loads(row[0]) if row else None

    def delete(self, identity):
        from ..sessions import BusyError
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM tasks WHERE id=?', (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            if row['status'] not in TERMINAL:
                raise BusyError('只能删除已结束任务，请先停止任务')
            for file in db.execute('SELECT id,metadata FROM exported_files WHERE task_id=?', (identity,)).fetchall():
                metadata = json.loads(file['metadata'])
                metadata.update(task_id=None, row_index=None)
                db.execute('UPDATE exported_files SET metadata=?,task_id=NULL,row_index=NULL WHERE id=?', (json.dumps(metadata, ensure_ascii=False), file['id']))
            for table in ('task_rows', 'task_steps'):
                db.execute(f'DELETE FROM {table} WHERE task_id=?', (identity,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='repairs'").fetchone():
                db.execute('DELETE FROM repairs WHERE task_id=?', (identity,))
            db.execute('DELETE FROM tasks WHERE id=?', (identity,))

    def recycle_file(self, identity, move):
        from ..sessions import BusyError
        undo = None
        try:
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT metadata,task_id FROM exported_files WHERE id=?', (identity,)).fetchone()
                if not row or json.loads(row['metadata']).get('deleted'):
                    raise KeyError(identity)
                if row['task_id']:
                    task = db.execute('SELECT status FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
                    if task and task['status'] not in TERMINAL:
                        raise BusyError('所属任务尚未结束，不能删除文件')
                metadata = json.loads(row['metadata'])
                trash_path, undo = move(metadata)
                metadata.update(deleted=True, deleted_at=now(), trash_path=trash_path)
                db.execute('UPDATE exported_files SET metadata=? WHERE id=?', (json.dumps(metadata, ensure_ascii=False), identity))
        except BaseException:
            if undo:
                undo()
            raise
