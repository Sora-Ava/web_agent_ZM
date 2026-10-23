import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleDot, Command as CommandIcon, ExternalLink, Globe2, Layers3, LoaderCircle, Play, Plus, Power, Save, Terminal, Workflow, Trash2 } from 'lucide-react';
import { api, socketUrl, type Snapshot } from './api';
import type { Definition, Proposal, WorkflowRecord, PendingLoginProposal, RecordingProposal } from './workflows';
import LoginConfirmation from './components/LoginConfirmation';
import CommandPanel from './components/CommandPanel';
import LoginPanel from './components/LoginPanel';
import BrowserView from './components/BrowserView';
import FileList from './components/FileList';
import WorkflowEditor from './components/WorkflowEditor';
import WorkflowRunPanel from './components/WorkflowRunPanel';
import BatchPanel from './components/BatchPanel';
import TaskPanel from './components/TaskPanel';
import DiagnosticsPanel from './components/DiagnosticsPanel';
import DeleteDialog, { type DeleteTarget } from './components/DeleteDialog';
import type { TaskRecord } from './tasks';

const initial:Snapshot={session:{status:'idle',current_url:null},commands:[],files:[],events:[],workflows:[],recording:null,run:null};
const statusNames:Record<string,string>={idle:'未启动',starting:'启动中',ready:'已就绪',running:'执行中',failed:'启动失败',closed:'已关闭'};
type EditorState={proposal:Proposal;record:WorkflowRecord|null};

export default function App(){
 const [snapshot,setSnapshot]=useState(initial);
 const [selected,setSelected]=useState<WorkflowRecord|null>(null);
 const [batchMode,setBatchMode]=useState(false);
 const [history,setHistory]=useState(false);
 const [taskId,setTaskId]=useState<string|null>(null);
 const [taskRecord,setTaskRecord]=useState<TaskRecord|null>(null);
 const taskRequest=useRef(0);
 const [editor,setEditor]=useState<EditorState|null>(null);
 const [loginConfirmation,setLoginConfirmation]=useState<PendingLoginProposal|null>(null);
 const [showLogin,setShowLogin]=useState(false);
 const [showDiagnostics,setShowDiagnostics]=useState(false);
 const [deleting,setDeleting]=useState<DeleteTarget|null>(null);
 const [error,setError]=useState('');
 const [pending,setPending]=useState(false);
 const [controlPending,setControlPending]=useState(false);
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
 useEffect(()=>{
  const request=++taskRequest.current;
  if(!taskId){setTaskRecord(null);return;}
  void api<TaskRecord>('/tasks/'+taskId).then(record=>{if(request===taskRequest.current)setTaskRecord(record);}).catch(e=>{if(request===taskRequest.current)setError((e as Error).message);});
 },[taskId,snapshot.tasks]);
 const status=snapshot.session.status;
 const controlMode=snapshot.session.control_mode||'agent';
 const busy=status==='running'||status==='starting'||pending||controlMode!=='agent';
 useEffect(()=>{if(controlMode==='agent')return;const timer=setInterval(()=>void refresh(),1000);return()=>clearInterval(timer);},[controlMode,refresh]);
 async function switchControl(){
  setControlPending(true);setError('');
  try{setSnapshot(await api<Snapshot>('/session/'+(controlMode==='manual'?'return-control':'takeover'),{method:'POST'}));}
  catch(e){setError((e as Error).message);}finally{setControlPending(false);}
 }
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
  const request=++selectId.current;setError('');setHistory(false);setTaskId(null);setBatchMode(false);
  try{const record=await api<WorkflowRecord>(`/workflows/${id}${version?`?version=${version}`:''}`);if(request===selectId.current)setSelected(record);}
  catch(e){setError((e as Error).message);}
 }
 async function newRecording(){
  setPending(true);setError('');
  try{
   await api('/recording/reset',{method:'POST'});selectId.current++;setSelected(null);setHistory(false);setTaskId(null);
   if(active){const result=await api<Snapshot&{login?:{status:string;message:string}}>('/session/start',{method:'POST'});if(result.login?.status==='failed')setError(result.login.message);}
   await refresh();
  }catch(e){setError((e as Error).message);}finally{setPending(false);}
 }
 async function prepareSave(){
  setPending(true);setError('');
  try{acceptProposal(await api<RecordingProposal>('/recording/proposal',{method:'POST',body:JSON.stringify({use_model:true})}));}
  catch(e){setError((e as Error).message);}finally{setPending(false);}
 }
 function acceptProposal(proposal:RecordingProposal){
  if(proposal.definition===null){setLoginConfirmation(proposal);setEditor(null);}
  else{setLoginConfirmation(null);setEditor({proposal,record:null});}
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
 async function submitBatch(rows:Record<string,string>[]){
  if(!selected)return;
  const result=await api<{id:string}>('/tasks',{method:'POST',body:JSON.stringify({flow_id:selected.id,version:selected.version,rows})});
  setTaskId(result.id);setTaskRecord(null);setHistory(true);await refresh();
 }
 async function controlTask(action:'pause'|'resume'|'stop'){
  if(!taskId)return;await api('/tasks/'+taskId+'/'+action,{method:'POST'});await refresh();
 }
 async function releaseQueue(){setError('');try{await api('/queue/release-authoring',{method:'POST'});await refresh();}catch(e){setError((e as Error).message);}}
 function openHistory(id:string|null=null){selectId.current++;setHistory(true);setTaskId(id);setTaskRecord(null);}
 async function confirmDelete(){
  if(!deleting)return;
  const resource=deleting.type==='workflow'?'workflows':deleting.type==='task'?'tasks':'files';
  await api('/'+resource+'/'+deleting.id,{method:'DELETE',body:JSON.stringify({confirmed:true,...(deleting.type==='workflow'?{expected_version:deleting.version}:{})})});
  if(deleting.type==='workflow'&&selected?.id===deleting.id){selectId.current++;setSelected(null);}
  if(deleting.type==='task'&&taskId===deleting.id){taskRequest.current++;setTaskId(null);setTaskRecord(null);}
  setDeleting(null);await refresh();
 }
 const metrics=snapshot.events.filter(e=>e.type==='metrics');
 const recordingCount=snapshot.recording?.step_count||0;
 return <div className="shell">
  <aside className="sidebar">
   <a className="brand" href="/"><div className="brand-icon"><Workflow size={23}/></div><div>Web Agent<small>浏览器自动化工作台</small></div></a>
   <div className="workspace-label">个人工作空间 <span>LOCAL</span></div>
   <nav><button className={!selected&&!history?'nav-active nav-button':'nav-button'} onClick={()=>{selectId.current++;setSelected(null);setHistory(false);setTaskId(null);}}><CommandIcon size={17}/>操作工作台</button><button className={history?'nav-active nav-button':'nav-button'} onClick={()=>openHistory()}><Layers3 size={17}/>任务历史</button></nav>
   <div className="sidebar-section sidebar-workflows"><div className="flow-list-heading"><span>标准流程</span><button className="icon-button" disabled={busy} onClick={()=>void newRecording()} aria-label="新建流程" title="新建流程并开始录制"><Plus size={17}/></button></div>
    <div className="flow-list">{(snapshot.workflows||[]).map(flow=><div key={flow.id} className="flow-row"><button className={'flow-item '+(selected?.id===flow.id?'selected':'')} onClick={()=>void chooseFlow(flow.id)} aria-label={'流程：'+flow.name}><Workflow size={15}/><span>{flow.name}</span><small>v{flow.version}</small></button><button className="icon-button delete-icon" disabled={busy} aria-label={'删除流程 '+flow.name} title="删除流程" onClick={()=>setDeleting({type:'workflow',id:flow.id,name:flow.name,version:flow.version})}><Trash2 size={13}/></button></div>)}{!snapshot.workflows?.length&&<p className="sidebar-empty">成功操作后保存流程，<br/>下次只需填写参数。</p>}</div>
   </div>
   <div className="phase-card"><div><Layers3 size={16}/><strong>阶段 05</strong><span className="phase-dot"/></div><p>通用浏览器 · 第二步</p><small>凭据引用 → 显式登录 → 回放</small></div>
   <div className="future-note"><Workflow size={18}/><strong>标准回放，减少等待</strong><p>正常执行零模型，<br/>修复确认后另存版本。</p></div>
   <a className="repo-link" href="https://github.com/Sora-Ava/web_agent_ZM" target="_blank" rel="noreferrer">项目仓库<ExternalLink size={13}/></a>
   <div className="local-status"><span className={connected?'dot':'dot offline'}/>{connected?'本地服务已连接':'正在连接本地服务'}<span>v0.5</span></div>
  </aside>
  <main><header className="topbar"><div><span className="breadcrumb">工作空间 / </span>{history?'任务历史':selected?'标准流程':'操作工作台'}</div><button className="diagnostics-button" onClick={()=>setShowDiagnostics(true)}>环境检查</button><div className="local-badge"><Globe2 size={13}/>Windows · 本机运行</div></header>
   <div className="page-content"><div className="page-heading"><div><div className="page-kicker">BROWSER AUTOMATION</div><h1>{history?'每一行，都有结果':selected?'让成功的流程，再跑一次':'把下一步，交给 Agent'}<span>✦</span></h1><p>{history?'查看任务进度、参数快照和关联文件。':selected?'填写本次查询参数，程序按已确认流程直接执行。':'指定网址并用自然语言操作当前标签页。'}</p></div>
    <div className="session-actions"><button className="secondary" disabled={busy||!connected} onClick={()=>setShowLogin(true)}>配置并执行登录</button><span className={'session-status '+(active?'online':'')}><CircleDot size={13}/>{pending?'处理中':statusNames[status]||status}</span>{active?<button className="secondary" disabled={busy} onClick={()=>void lifecycle(false)}><Power size={15}/>关闭浏览器</button>:<button className="primary" disabled={busy||!connected} onClick={()=>void lifecycle(true)}>{pending?<LoaderCircle size={16} className="spin"/>:<Play size={15}/>}启动浏览器</button>}</div>
   </div>
   {error&&<div className="error-banner" role="alert">{error}<button onClick={()=>setError('')} aria-label="关闭错误">×</button></div>}
   <div className="info-strip"><span className="info-number">05</span><div><strong>{history?'任务历史与关联文件保存在本地':selected?'正常执行不调用 LLM，异常时有限恢复':'站点内动作可录制，跨站操作先自由执行'}</strong><span>{history?'每行独立记录；完成任务仍需查看各行结果。':selected?'登录状态、元素定位和下载结果仍由程序检查。':`当前草稿已记录 ${recordingCount} 步。输入信息将作为参数建议展示。`}</span></div></div>
   {snapshot.queue?.fault&&<div className="queue-banner queue-fault"><div><strong>队列因全局故障暂停派发</strong><span>{snapshot.queue.fault}</span></div><button className="secondary" disabled={busy} onClick={()=>void releaseQueue()}>重新启动队列</button></div>}
   {snapshot.queue?.authoring&&<div className="queue-banner"><div><strong>浏览器由编排会话占用</strong><span>队列等待中；结束编排后按顺序执行。</span></div><button className="secondary" disabled={busy} onClick={()=>void releaseQueue()}>结束编排并启动队列</button></div>}
   {selected&&!history&&<div className="run-mode-tabs"><button className={!batchMode?'selected':''} onClick={()=>setBatchMode(false)}>单次运行</button><button className={batchMode?'selected':''} onClick={()=>setBatchMode(true)}>批量运行</button></div>}
   {!selected&&!history&&<div className="recording-toolbar"><span>{snapshot.recording?.warnings.length?snapshot.recording.warnings[snapshot.recording.warnings.length-1]:`已记录 ${recordingCount} 个成功操作`}</span><div><button className="secondary" disabled={busy} onClick={()=>void newRecording()}><Plus size={14}/>新建录制</button><button className="primary" disabled={busy||!recordingCount} onClick={()=>void prepareSave()}><Save size={14}/>保存为标准流程</button></div></div>}
   <div className="workspace-grid">
    {history?<TaskPanel key={taskId||'history'} task={taskRecord} tasks={snapshot.tasks||[]} onChoose={id=>openHistory(id)} onControl={controlTask} onDelete={task=>setDeleting({type:'task',id:task.id,name:task.name})}/>:selected&&batchMode?<BatchPanel record={selected} onSubmit={submitBatch}/>:selected?<WorkflowRunPanel record={selected} run={snapshot.run||null} busy={busy} onRun={run} onVersion={version=>void chooseFlow(selected.id,version)} onEdit={()=>setEditor({record:selected,proposal:{definition:selected.definition,warnings:[],suggestion_source:'recording',model_calls:0}})}/>:<CommandPanel commands={snapshot.commands} busy={busy} ready={status==='ready'} onSend={send}/>}
    <div className="right-column"><BrowserView url={snapshot.session.current_url} active={active} controlMode={controlMode} controlPending={controlPending||!connected} onControl={()=>void switchControl()}/><FileList files={snapshot.files} onDelete={file=>setDeleting({type:'file',id:file.id,name:file.name})}/></div>
   </div>
   <footer className="page-footer"><span><Terminal size={13}/>结构化工具执行 · Playwright</span><span>{metrics.length?`编排累计 ${metrics.reduce((sum,e)=>sum+(e.model_calls||0),0)} 次模型请求`:'正常执行无需模型请求'}</span></footer>
  </div></main>
  {showLogin&&<LoginPanel url={snapshot.session.current_url} onClose={()=>setShowLogin(false)} onSuccess={refresh}/>}
   {showDiagnostics&&<DiagnosticsPanel onClose={()=>setShowDiagnostics(false)}/>}
  {deleting&&<DeleteDialog target={deleting} onConfirm={confirmDelete} onClose={()=>setDeleting(null)}/>}
  {editor&&<WorkflowEditor proposal={editor.proposal} editing={!!editor.record} onSave={save} onClose={()=>setEditor(null)}/>}
  {loginConfirmation&&<LoginConfirmation proposal={loginConfirmation} onResult={acceptProposal} onClose={()=>setLoginConfirmation(null)}/>}
 </div>;
}
