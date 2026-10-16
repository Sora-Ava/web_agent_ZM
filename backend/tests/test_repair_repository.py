import pytest

from app.workflows.repository import WorkflowRepository, VersionConflict
from app.workflows.models import site_key
from app.tasks.repository import TaskRepository
from app.recovery.repository import RepairRepository
from workflow_samples import sample_workflow


def setup(tmp_path):
    flows = WorkflowRepository(tmp_path / 'agent.db', site_key('http://site.test'))
    flow = flows.create(sample_workflow('http://site.test'), True)
    tasks = TaskRepository(flows)
    task = tasks.create(flow, [{'batch': 'P1', 'level': '1'}])
    return flows, tasks, task, RepairRepository(tasks)


def repair(value='下载报表'):
    return {'step_id': 'export', 'step_name': '导出', 'original': {'kind': 'role', 'role': 'button', 'value': '导出'},
            'replacement': {'kind': 'role', 'role': 'button', 'value': value}, 'reason': '定位变化'}


def test_approve_creates_immutable_version_and_persists_audit(tmp_path):
    flows, tasks, task, repo = setup(tmp_path)
    entry = repo.record(task['id'], 0, repair())
    tasks.update_task(task['id'], 'completed')
    with pytest.raises(ValueError):
        repo.approve(task['id'], [entry['id']], 1, False)
    saved = repo.approve(task['id'], [entry['id']], 1, True)
    assert saved['version'] == 2
    assert flows.get(task['flow_id'], 1)['definition']['steps'][-1]['action']['target']['value'] == '导出'
    assert saved['definition']['steps'][-1]['action']['target']['value'] == '下载报表'
    assert tasks.get(task['id'])['definition']['steps'][-1]['action']['target']['value'] == '导出'
    assert RepairRepository(tasks).list(task['id'])[0]['approved_version'] == 2


def test_stale_version_and_conflicting_repairs_rejected_atomically(tmp_path):
    flows, tasks, task, repo = setup(tmp_path)
    a = repo.record(task['id'], 0, repair('下载一'))
    b = repo.record(task['id'], 0, repair('下载二'))
    tasks.update_task(task['id'], 'completed')
    with pytest.raises(ValueError):
        repo.approve(task['id'], [a['id'], b['id']], 1, True)
    assert flows.get(task['flow_id'])['version'] == 1
    flows.update(task['flow_id'], sample_workflow('http://site.test'), True, 1)
    with pytest.raises(VersionConflict):
        repo.approve(task['id'], [a['id']], 1, True)
    assert not repo.list(task['id'])[0]['approved_version']


def test_recovery_request_counts_persist_in_task_snapshot(tmp_path):
    _, tasks, task, _ = setup(tmp_path)
    tasks.add_step(task['id'], 0, {'id': 'query:recovery:1', 'model_calls': 1, 'result': {'status': 'failed'}})
    tasks.add_step(task['id'], 0, {'id': 'query:recovery:2', 'model_calls': 1, 'result': {'status': 'success'}})
    assert TaskRepository(tasks.workflows).get(task['id'])['model_calls'] == 2
    assert tasks.list()[0]['model_calls'] == 2
