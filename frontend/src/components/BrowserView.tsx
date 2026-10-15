import { useEffect, useRef, useState } from 'react';
import { Eye, Monitor, Radio, LockKeyhole } from 'lucide-react';
import { socketUrl } from '../api';

export default function BrowserView({url,active}:{url:string|null;active:boolean}){
  const [frame,setFrame]=useState('');
  const [connected,setConnected]=useState(false);
  const latest=useRef('');
  useEffect(()=>{
    if(!active){setFrame('');return;}
    let stopped=false;let ws:WebSocket;let timer:ReturnType<typeof setTimeout>;
    function connect(){ws=new WebSocket(socketUrl('/screen'));ws.binaryType='blob';ws.onopen=()=>setConnected(true);ws.onmessage=e=>{if(!(e.data instanceof Blob))return;const next=URL.createObjectURL(e.data);if(latest.current)URL.revokeObjectURL(latest.current);latest.current=next;setFrame(next);};ws.onclose=()=>{setConnected(false);if(!stopped)timer=setTimeout(connect,2000);};ws.onerror=()=>ws.close();}
    connect();return()=>{stopped=true;clearTimeout(timer);ws.close();if(latest.current)URL.revokeObjectURL(latest.current);latest.current='';};
  },[active]);
  return <section className="panel browser-panel"><div className="panel-heading"><div><span className="eyebrow">LIVE VIEW</span><h2>浏览器画面</h2></div><span className="view-label"><Eye size={14}/>只读观看</span></div><div className="browser-chrome"><div className="traffic"><i/><i/><i/></div><div className="address"><LockKeyhole size={12}/><span>{url||'等待浏览器启动'}</span></div></div><div className="browser-canvas">{frame&&active?<img src={frame} alt="自动化浏览器实时画面"/>:<div className="browser-empty"><Monitor size={44} strokeWidth={1}/><h3>{active?'正在连接浏览器画面':'你的浏览器将在这里呈现'}</h3><p>启动后自动登录指定系统<br/>在左侧输入指令，实时查看执行过程</p></div>}</div><div className="browser-footer"><span><Radio size={13}/>{active?(connected?'实时连接 · 约 2 帧/秒':'画面重连中…'):'尚未启动'}</span><span>画面仅在本机展示，不发送给模型</span></div></section>;
}
