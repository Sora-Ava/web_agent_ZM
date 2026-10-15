import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleDot, Command as CommandIcon, ExternalLink, Globe2, Layers3, LoaderCircle, Play, Plus, Power, Save, Terminal, Workflow } from 'lucide-react';
import { api, socketUrl, type Snapshot } from './api';
import type { Definition, Proposal, WorkflowRecord } from './workflows';
import CommandPanel from './components/CommandPanel';
import BrowserView from './components/BrowserView';
import FileList from './components/FileList';
import WorkflowEditor from './components/WorkflowEditor';
import WorkflowRunPanel from './components/WorkflowRunPanel';

const initial:Snapshot={session:{status:'idle',current_url:null},commands:[],files:[],events:[],workflows:[],recording:null,run:null};
const statusNames:Record<string,string>={idle:'未启动',starting:'启动中',ready:'已就绪',running:'执行中',failed:'启动失败',closed:'已关闭'};
type EditorState={proposal:Proposal;record:WorkflowRecord|null};

export default function App(){
 const [snapshot,setSnapshot]=useState(initial);
 const [selected,setSelected]=useState<WorkflowRecord|null>(null);
 const [editor,setEditor]=useState<EditorState|null>(null);
 const [error,setError]=useState('');
 const [pending,setPending]=useState(false);
 const [connected,setConnected]=useState(false);
 const refreshId=useRef(0);
 const selectId=useRef(0);
 const refresh=useCallback(async()=>{
  const id=++refreshId.current;
  try{const next=await api<Snapshot>('/session');if(id===refreshId.current)setSnapshot(next);}
  catch(e){if(id===refreshId.current)setError((e as Error).message);}
 },[]);
 useEffect(()=>{
  let stopped=false;let ws:WebSocket;let timer:ReturnType<typeof setTimeout>;
  function connect(){
   ws=new WebSocket(socketUrl('/events'));
   ws.onopen=()=>{setConnected(true);void refresh();};
   ws.onmessage=e=>{const event=JSON.parse(e.data);if(event.type==='snapshot'){refreshId.current++;setSnapshot(event);}else if(event.type!=='heartbeat')void refresh();};
   ws.onclose=()=>{setConnected(false);if(!stopped)timer=setTimeout(connect,2000);};
   ws.onerror=()=>ws.close();
  }
  connect();return()=>{stopped=true;clearTimeout(timer);ws.close();};
 },[refresh]);
 const status=snapshot.session.status;
 const busy=status==='running'||status==='starting'||pending;
 const active=['ready','running'].includes(status);
 async function lifecycle(start:boolean){
  setPending(true);setError('');
  try{const result=await api<Snapshot&{login?:{status:string;message:string}}>(start?'/session/start':'/session',{method:start?'POST':'DELETE'});setSnapshot(result);if(result.login?.status==='failed')setError(result.login.message);}
  catch(e){setError((e as Error).message);}finally{setPending(false);}
 }
 async function send(text:string){
  setError('');try{await api('/commands',{method:'POST',body:JSON.stringify({instruction:text})});await refresh();}
  catch(e){setError((e as Error).message);}
 }
 async function chooseFlow(id:string,version?:number){
  const request=++selectId.current;setError('');
  try{const record=await api<WorkflowRecord>(`/workflows/${id}${version?`?version=${version}`:''}`);if(request===selectId.current)setSelected(record);}
  catch(e){setError((e as Error).message);}
 }
 async function newRecording(){
  setPending(true);setError('');
  try{
   await api('/recording/reset',{method:'POST'});selectId.current++;setSelected(null);
   if(active){const result=await api<Snapshot&{login?:{status:string;message:string}}>('/session/start',{method:'POST'});if(result.login?.status==='failed')setError(result.login.message);}
   await refresh();
  }catch(e){setError((e as Error).message);}finally{setPending(false);}
 }
 async function prepareSave(){
  setPending(true);setError('');
  try{const proposal=await api<Proposal>('/recording/proposal',{method:'POST',body:JSON.stringify({use_model:true})});setEditor({proposal,record:null});}
  catch(e){setError((e as Error).message);}finally{setPending(false);}
 }
 async function save(definition:Definition){
  if(!editor)return;
  const path=editor.record?`/workflows/${editor.record.id}/versions`:'/workflows';
  const payload=editor.record?{definition,confirmed:true,expected_version:Math.max(...editor.record.versions)}:{definition,confirmed:true,source_revision:editor.proposal.revision};
  const record=await api<WorkflowRecord>(path,{method:'POST',body:JSON.stringify(payload)});
  setSelected(record);setEditor(null);await refresh();
 }
 async function run(parameters:Record<string,string>){
  if(!selected)return;
  setPending(true);setError('');
  try{await api(`/workflows/${selected.id}/runs`,{method:'POST',body:JSON.stringify({parameters,version:selected.version})});await refresh();}
  catch(e){setError((e as Error).message);}finally{setPending(false);}
 }
 const metrics=snapshot.events.filter(e=>e.type==='metrics');
 const recordingCount=snapshot.recording?.step_count||0;
 return <div className="shell">
  <aside className="sidebar">
   <a className="brand" href="/"><div className="brand-icon"><Workflow size={23}/></div><div>Web Agent<small>浏览器自动化工作台</small></div></a>
   <div className="workspace-label">个人工作空间 <span>LOCAL</span></div>
   <nav><button className={!selected?'nav-active nav-button':'nav-button'} onClick={()=>{selectId.current++;setSelected(null);}}><CommandIcon size={17}/>操作工作台</button></nav>
   <div className="sidebar-section sidebar-workflows"><div className="flow-list-heading"><span>标准流程</span><button className="icon-button" disabled={busy} onClick={()=>void newRecording()} aria-label="新建流程" title="新建流程并开始录制"><Plus size={17}/></button></div>
    <div className="flow-list">{(snapshot.workflows||[]).map(flow=><button key={flow.id} className={'flow-item '+(selected?.id===flow.id?'selected':'')} onClick={()=>void chooseFlow(flow.id)} aria-label={'流程：'+flow.name}><Workflow size={15}/><span>{flow.name}</span><small>v{flow.version}</small></button>)}{!snapshot.workflows?.length&&<p className="sidebar-empty">成功操作后保存流程，<br/>下次只需填写参数。</p>}</div>
   </div>
   <div className="phase-card"><div><Layers3 size={16}/><strong>阶段 02</strong><span className="phase-dot"/></div><p>把成功操作保存下来</p><small>录制 → 确认 → 标准回放</small></div>
   <div className="future-note"><Workflow size={18}/><strong>标准回放，减少等待</strong><p>批量执行与异常恢复将在<br/>后续阶段加入。</p></div>
   <a className="repo-link" href="https://github.com/Sora-Ava/web_agent_ZM" target="_blank" rel="noreferrer">项目仓库<ExternalLink size={13}/></a>
   <div className="local-status"><span className={connected?'dot':'dot offline'}/>{connected?'本地服务已连接':'正在连接本地服务'}<span>v0.2</span></div>
  </aside>
  <main><header className="topbar"><div><span className="breadcrumb">工作空间 / </span>{selected?'标准流程':'操作工作台'}</div><div className="local-badge"><Globe2 size={13}/>Windows · 本机运行</div></header>
   <div className="page-content"><div className="page-heading"><div><div className="page-kicker">BROWSER AUTOMATION</div><h1>{selected?'让成功的流程，再跑一次':'把下一步，交给 Agent'}<span>✦</span></h1><p>{selected?'填写本次查询参数，程序按已确认流程直接执行。':'用自然语言逐步操作，成功后保存为标准流程。'}</p></div>
    <div className="session-actions"><span className={'session-status '+(active?'online':'')}><CircleDot size={13}/>{pending?'处理中':statusNames[status]||status}</span>{active?<button className="secondary" disabled={busy} onClick={()=>void lifecycle(false)}><Power size={15}/>关闭浏览器</button>:<button className="primary" disabled={busy||!connected} onClick={()=>void lifecycle(true)}>{pending?<LoaderCircle size={16} className="spin"/>:<Play size={15}/>}启动并登录</button>}</div>
   </div>
   {error&&<div className="error-banner" role="alert">{error}<button onClick={()=>setError('')} aria-label="关闭错误">×</button></div>}
   <div className="info-strip"><span className="info-number">02</span><div><strong>{selected?'标准流程回放不调用 LLM':'成功动作自动记录，保存前由你确认'}</strong><span>{selected?'登录状态、元素定位和下载结果仍由程序检查。':`当前草稿已记录 ${recordingCount} 步。输入信息将作为参数建议展示。`}</span></div></div>
   {!selected&&<div className="recording-toolbar"><span>{snapshot.recording?.warnings.length?'草稿包含未完成指令，请保存前检查。':`已记录 ${recordingCount} 个成功操作`}</span><div><button className="secondary" disabled={busy} onClick={()=>void newRecording()}><Plus size={14}/>新建录制</button><button className="primary" disabled={busy||!recordingCount} onClick={()=>void prepareSave()}><Save size={14}/>保存为标准流程</button></div></div>}
   <div className="workspace-grid">
    {selected?<WorkflowRunPanel record={selected} run={snapshot.run||null} busy={busy} onRun={run} onVersion={version=>void chooseFlow(selected.id,version)} onEdit={()=>setEditor({record:selected,proposal:{definition:selected.definition,warnings:[],suggestion_source:'recording',model_calls:0}})}/>:<CommandPanel commands={snapshot.commands} busy={busy} ready={status==='ready'} onSend={send}/>}
    <div className="right-column"><BrowserView url={snapshot.session.current_url} active={active}/><FileList files={snapshot.files}/></div>
   </div>
   <footer className="page-footer"><span><Terminal size={13}/>结构化工具执行 · Playwright</span><span>{metrics.length?`编排累计 ${metrics.reduce((sum,e)=>sum+(e.model_calls||0),0)} 次模型请求`:'标准回放无需模型请求'}</span></footer>
  </div></main>
  {editor&&<WorkflowEditor proposal={editor.proposal} editing={!!editor.record} onSave={save} onClose={()=>setEditor(null)}/>}
 </div>;
}
