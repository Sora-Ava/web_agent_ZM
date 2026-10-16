import { useEffect, useState } from 'react';
import { Wrench, Save } from 'lucide-react';
import { api } from '../api';
import type { TaskRecord, RepairRecord } from '../tasks';
import type { WorkflowRecord } from '../workflows';

export default function RepairPanel({task}:{task:TaskRecord}){
 const [repairs,setRepairs]=useState<RepairRecord[]>([]);
 const [selected,setSelected]=useState<string[]>([]);
 const [confirmed,setConfirmed]=useState(false);
 const [saving,setSaving]=useState(false);
 const [error,setError]=useState('');
 const [saved,setSaved]=useState<number|null>(null);
 useEffect(()=>{let cancelled=false;void api<RepairRecord[]>('/tasks/'+task.id+'/repairs').then(items=>{
  if(cancelled)return;setRepairs(items);
  setSelected(previous=>{if(previous.length)return previous;const latest=new Map<string,string>();items.filter(r=>!r.approved_version).forEach(r=>latest.set(r.step_id,r.id));return [...latest.values()];});
 }).catch(e=>{if(!cancelled)setError((e as Error).message);});return()=>{cancelled=true;};},[task.id,task.model_calls,task.status,task.rows]);
 const pending=repairs.filter(r=>!r.approved_version);
 const finished=['completed','failed','stopped','interrupted'].includes(task.status);
 async function approve(){setSaving(true);setError('');try{const result=await api<WorkflowRecord>('/tasks/'+task.id+'/repairs/approve',{method:'POST',body:JSON.stringify({ids:selected,confirmed:true,expected_version:task.version})});setSaved(result.version);setConfirmed(false);setSelected([]);setRepairs(await api<RepairRecord[]>('/tasks/'+task.id+'/repairs'));}catch(e){setError((e as Error).message);}finally{setSaving(false);}}
 if(!repairs.length&&!error)return null;
 return <section className="repair-panel"><div className="repair-title"><Wrench size={16}/><h3>待确认定位修复</h3></div><p>这些定位已在当前任务成功执行。审核只更改选中步骤的定位，原参数、动作和版本保持不变。</p>
 {repairs.map(repair=><label className="repair-item" key={repair.id}><input type="checkbox" aria-label={'选择定位修复 '+repair.step_name} checked={selected.includes(repair.id)} disabled={!!repair.approved_version||saving||!finished} onChange={e=>{setConfirmed(false);setSelected(e.target.checked?[...selected.filter(id=>!repairs.some(r=>r.id===id&&r.step_id===repair.step_id)),repair.id]:selected.filter(id=>id!==repair.id));}}/><div><strong>{repair.step_name}</strong><small>第 {repair.row_index+1} 行 · {repair.approved_version?`已固化为 v${repair.approved_version}`:'当前任务临时使用'}</small><div className="repair-locators"><code>{repair.original.kind} · {repair.original.role||''} · {repair.original.value}</code><span>→</span><code>{repair.replacement.kind} · {repair.replacement.role||''} · {repair.replacement.value}</code></div><p>{repair.reason}</p></div></label>)}
 {pending.length>0&&<><label className="repair-confirm"><input type="checkbox" checked={confirmed} disabled={!finished||saving} onChange={e=>setConfirmed(e.target.checked)}/>我已检查定位修复，确认另存流程新版本</label>{!finished&&<small className="muted">请等待任务结束后审核。</small>}<button className="primary" disabled={!confirmed||!selected.length||!finished||saving} onClick={()=>void approve()}><Save size={14}/>{saving?'正在保存':'确认并保存为新版本'}</button></>}
 {saved&&<div className="repair-saved" role="status">已保存为 v{saved}，原版本保持不变</div>}{error&&<p className="batch-error" role="alert">{error}</p>}
 </section>;
}
