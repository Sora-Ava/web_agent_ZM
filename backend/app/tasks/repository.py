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
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',
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
            task['model_calls'] = 0
            task['rows'] = []
            steps, files = {}, {}
            for step in db.execute('SELECT row_index,record FROM task_steps WHERE task_id=? ORDER BY id', (identity,)):
                steps.setdefault(step['row_index'], []).append(json.loads(step['record']))
            for file in db.execute('SELECT id,row_index FROM exported_files WHERE task_id=? ORDER BY rowid', (identity,)):
                files.setdefault(file['row_index'], []).append(file['id'])
            for record in db.execute('SELECT * FROM task_rows WHERE task_id=? ORDER BY row_index', (identity,)):
                row = dict(record)
                row['parameters'] = json.loads(row['parameters'])
                row['result'] = json.loads(row['result']) if row['result'] else None
                row['steps'] = steps.get(row['row_index'], [])
                row['file_ids'] = files.get(row['row_index'], [])
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
                    {'name': json.loads(record['definition'])['name'], 'counts': counts, 'total': sum(counts.values()), 'model_calls': 0})
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
