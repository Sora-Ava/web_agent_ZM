import { useState } from 'react';
import { ArrowUp, Check, CircleHelp, LoaderCircle, X } from 'lucide-react';
import type { Command } from '../api';

export default function CommandPanel({commands,busy,ready,onSend}:{commands:Command[];busy:boolean;ready:boolean;onSend:(text:string)=>Promise<void>}){
  const [text,setText]=useState('');
  const [sending,setSending]=useState(false);
  async function submit(){if(!text.trim()||busy||sending||!ready)return;const instruction=text;setText('');setSending(true);try{await onSend(instruction);}finally{setSending(false);}}
  return <section className="panel command-panel"><div className="panel-heading"><div><span className="eyebrow">STEP BY STEP</span><h2>逐步指挥</h2></div><span className="tag">编排模式</span></div>
    <div className="command-feed" aria-live="polite">{commands.length===0?<div className="welcome"><div className="mini-orbit">✦</div><h3>从第一条指令开始</h3><p>告诉 Agent 当前这一步要做什么。<br/>它会查看页面元素，执行后向你反馈。</p><div className="examples">{['访问 www.baidu.com','在搜索框搜索今日黄金价格','点击页面中的目标链接','在输入框填写指定内容'].map((example,i)=><button key={example} onClick={()=>setText(example)}><span>0{i+1}</span>{example}</button>)}</div></div>:commands.map((cmd,i)=><article className="command-item" key={cmd.id}><div className="command-index">{String(i+1).padStart(2,'0')}</div><div className="command-content"><div className="user-instruction">{cmd.instruction}</div><div className={'agent-result '+(cmd.result?.status||'pending')}>{cmd.status==='running'?<LoaderCircle size={15} className="spin"/>:cmd.result?.status==='success'?<Check size={15}/>:cmd.result?.status==='ambiguous'?<CircleHelp size={15}/>:<X size={15}/>}<span>{cmd.result?.message||(cmd.status==='running'?'正在查看页面并执行…':'操作已中断')}</span></div></div></article>)}</div>
    <form className="composer" onSubmit={e=>{e.preventDefault();void submit();}}><label htmlFor="instruction">下一步操作</label><textarea id="instruction" value={text} onChange={e=>setText(e.target.value)} placeholder={ready?'例如：访问 www.baidu.com 并搜索今日黄金价格':'请先启动浏览器'} maxLength={4000} onKeyDown={e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();void submit();}}}/><div className="composer-bottom"><span>{busy?'当前指令执行完毕后可继续':'Ctrl + Enter 发送 · 每次执行一条指令'}</span><button className="send" type="submit" disabled={!ready||busy||sending||!text.trim()} aria-label="发送指令"><ArrowUp size={20}/></button></div></form>
  </section>;
}
