import asyncio

import httpx

from app.main import create_app
from app.downloads import DownloadStore
from workflow_samples import sample_workflow


async def test_queue_authoring_validation_history_and_download_reopen(session):
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        await c.post('/api/session/start')
        bad = await c.post('/api/tasks', json={'flow_id': flow['id'], 'rows': [{'batch': 'OK'}, {'batch': ''}]})
        assert bad.status_code == 422
        assert (await c.get('/api/tasks')).json() == []
        response = await c.post('/api/tasks', json={'flow_id': flow['id'], 'version': 1, 'rows': [{'batch': 'API3', 'level': '2'}]})
        assert response.status_code == 202
        identity = response.json()['id']
        await asyncio.sleep(.1)
        assert app.state.tasks.repository.get(identity)['status'] == 'queued'
        assert (await c.get('/api/session')).json()['queue']['authoring']
        assert (await c.post('/api/queue/release-authoring')).status_code == 200
        await app.state.tasks.dispatcher
        task = (await c.get('/api/tasks/' + identity)).json()
        assert task['status'] == 'completed' and task['rows'][0]['status'] == 'success'
        assert (await c.post(f'/api/tasks/{identity}/pause')).status_code == 409
        assert (await c.get('/api/tasks/not-found')).status_code == 404
        file_id = task['rows'][0]['result']['file_ids'][0]
        metadata = (await c.get('/api/files')).json()[0]
        assert metadata['task_id'] == identity and metadata['row_index'] == 0
        session.downloads = DownloadStore(session.settings.download_dir)
        session.downloads.bind_repository(app.state.tasks.repository)
        content = await c.get(f'/api/files/{file_id}/download')
        assert content.status_code == 200 and 'API3,2,120' in content.content.decode('utf-8-sig')
        session.downloads.get(file_id).path.unlink()
        assert (await c.get(f'/api/files/{file_id}/download')).status_code == 404
    await app.state.tasks.shutdown()


async def test_queued_controls_and_foreign_site_rejection(session):
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    app.state.tasks.authoring = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        response = await c.post('/api/tasks', json={'flow_id': flow['id'], 'rows': [{'batch': 'P1'}]})
        identity = response.json()['id']
        assert (await c.post(f'/api/tasks/{identity}/pause')).json()['status'] == 'paused'
        assert (await c.post(f'/api/tasks/{identity}/resume')).json()['status'] == 'queued'
        assert (await c.post(f'/api/tasks/{identity}/stop')).json()['status'] == 'stopped'
        assert (await c.get('/api/tasks')).json()[0]['counts'] == {'skipped': 1}
        assert (await c.post('/api/tasks', json={'flow_id': flow['id'], 'rows': [{'batch': 123}]})).status_code == 422
    await app.state.tasks.shutdown()
