import { useState } from 'react';

export type DeleteTarget={type:'workflow'|'task'|'file';id:string;name:string;version?:number};
export default function DeleteDialog({target,onConfirm,onClose}:{target:DeleteTarget;onConfirm:()=>Promise<void>;onClose:()=>void}){
 const [pending,setPending]=useState(false);
 const [error,setError]=useState('');
 async function confirm(){setPending(true);setError('');try{await onConfirm();}catch(e){setError((e as Error).message);setPending(false);}}
 const description=target.type==='workflow'?'流程全部版本将从列表移除，已存在的任务快照和导出文件保留。':target.type==='task'?'删除此任务的参数行、步骤和修复记录；导出文件保留，可继续从文件列表下载。':'文件将移入导出目录下的 .trash 回收目录；列表移除，关联历史保留删除标记，可从本地回收目录取回文件。';
 return <div className="delete-overlay"><section role="dialog" aria-modal="true" aria-label="确认删除" className="delete-dialog"><h2>确认删除</h2><p className="delete-name">{target.name}</p><p>{description}</p>{error&&<div className="batch-error" role="alert">{error}</div>}<div className="run-actions"><button className="secondary" disabled={pending} onClick={onClose}>取消删除</button><button className="danger-button" disabled={pending} onClick={()=>void confirm()}>{pending?'正在删除':'确认删除'}</button></div></section></div>;
}
