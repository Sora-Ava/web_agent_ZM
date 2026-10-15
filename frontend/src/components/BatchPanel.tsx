import { useEffect, useState } from 'react';
import { ClipboardPaste, Plus, Trash2, ListPlus } from 'lucide-react';
import type { WorkflowRecord } from '../workflows';

export default function BatchPanel({record,onSubmit}:{record:WorkflowRecord;onSubmit:(rows:Record<string,string>[])=>Promise<void>}){
 const empty=()=>Object.fromEntries(record.definition.parameters.map(p=>[p.key,p.default??'']));
 const [rows,setRows]=useState<Record<string,string>[]>([empty()]);
 const [paste,setPaste]=useState('');
 const [error,setError]=useState('');
 const [sending,setSending]=useState(false);
 useEffect(()=>{setRows([Object.fromEntries(record.definition.parameters.map(p=>[p.key,p.default??'']))]);setPaste('');setError('');},[record.id,record.version]);
 function applyPaste(){
  const lines=paste.replace(/\r\n?/g,'\n').replace(/\n+$/,'').split('\n');
  if(!paste.trim()){setError('请先粘贴参数内容');return;}
  if(lines.length>500){setError('一次最多 500 行');return;}
  const cells=lines.map(line=>line.split('\t'));
  const invalid=cells.findIndex(row=>row.length!==record.definition.parameters.length);
  if(invalid>=0){setError(`第 ${invalid+1} 行列数不匹配，请按参数顺序粘贴，无需表头。`);return;}
  setRows(cells.map(row=>Object.fromEntries(record.definition.parameters.map((p,index)=>[p.key,row[index]]))));setError('');
 }
 const issues=rows.flatMap((row,index)=>record.definition.parameters.flatMap(p=>{
  const value=row[p.key]??'';
  return p.required&&!value.trim()?[`第 ${index+1} 行：请填写${p.label}`]:p.kind==='number'&&value&&(!value.trim()||!Number.isFinite(Number(value)))?[`第 ${index+1} 行：${p.label}需要数值`]:value.length>4000?[`第 ${index+1} 行：${p.label}过长`]:[];
 }));
 async function submit(){if(issues.length){setError(issues[0]);return;}setSending(true);setError('');try{await onSubmit(rows);}catch(e){setError((e as Error).message);}finally{setSending(false);}}
 return <section className="panel batch-panel"><div className="panel-heading"><div><span className="eyebrow">BATCH PARAMETERS</span><h2>{record.definition.name}</h2></div><span className="version-chip">v{record.version}</span></div><div className="batch-body">
  <p className="batch-help">每行独立执行，单行失败后继续。提交时固定参数和流程版本，正常执行不调用模型。</p>
  <label className="field-label">粘贴参数表格<textarea aria-label="粘贴参数表格" value={paste} onChange={e=>setPaste(e.target.value)} placeholder="从 Excel 复制单元格，按下方列顺序粘贴；无需表头" rows={3}/></label>
  <button className="secondary" onClick={applyPaste}><ClipboardPaste size={14}/>应用粘贴内容</button>
  <div className="batch-table-wrap"><table className="batch-table"><thead><tr><th>行</th>{record.definition.parameters.map(p=><th key={p.key}>{p.label}{p.required?' *':''}</th>)}<th/></tr></thead><tbody>{rows.map((row,index)=><tr key={index}><td>{index+1}</td>{record.definition.parameters.map(p=><td key={p.key}><input aria-label={`第 ${index+1} 行 ${p.label}`} value={row[p.key]??''} maxLength={4000} onChange={e=>setRows(rows.map((r,i)=>i===index?{...r,[p.key]:e.target.value}:r))}/></td>)}<td><button className="icon-button" aria-label={`删除第 ${index+1} 行`} disabled={rows.length===1} onClick={()=>setRows(rows.filter((_,i)=>i!==index))}><Trash2 size={14}/></button></td></tr>)}</tbody></table></div>
  {error&&<p className="batch-error" role="alert">{error}</p>}{!error&&issues.length>0&&<p className="batch-error">{issues[0]}</p>}
  <div className="run-actions"><button className="secondary" disabled={rows.length>=500} onClick={()=>setRows([...rows,empty()])}><Plus size={14}/>添加一行</button><button className="primary" disabled={sending||issues.length>0} onClick={()=>void submit()}><ListPlus size={15}/>{sending?'正在提交':'加入任务队列'}</button></div><small className="muted">共 {rows.length} 行 · 表格按字符串保留批次号前导零</small>
 </div></section>;
}
