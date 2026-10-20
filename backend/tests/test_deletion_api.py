import httpx

from app.main import create_app
from workflow_samples import sample_workflow


async def test_confirmed_deletion_and_last_run_snapshot_remains_valid(session):
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        response = await c.post(f'/api/workflows/{flow["id"]}/runs', json={'parameters': {'batch': 'DEL'}})
        identity = response.json()['id']
        await app.state.workflows.task
        file_id = app.state.tasks.repository.get(identity)['rows'][0]['file_ids'][0]
        assert (await c.request('DELETE', '/api/tasks/'+identity, json={'confirmed':False})).status_code == 422
        assert (await c.request('DELETE', '/api/tasks/'+identity, json={'confirmed':True})).status_code == 200
        assert (await c.get('/api/session')).status_code == 200
        assert (await c.get('/api/files/'+file_id+'/download')).status_code == 200
        assert (await c.request('DELETE', '/api/files/'+file_id, json={'confirmed':True})).status_code == 200
        assert (await c.get('/api/files/'+file_id+'/download')).status_code == 404
        assert (await c.request('DELETE', '/api/workflows/'+flow['id'], json={'confirmed':True,'expected_version':1})).status_code == 200
        assert (await c.get('/api/workflows')).json() == []
        assert (await c.get('/api/tasks')).json() == []
    await app.state.tasks.shutdown()


async def test_queued_association_blocks_flow_and_task_deletion(session):
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    app.state.tasks.authoring = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        submitted = await c.post('/api/tasks', json={'flow_id':flow['id'],'rows':[{'batch':'WAIT'}]})
        identity = submitted.json()['id']
        assert (await c.request('DELETE', '/api/tasks/'+identity, json={'confirmed':True})).status_code == 409
        assert (await c.request('DELETE', '/api/workflows/'+flow['id'], json={'confirmed':True,'expected_version':1})).status_code == 409
        assert (await c.request('DELETE', '/api/files/unknown', json={'confirmed':True})).status_code == 404
    await app.state.tasks.shutdown()

async def test_locked_export_retains_file_and_registration(session, monkeypatch):
    from pathlib import Path
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as c:
        response = await c.post(f'/api/workflows/{flow["id"]}/runs', json={'parameters': {'batch': 'LOCK'}})
        await app.state.workflows.task
        file_id = app.state.tasks.repository.get(response.json()['id'])['rows'][0]['file_ids'][0]
        item = session.downloads.get(file_id)
        original = item.path.read_bytes()
        replace = Path.replace
        def locked(path, target):
            if path == item.path:
                raise PermissionError('synthetic occupied file')
            return replace(path, target)
        monkeypatch.setattr(Path, 'replace', locked)
        response = await c.request('DELETE', '/api/files/'+file_id, json={'confirmed': True})
        assert response.status_code == 409
        assert '重试' in response.json()['detail']
        assert item.path.read_bytes() == original
        assert session.downloads.get(file_id) is not None
        assert (await c.get('/api/files/'+file_id+'/download')).status_code == 200
    await app.state.tasks.shutdown()
