import pytest
from pydantic import ValidationError

from app.contracts import BrowserAction
from app.workflows.models import WorkflowDefinition, site_key
from app.workflows.repository import WorkflowRepository, VersionConflict
from workflow_samples import sample_workflow


def test_versions_persist_and_cannot_be_overwritten(tmp_path):
    path = tmp_path / 'flows.sqlite3'
    repo = WorkflowRepository(path, site_key('http://example.test'))
    flow = sample_workflow('http://example.test')
    first = repo.create(flow, confirmed=True)
    flow.name = '更名导出'
    second = repo.update(first['id'], flow, confirmed=True, expected_version=1)
    assert second['version'] == 2
    with pytest.raises(VersionConflict):
        repo.update(first['id'], flow, confirmed=True, expected_version=1)
    reopened = WorkflowRepository(path, site_key('http://example.test'))
    assert reopened.get(first['id'], 1)['definition']['name'] == '生产导出'
    assert reopened.get(first['id'])['definition']['name'] == '更名导出'


def test_draft_proposal_bindings_and_no_data_rule(tmp_path):
    repo = WorkflowRepository(tmp_path / 'flows.sqlite3', site_key('http://example.test'))
    for kind, name, value in [('fill', '生产批次号', '0001'), ('select', '物料层级', '2'), ('download', '导出', None)]:
        target = {'kind': 'label', 'value': name} if kind != 'download' else {'kind': 'role', 'role': 'button', 'value': name}
        repo.record(BrowserAction(kind=kind, target=target, value=value), 'command1', '查询导出', 'http://example.test/home')
    proposal = repo.proposal()
    definition = WorkflowDefinition.model_validate(proposal['definition'])
    assert definition.parameters[0].default == '0001'
    assert definition.steps[0].value_parameter == definition.parameters[0].key
    assert definition.steps[-2].condition.when_true == 'no_data'
    assert definition.steps[-1].action.kind == 'download'
    repo.record(BrowserAction(kind='wait', target={'kind': 'text', 'value': '完成'}), 'command2', '等待', 'http://example.test/home')
    with pytest.raises(VersionConflict):
        repo.create(definition, confirmed=True, source_revision=proposal['revision'])


def test_definition_rejects_missing_parameter_and_unconfirmed_save(tmp_path):
    definition = sample_workflow('http://example.test')
    data = definition.model_dump()
    data['parameters'] = []
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(data)
    repo = WorkflowRepository(tmp_path / 'flows.sqlite3', definition.site_key)
    with pytest.raises(ValueError):
        repo.create(definition, confirmed=False)


def test_known_credentials_never_persist_in_draft_or_definition(tmp_path):
    repo = WorkflowRepository(tmp_path / 'flows.sqlite3', site_key('http://example.test'), secrets=['SECRET_CONFIGURED_KEY'])
    with pytest.raises(ValueError):
        repo.record(BrowserAction(kind='fill', target={'kind': 'label', 'value': '批次号'}, value='SECRET_CONFIGURED_KEY'), 'c', '输入', 'http://example.test/home')
    definition = sample_workflow('http://example.test')
    definition.parameters[0].default = 'SECRET_CONFIGURED_KEY'
    with pytest.raises(ValueError):
        repo.create(definition, confirmed=True)
    assert not repo.draft()['steps']
