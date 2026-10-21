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


def sample_with_login(url, name='生产导出'):
    from app.workflows.models import WorkflowStep,LoginSpec
    definition=sample_workflow(url,name)
    definition.steps.insert(0,WorkflowStep(id='login',name='显式登录',login=LoginSpec(url=url+'/demo/login',credential_ref='test',username={'kind':'css','value':'#username'},password={'kind':'css','value':'#password'},submit={'kind':'css','value':'#login'},success={'kind':'css','value':'#logged-in'})))
    definition.steps.insert(1,WorkflowStep(id='home',name='进入业务首页',action={'kind':'navigate','value':url+'/demo/home'}))
    return definition
