import {useState} from 'react';
import {api} from '../api';
import type {PendingLoginProposal, RecordingProposal} from '../workflows';

export default function LoginConfirmation({proposal,onResult,onClose}:{proposal:PendingLoginProposal;onResult:(value:RecordingProposal)=>void;onClose:()=>void}){
 const [choice,setChoice]=useState('');
 const [confirmed,setConfirmed]=useState(false);
 const [saving,setSaving]=useState(false);
 const [error,setError]=useState('');
 const attempt=proposal.login_confirmation.attempts.find(a=>a.status==='awaiting_confirmation');
 async function submit(){
  if(!attempt||choice===''||!confirmed)return;
  setSaving(true);setError('');
  try{onResult(await api<RecordingProposal>('/recording/confirm-login',{method:'POST',body:JSON.stringify({attempt_id:attempt.id,revision:proposal.revision,target:attempt.candidates[Number(choice)].target,confirmed:true})}));}
  catch(e){setError((e as Error).message);}finally{setSaving(false);}
 }
 return <div className="modal-backdrop"><form className="workflow-editor" role="dialog" aria-modal="true" aria-label="确认登录成功标识" onSubmit={e=>{e.preventDefault();void submit();}}>
  <div className="editor-heading"><h2>确认登录成功标识</h2></div><div className="editor-body">
   <p>{proposal.login_confirmation.message}</p>
   {proposal.login_confirmation.attempts.some(a=>a.status==='superseded')&&<p className="editor-warning">旧的未完成登录尝试已被新尝试替代；普通业务操作会保留。</p>}
   {attempt&&attempt.candidates.length>0&&<><p>请选择登录后稳定出现的业务导航或区域。确认后将自动整理账号、密码和提交为一个登录步骤。</p>
    <label className="field-label">登录成功标识<select aria-label="登录成功标识" value={choice} onChange={e=>{setChoice(e.target.value);setConfirmed(false);}}><option value="">请选择成功标识</option>{attempt.candidates.map((candidate,i)=><option key={i} value={i}>{candidate.label}</option>)}</select></label>
    <label className="check-label"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我确认此标识表示登录后的业务页面</label></>}
   {error&&<p role="alert" className="editor-error">{error}</p>}
  </div><div className="editor-footer"><button type="button" className="secondary" disabled={saving} onClick={onClose}>返回继续操作</button><button type="submit" className="primary" disabled={saving||!confirmed||choice===''}>确认成功标识</button></div>
 </form></div>;
}
