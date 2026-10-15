from app.workflows.models import WorkflowDefinition, site_key


def sample_workflow(url, name='生产导出'):
    return WorkflowDefinition.model_validate({
        'name': name, 'site_key': site_key(url), 'entry_url': url + '/demo/home',
        'parameters': [{'key': 'batch', 'label': '生产批次号', 'default': '001'},
                       {'key': 'level', 'label': '物料层级', 'default': '2'}],
        'steps': [
            {'id': 'entry', 'name': '进入生产查询', 'action': {'kind': 'click', 'target': {'kind': 'role', 'role': 'button', 'value': '生产查询'}}},
            {'id': 'batch', 'name': '填写批次号', 'value_parameter': 'batch', 'action': {'kind': 'fill', 'target': {'kind': 'label', 'value': '生产批次号'}, 'value': '001'}},
            {'id': 'level', 'name': '选择层级', 'value_parameter': 'level', 'action': {'kind': 'select', 'target': {'kind': 'label', 'value': '物料层级'}, 'value': '2'}},
            {'id': 'query', 'name': '查询', 'action': {'kind': 'click', 'target': {'kind': 'role', 'role': 'button', 'value': '查询'}, 'wait_for_query': True}},
            {'id': 'empty', 'name': '暂无数据判断', 'condition': {'target': {'kind': 'text', 'value': '暂无数据'}, 'when_true': 'no_data', 'when_false': 'continue'}},
            {'id': 'export', 'name': '导出', 'action': {'kind': 'download', 'target': {'kind': 'role', 'role': 'button', 'value': '导出'}}},
        ]})
