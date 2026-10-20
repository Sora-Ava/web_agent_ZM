import { useEffect, useState } from 'react';
import { api } from '../api';

type Diagnostics={site_type:string;login_configured:boolean;model_configured:boolean;query_wait_configured:boolean;browser:{installed:boolean;status:string};paths:{database:string;downloads:string;trash:string};timeouts:{action_ms:number;query_ms:number;download_ms:number;model_s:number}};
export default function DiagnosticsPanel({onClose}:{onClose:()=>void}){
 const [data,setData]=useState<Diagnostics|null>(null);
 const [error,setError]=useState('');
 useEffect(()=>{let closed=false;void api<Diagnostics>('/diagnostics').then(d=>{if(!closed)setData(d);}).catch(e=>{if(!closed)setError((e as Error).message);});return()=>{closed=true;};},[]);
 return <div className="delete-overlay"><section className="delete-dialog diagnostics-dialog" role="dialog" aria-modal="true" aria-label="环境检查"><h2>环境检查</h2>{error?<p role="alert">{error}</p>:!data?<p>正在检查本机配置…</p>:<><p>通用浏览器：登录网址和定位由流程步骤提供。</p><div className="diagnostic-checks">{[['Chromium 已安装',data.browser.installed],['本机凭据配置齐全',data.login_configured],['模型配置齐全',data.model_configured],['查询等待配置齐全',data.query_wait_configured]].map(([label,ready])=><div key={String(label)}><span>{label}</span><strong className={ready?'status-success':'status-failed'}>{ready?'已就绪':'待配置'}</strong></div>)}</div><p>仅检查配置完整性，不发送凭据或请求模型。</p><dl>{Object.entries(data.paths).map(([key,value])=><div key={key}><dt>{{database:'数据库',downloads:'导出目录',trash:'回收目录'}[key]}</dt><dd>{value}</dd></div>)}</dl><small>动作 {data.timeouts.action_ms/1000}s · 查询 {data.timeouts.query_ms/1000}s · 下载 {data.timeouts.download_ms/1000}s · 模型 {data.timeouts.model_s}s</small></>}<div className="run-actions"><button className="secondary" onClick={onClose}>关闭检查</button></div></section></div>;
}
