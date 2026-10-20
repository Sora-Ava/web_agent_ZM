import json
from pathlib import Path

import pytest

from app.workflows.repository import WorkflowRepository, VersionConflict
from app.workflows.models import site_key
from app.tasks.repository import TaskRepository
from app.downloads import DownloadStore, ExportedFile
from app.sessions import BusyError
from workflow_samples import sample_workflow


def setup(tmp_path):
    flows = WorkflowRepository(tmp_path / 'agent.db', site_key('http://site.test'))
    flow = flows.create(sample_workflow('http://site.test'), True)
    tasks = TaskRepository(flows)
    task = tasks.create(flow, [{'batch': 'P1'}])
    tasks.update_task(task['id'], 'completed')
    path = tmp_path / 'exports'
    path.mkdir()
    file = path / 'unique.csv'
    file.write_bytes(b'P1,2,120')
    tasks.register_file({'id': 'file', 'path': str(file), 'name': file.name, 'size': file.stat().st_size,
        'task_id': task['id'], 'row_index': 0, 'original_name': 'production.csv'})
    store = DownloadStore(path)
    store.bind_repository(tasks)
    return flows, tasks, flow, task, store


def test_flow_archive_keeps_snapshot_and_rejects_active_or_stale(tmp_path):
    flows, tasks, flow, task, _ = setup(tmp_path)
    with pytest.raises(VersionConflict):
        flows.delete(flow['id'], 2)
    tasks.update_task(task['id'], 'paused')
    with pytest.raises(BusyError):
        flows.delete(flow['id'], 1)
    tasks.update_task(task['id'], 'stopped')
    flows.delete(flow['id'], 1)
    assert flows.list() == [] and tasks.get(task['id'])['definition'] == flow['definition']
    with pytest.raises(KeyError):
        flows.get(flow['id'])
    with pytest.raises(KeyError):
        flows.update(flow['id'], sample_workflow('http://site.test'), True, 1)


def test_task_delete_keeps_and_detaches_export(tmp_path):
    _, tasks, _, task, store = setup(tmp_path)
    tasks.update_task(task['id'], 'queued')
    with pytest.raises(BusyError):
        tasks.delete(task['id'])
    tasks.update_task(task['id'], 'completed')
    tasks.delete(task['id'])
    with pytest.raises(KeyError):
        tasks.get(task['id'])
    reopened = DownloadStore(store.directory)
    reopened.bind_repository(tasks)
    assert reopened.get('file').path.is_file()
    assert reopened.get('file').task_id is None


def test_file_delete_moves_to_trash_and_persists_deleted_history(tmp_path):
    _, tasks, _, task, store = setup(tmp_path)
    path = store.get('file').path
    store.delete('file')
    assert not path.exists() and (store.directory / '.trash' / path.name).read_bytes() == b'P1,2,120'
    assert store.list() == [] and store.get('file') is None
    reopened = DownloadStore(store.directory)
    reopened.bind_repository(tasks)
    assert reopened.list() == []
    row = tasks.get(task['id'])['rows'][0]
    assert row['file_ids'] == [] and row['deleted_files'][0]['id'] == 'file'


def test_file_delete_rejects_activity_and_outside_path(tmp_path):
    _, tasks, _, task, store = setup(tmp_path)
    tasks.update_task(task['id'], 'paused')
    with pytest.raises(BusyError):
        store.delete('file')
    tasks.update_task(task['id'], 'completed')
    outside = tmp_path / 'outside.csv'
    outside.write_bytes(b'keep')
    tasks.register_file({'id': 'bad', 'path': str(outside), 'name': outside.name, 'size': 4, 'task_id': None, 'row_index': None})
    store.files['bad'] = ExportedFile('bad', outside.name, outside, 4)
    with pytest.raises(ValueError):
        store.delete('bad')
    assert outside.read_bytes() == b'keep'
    store.get('file').path.unlink()
    store.delete('file')
    assert store.get('file') is None
