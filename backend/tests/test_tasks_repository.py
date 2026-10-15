import pytest

from app.workflows.repository import WorkflowRepository
from app.workflows.models import site_key
from workflow_samples import sample_workflow
from app.tasks.repository import TaskRepository


def test_snapshot_validation_and_reopen(tmp_path):
    path = tmp_path / 'agent.sqlite3'
    flows = WorkflowRepository(path, site_key('http://site.test'))
    flow = flows.create(sample_workflow('http://site.test'), confirmed=True)
    tasks = TaskRepository(flows)
    values = [{'batch': '00001', 'level': '2'}]
    task = tasks.create(flow, values)
    values[0]['batch'] = 'changed'
    updated = sample_workflow('http://site.test')
    updated.name = 'new version'
    flows.update(flow['id'], updated, True, 1)
    reopened = TaskRepository(WorkflowRepository(path, site_key('http://site.test')))
    saved = reopened.get(task['id'])
    assert saved['version'] == 1 and saved['definition']['name'] != updated.name
    assert saved['rows'][0]['parameters']['batch'] == '00001'
    with pytest.raises(ValueError):
        tasks.create(flow, [{'batch': 'valid'}, {'batch': ''}])
    with pytest.raises(ValueError):
        tasks.create(flow, [])
    assert len(tasks.list()) == 1


def test_row_steps_and_file_metadata_persist(tmp_path):
    flows = WorkflowRepository(tmp_path / 'data.db', site_key('http://site.test'))
    record = flows.create(sample_workflow('http://site.test'), True)
    repo = TaskRepository(flows)
    task = repo.create(record, [{'batch': 'P1', 'level': '1'}])
    repo.update_task(task['id'], 'running')
    repo.update_row(task['id'], 0, status='running')
    repo.add_step(task['id'], 0, {'id': 'query', 'result': {'status': 'success'}})
    repo.register_file({'id': 'file1', 'name': 'unique.csv', 'path': str(tmp_path / 'unique.csv'), 'size': 10,
                        'original_name': 'data.csv', 'task_id': task['id'], 'row_index': 0})
    repo.update_row(task['id'], 0, status='success', result={'file_ids': ['file1']})
    reopened = TaskRepository(flows)
    row = reopened.get(task['id'])['rows'][0]
    assert row['status'] == 'success' and len(row['steps']) == 1
    assert row['result']['file_ids'] == ['file1']
    assert reopened.get_file('file1')['original_name'] == 'data.csv'
    assert reopened.files()[0]['task_id'] == task['id']


def test_download_association_survives_interruption_before_row_result(tmp_path):
    flows = WorkflowRepository(tmp_path / 'data.db', site_key('http://site.test'))
    record = flows.create(sample_workflow('http://site.test'), True)
    repo = TaskRepository(flows)
    task = repo.create(record, [{'batch': 'PARTIAL', 'level': '1'}])
    repo.update_task(task['id'], 'paused')
    repo.update_row(task['id'], 0, status='running')
    repo.register_file({'id': 'partial', 'name': 'unique.csv', 'original_name': 'data.csv',
        'path': str(tmp_path / 'unique.csv'), 'size': 20, 'task_id': task['id'], 'row_index': 0})
    repo.interrupt_unfinished()
    reopened = TaskRepository(flows)
    row = reopened.get(task['id'])['rows'][0]
    assert row['status'] == 'interrupted' and row['result'] is None
    assert row['file_ids'] == ['partial']
