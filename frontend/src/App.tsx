import { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, CircleDot, Command as CommandIcon, ExternalLink, Globe2, Layers3, LoaderCircle, Play, Power, Terminal, Workflow } from 'lucide-react';
import { api, socketUrl, type Snapshot } from './api';
import CommandPanel from './components/CommandPanel';
import BrowserView from './components/BrowserView';
import FileList from './components/FileList';

const initial:Snapshot={session:{status:'idle',current_url:null},commands:[],files:[],events:[]};
const statusNames:Record<string,string>={idle:'未启动',starting:'启动中',ready:'已就绪',running:'执行中',failed:'启动失败',closed:'已关闭'};

export default function App(){
 const [snapshot,setSnapshot]=useState(initial);
 const [error,setError]=useState('');
 const [pending,setPending]=useState(false);
 const [connected,setConnected]=useState(false);
 const refresh=useCallback(async()=>{try{setSnapshot(await api<Snapshot>('/session'));}catch(e){setError((e as Error).message);}},[]);
 useEffect(()=>{let stopped=false;let ws:WebSocket;let timer:ReturnType<typeof setTimeout>;function connect(){ws=new WebSocket(socketUrl('/events'));ws.onopen=()=>{setConnected(true);void refresh();};ws.onmessage=e=>{const event=JSON.parse(e.data);if(event.type==='snapshot')setSnapshot(event);else if(event.type!=='heartbeat')void refresh();};ws.onclose=()=>{setConnected(false);if(!stopped)timer=setTimeout(connect,2000);};ws.onerror=()=>ws.close();}connect();return()=>{stopped=true;clearTimeout(timer);ws.close();};},[refresh]);
 const status=snapshot.session.status;
 const busy=status==='running'||status==='starting'||pending;
 const active=['ready','running'].includes(status);
 async function lifecycle(start:boolean){setPending(true);setError('');try{const result=await api<Snapshot&{login?:{status:string;message:string}}>(start?'/session/start':'/session',{method:start?'POST':'DELETE'});setSnapshot(result);if(result.login?.status==='failed')setError(result.login.message);}catch(e){setError((e as Error).message);}finally{setPending(false);}}
 async function send(text:string){setError('');try{await api('/commands',{method:'POST',body:JSON.stringify({instruction:text})});await refresh();}catch(e){setError((e as Error).message);}}
 const metrics=snapshot.events.filter(e=>e.type==='metrics');
 return <div className="shell"><aside className="sidebar"><a className="brand" href="/"><div className="brand-icon"><Workflow size={23}/></div><div>Web Agent<small>浏览器自动化工作台</small></div></a><div className="workspace-label">个人工作空间 <span>LOCAL</span></div><nav><div className="nav-active"><CommandIcon size={17}/>操作工作台<ArrowUpRight size={15}/></div></nav><div className="sidebar-section"><span>当前版本</span><div className="phase-card"><div><Layers3 size={16}/><strong>阶段 01</strong><span className="phase-dot"/></div><p>先跑通一次真实操作</p><small>登录 → 查询 → 导出</small></div></div><div className="future-note"><Workflow size={18}/><strong>从一次操作，到标准流程</strong><p>流程保存与批量执行将在<br/>后续阶段加入。</p></div><a className="repo-link" href="https://github.com/Sora-Ava/web_agent_ZM" target="_blank" rel="noreferrer">项目仓库<ExternalLink size={13}/></a><div className="local-status"><span className={connected?'dot':'dot offline'}/>{connected?'本地服务已连接':'正在连接本地服务'}<span>v0.1</span></div></aside>
 <main><header className="topbar"><div><span className="breadcrumb">工作空间 / </span>操作工作台</div><div className="local-badge"><Globe2 size={13}/>Windows · 本机运行</div></header><div className="page-content"><div className="page-heading"><div><div className="page-kicker">BROWSER AUTOMATION</div><h1>把下一步，交给 Agent<span>✦</span></h1><p>用自然语言指挥浏览器，一步一步完成业务操作。</p></div><div className="session-actions"><span className={'session-status '+(active?'online':'')}><CircleDot size={13}/>{pending?'处理中':statusNames[status]||status}</span>{active?<button className="secondary" disabled={busy} onClick={()=>void lifecycle(false)}><Power size={15}/>关闭浏览器</button>:<button className="primary" disabled={busy||!connected} onClick={()=>void lifecycle(true)}>{pending?<LoaderCircle size={16} className="spin"/>:<Play size={15}/>}启动并登录</button>}</div></div>
 {error&&<div className="error-banner" role="alert">{error}<button onClick={()=>setError('')} aria-label="关闭错误">×</button></div>}
 <div className="info-strip"><span className="info-number">01</span><div><strong>先启动浏览器，再输入操作指令</strong><span>登录凭据从本地 .env 读取，Agent 仅查看页面文本与元素。</span></div></div>
 <div className="workspace-grid"><CommandPanel commands={snapshot.commands} busy={busy} ready={status==='ready'} onSend={send}/><div className="right-column"><BrowserView url={snapshot.session.current_url} active={active}/><FileList files={snapshot.files}/></div></div>
 <footer className="page-footer"><span><Terminal size={13}/>结构化工具执行 · Playwright</span><span>{metrics.length?`本次累计 ${metrics.reduce((sum,e)=>sum+(e.model_calls||0),0)} 次模型请求`:'等待第一条操作指令'}</span></footer></div></main></div>;
}
