import asyncio
import json
from types import SimpleNamespace

import httpx

from app.main import create_app
from app.api import screen
from workflow_samples import sample_workflow


async def test_startup_interrupts_all_unfinished_states_and_preserves_results(session):
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]),True)
    ids=[]
    for state in ('queued','running','paused','pausing','stopping'):
        task = app.state.tasks.repository.create(flow,[{'batch':state}])
        app.state.tasks.repository.update_task(task['id'],state)
        ids.append(task['id'])
    async with app.router.lifespan_context(app):
        assert all(app.state.tasks.repository.status(i)=='interrupted' for i in ids)
        assert all(app.state.tasks.repository.get(i)['rows'][0]['status']=='interrupted' for i in ids)


async def test_screen_disconnect_exits_even_with_no_frames(session):
    app = create_app(session.settings, session)
    class WebSocket:
        app = None
        headers = {}
        async def accept(self): pass
        async def receive(self): return {'type':'websocket.disconnect'}
        async def send_bytes(self,frame): raise AssertionError('no frame expected')
        async def close(self,**kwargs): pass
    ws=WebSocket();ws.app=app
    async with asyncio.timeout(.3):
        await screen(ws)
    assert app.state.screen_clients==0


async def test_diagnostics_only_exposes_safe_readiness(session):
    app=create_app(session.settings,session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        response=await c.get('/api/diagnostics')
        assert response.status_code==200
        data=response.json()
        assert data['site_type']=='generic' and data['browser']['installed']
        encoded=json.dumps(data)
        assert session.settings.credentials.resolve('test',session.settings.site_url).password.get_secret_value() not in encoded
        assert 'llm_api_key' not in encoded and 'site_username' not in encoded


async def test_task_model_and_total_timing_persist(session):
    app=create_app(session.settings,session)
    flow=app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]),True)
    identity=await app.state.tasks.submit(flow['id'],1,[{'batch':'TIMING'}])
    await app.state.tasks.dispatcher
    task=app.state.tasks.repository.get(identity)
    assert task['elapsed_s']>0 and task['model_elapsed_s']==0
    assert app.state.tasks.repository.list()[0]['elapsed_s']>0
    app.state.tasks.repository.add_step(identity,0,{'id':'mock','model_calls':1,'elapsed_s':.25,'result':{'status':'success'}})
    assert app.state.tasks.repository.get(identity)['model_elapsed_s']==.25
    await app.state.tasks.shutdown()


def test_upgrade_backfills_existing_recovery_timings(tmp_path):
    from app.workflows.repository import WorkflowRepository
    from app.workflows.models import site_key
    from app.tasks.repository import TaskRepository
    flows=WorkflowRepository(tmp_path/'old.db',site_key('http://site.test'))
    flow=flows.create(sample_workflow('http://site.test'),True)
    with flows.connection() as db:
        db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,flow_id TEXT,version INTEGER,definition TEXT,status TEXT,created_at TEXT,updated_at TEXT,model_calls INTEGER DEFAULT 0)')
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',('old',flow['id'],1,json.dumps(flow['definition']),'completed','old','old',1))
        db.execute('CREATE TABLE task_steps(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,row_index INTEGER,record TEXT)')
        db.execute('INSERT INTO task_steps(task_id,row_index,record) VALUES(?,?,?)',('old',0,json.dumps({'model_calls':1,'elapsed_s':2.5})))
    repo=TaskRepository(flows)
    assert repo.get('old')['model_calls']==1 and repo.get('old')['model_elapsed_s']==2.5
