import ParameterInput from './ParameterInput';
import { useEffect, useState } from 'react';
import { Check, CircleHelp, Edit3, LoaderCircle, Play, X } from 'lucide-react';
import type { WorkflowRecord, WorkflowRun } from '../workflows';

export default function WorkflowRunPanel({record,run,busy,onRun,onEdit,onVersion}:{record:WorkflowRecord;run:WorkflowRun|null;busy:boolean;onRun:(parameters:Record<string,string>)=>Promise<void>;onEdit:()=>void;onVersion:(version:number)=>void}){
 const [values,setValues]=useState<Record<string,string>>({});
 const [sending,setSending]=useState(false);
 useEffect(()=>{setValues(Object.fromEntries(record.definition.parameters.map(p=>[p.key,p.default??''])));},[record.id,record.version]);
 const current=run?.flow_id===record.id&&run.version===record.version?run:null;
 async function submit(){setSending(true);try{await onRun(values);}finally{setSending(false);}}
 return <section className="panel workflow-run-panel"><div className="panel-heading"><div><span className="eyebrow">STANDARD WORKFLOW</span><h2>{record.definition.name}</h2></div><select className="version-select" aria-label="流程版本" value={record.version} disabled={busy} onChange={e=>onVersion(Number(e.target.value))}>{record.versions.map(version=><option key={version} value={version}>v{version}{version===Math.max(...record.versions)?' · 最新':''}</option>)}</select></div>
  <div className="run-panel-body"><div className="run-intro"><strong>填写参数，直接执行</strong><p>按已确认步骤操作；仅在流程包含登录步骤时登录。正常回放无需模型判断。</p></div><form className="run-form" onSubmit={e=>{e.preventDefault();void submit();}}>{record.definition.parameters.map(p=><label className="field-label" key={p.key}>{p.label}{p.required&&<span className="required-mark"> *</span>}<ParameterInput parameter={p} value={values[p.key]??''} disabled={busy} onChange={value=>setValues({...values,[p.key]:value})}/></label>)}<div className="run-actions"><button type="button" className="secondary" disabled={busy} onClick={onEdit}><Edit3 size={14}/>编辑并另存新版本</button><button type="submit" className="primary" disabled={busy||sending}>{busy?<LoaderCircle size={15} className="spin"/>:<Play size={14}/>}运行标准流程</button></div></form>
  {current&&<div className={'run-result '+current.status} data-status={current.status} aria-live="polite"><div>{current.status==='running'?<LoaderCircle size={17} className="spin"/>:current.status==='success'?<Check size={17}/>:current.status==='no_data'?<CircleHelp size={17}/>:<X size={17}/>}<strong>{current.status==='running'?'正在执行':current.status==='success'?'运行成功':current.status==='no_data'?'无数据，未导出':current.status==='interrupted'?'运行已中断':'执行失败'}</strong><span>v{current.version}</span></div><p>{current.result?.message||'请等待当前步骤完成'}</p><small>LLM 调用：{current.result?.model_calls??current.model_calls} 次</small></div>}
  <div className="run-steps"><div className="editor-section-title"><h3>执行步骤</h3><span>{record.definition.steps.length} 步</span></div>{record.definition.steps.map((step,index)=>{const executed=current?.steps.find(s=>s.id===step.id);return <div className={'run-step '+(executed?.result.status||'')} key={step.id}><span>{String(index+1).padStart(2,'0')}</span><div><strong>{step.name}</strong>{executed&&<small>{executed.result.message}</small>}</div>{executed?.result.status==='success'?<Check size={14}/>:executed?<X size={14}/>:null}</div>;})}</div></div>
 </section>;
}
