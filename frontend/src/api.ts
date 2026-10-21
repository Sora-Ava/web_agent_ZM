import type { TaskSummary, QueueState } from './tasks';
export type Result = {status:'success'|'failed'|'ambiguous';message:string;file_id?:string};
export type Command = {id:string;instruction:string;status:string;result:Result|null};
export type ExportFile = {id:string;name:string;size:number;original_name?:string;task_id?:string|null;row_index?:number|null};
export type AgentEvent = {type:string;seq?:number;message?:string;result?:Result;action?:string;model_calls?:number;elapsed_s?:number};
export type Snapshot = {session:{status:string;current_url:string|null;control_mode?:'agent'|'requesting'|'manual'};commands:Command[];files:ExportFile[];events:AgentEvent[];workflows?:WorkflowSummary[];recording?:{revision:string;step_count:number;warnings:string[]}|null;run?:WorkflowRun|null;tasks?:TaskSummary[];queue?:QueueState|null};

export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
  const response=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(typeof body.detail==='string'?body.detail:'请求失败，请检查输入和服务状态');}
  return response.json();
}
export function socketUrl(path:string){return `${location.protocol==='https:'?'wss':'ws'}://${location.host}/api${path}`;}
import type { WorkflowRun, WorkflowSummary } from './workflows';
