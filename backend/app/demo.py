"""Local fixture, mounted only on the local service for onboarding and tests."""
import asyncio
import csv
import io

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

demo_app = FastAPI()

STYLE = '<style>body{font:16px system-ui;margin:48px;max-width:900px;color:#16332c}label{display:block;margin:18px 0}input,select,button{font:inherit;padding:10px;margin:4px}button{cursor:pointer}#loading{padding:20px;background:#e3f1e8}td,th{padding:12px;border-bottom:1px solid #ddd}</style>'


@demo_app.get('/demo/login', response_class=HTMLResponse)
async def login_page():
    return STYLE + '''<h1>内部管理系统 · 演示</h1><p>用于验证 Agent 的本地模拟站点</p>
    <form onsubmit="handleLogin(event)"><label>账号<input id="username" autocomplete="username"></label>
    <label>密码<input id="password" type="password" autocomplete="current-password"></label><button id="login">登录</button></form><p id="error"></p>
    <script>async function handleLogin(e){e.preventDefault();const r=await fetch('/demo/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.querySelector('#username').value,password:document.querySelector('#password').value})});if(r.ok)location.href='/demo/home';else document.querySelector('#error').textContent='登录失败'}</script>'''


@demo_app.post('/demo/auth')
async def auth(request: Request):
    data = await request.json()
    if data != {'username': 'demo', 'password': 'demo-password'}:
        return JSONResponse({'error': 'invalid credentials'}, status_code=401)
    response = JSONResponse({'ok': True})
    response.set_cookie('demo_session', 'local-demo', httponly=True, samesite='strict')
    return response


@demo_app.get('/demo/home', response_class=HTMLResponse)
async def home(request: Request):
    if request.cookies.get('demo_session') != 'local-demo':
        return RedirectResponse('/demo/login')
    return STYLE + '<h1 id="logged-in">业务首页</h1><button onclick="location.href=\'/demo/business\'">生产查询</button>'


@demo_app.get('/demo/business', response_class=HTMLResponse)
async def business(request: Request):
    if request.cookies.get('demo_session') != 'local-demo':
        return RedirectResponse('/demo/login')
    return STYLE + '''<h1 id="logged-in">生产查询</h1><label>生产批次号<input id="batch"></label>
    <label for="level">物料层级</label><select id="level"><option value="1">1</option><option value="2">2</option><option value="3">3</option></select>
    <button onclick="query()">查询</button><button id="export" disabled onclick="downloadFile(false)">导出</button>
    <div id="loading" hidden>查询中…</div><div id="results">请输入查询条件</div>
    <section><h3>测试场景</h3><button onclick="downloadFile(true)">导出空文件</button>
    <button onclick="count()">重复按钮</button><button onclick="count()">重复按钮</button><span id="click-count">0</span></section>
    <script>
    let queriedBatch='', queriedLevel='';
    function count(){document.querySelector('#click-count').textContent=Number(document.querySelector('#click-count').textContent)+1}
    async function query(){const b=document.querySelector('#batch').value,l=document.querySelector('#level').value;document.querySelector('#export').disabled=true;
      await new Promise(r=>setTimeout(r,200));document.querySelector('#loading').hidden=false;
      const r=await fetch('/demo/query?batch='+encodeURIComponent(b)+'&level='+encodeURIComponent(l));const data=await r.json();
      const results=document.querySelector('#results');results.replaceChildren();
      if(data.rows.length){const table=document.createElement('table');for(const row of [['批次号','物料层级','产量'],...data.rows]){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=value;tr.append(td)}table.append(tr)}results.append(table);queriedBatch=b;queriedLevel=l;document.querySelector('#export').disabled=false}
      else results.textContent='暂无数据';document.querySelector('#loading').hidden=true;
    }
    function downloadFile(empty){location.href='/demo/export?batch='+encodeURIComponent(queriedBatch)+'&level='+encodeURIComponent(queriedLevel)+'&empty='+empty}
    </script>'''


@demo_app.get('/demo/query')
async def query(request: Request, batch: str = '', level: str = '1'):
    if request.cookies.get('demo_session') != 'local-demo':
        return JSONResponse({'error': 'login required'}, status_code=401)
    await asyncio.sleep(.5)
    return {'rows': [] if batch.upper() == 'NONE' or not batch else [[batch, level, '120']]}


@demo_app.get('/demo/export')
async def export(request: Request, batch: str = '', level: str = '1', empty: bool = False):
    if request.cookies.get('demo_session') != 'local-demo':
        return Response(status_code=401)
    await asyncio.sleep(.3)
    output = io.StringIO()
    if not empty:
        writer = csv.writer(output)
        writer.writerow(['批次号', '物料层级', '产量'])
        writer.writerow([batch, level, 120])
    return Response(output.getvalue().encode('utf-8-sig') if not empty else b'', media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="production.csv"'})

