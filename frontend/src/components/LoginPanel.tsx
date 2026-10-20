import {useState} from 'react';
import {api} from '../api';
import LoginFields,{emptyLogin} from './LoginFields';
export default function LoginPanel({url,onClose,onSuccess}:{url:string|null;onClose:()=>void;onSuccess:()=>Promise<void>}){
 const [login,setLogin]=useState(()=>emptyLogin(url?.startsWith('http')?url:''));
 const [error,setError]=useState('');const [running,setRunning]=useState(false);
 async function run(){setRunning(true);setError('');try{const response=await api<{result:{status:string;message:string}}>('/login',{method:'POST',body:JSON.stringify({login,parameters:{}})});if(response.result.status!=='success')throw Error(response.result.message);await onSuccess();onClose();}catch(e){setError((e as Error).message);}finally{setRunning(false);}}
 return <div className="modal-backdrop"><form className="workflow-editor login-panel" role="dialog" aria-modal="true" aria-label="配置并执行登录" onSubmit={e=>{e.preventDefault();void run();}}><div className="editor-heading"><h2>配置并执行登录</h2><button type="button" className="secondary" disabled={running} onClick={onClose}>关闭</button></div><div className="editor-body"><p>账号密码由本机 .env 提供。这里仅选择凭据引用；成功后记录一个登录步骤，可单独保存或继续录制业务。</p><LoginFields login={login} onChange={setLogin}/>{error&&<p className="editor-error" role="alert">{error}</p>}<p className="muted">没有凭据可选时，请先在 .env 配置 CREDENTIALS_JSON 并重启服务。</p></div><div className="editor-footer"><button className="primary" disabled={running} type="submit">{running?'登录中…':'执行登录'}</button></div></form></div>;
}
