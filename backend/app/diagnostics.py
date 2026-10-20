from pathlib import Path

from fastapi import APIRouter, Request
from playwright.async_api import async_playwright

router = APIRouter(prefix='/api')


@router.get('/diagnostics')
async def diagnostics(request: Request):
    c = request.app.state.settings
    installed = False
    try:
        async with async_playwright() as p:
            installed = Path(p.chromium.executable_path).is_file()
    except Exception:
        pass
    return {'site_type': 'generic',
        'login_configured': bool(c.credentials.metadata()),
        'model_configured': bool(c.llm_base_url and c.llm_model),
        'query_wait_configured': bool(c.loading_selector and c.result_selector),
        'browser': {'installed': installed, 'status': request.app.state.session.state().status},
        'paths': {'database': str(c.database_path.resolve()), 'downloads': str(c.download_dir.resolve()), 'trash': str(c.download_dir.resolve()/'.trash')},
        'timeouts': {'action_ms': c.action_timeout_ms, 'query_ms': c.query_timeout_ms, 'download_ms': c.download_timeout_ms, 'model_s': c.model_timeout_s},
        'queue_fault': request.app.state.tasks.fault}
