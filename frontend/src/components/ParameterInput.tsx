import {useCredentials} from '../credentials';
import type {Parameter} from '../workflows';
export default function ParameterInput({parameter,value,onChange,disabled=false,label}:{parameter:Parameter;value:string;onChange:(value:string)=>void;disabled?:boolean;label?:string}){
 const credentials=useCredentials();
 if(parameter.kind==='credential')return <select aria-label={label||parameter.label} value={value} required={parameter.required} disabled={disabled} onChange={e=>onChange(e.target.value)}><option value="">请选择凭据</option>{credentials.map(c=><option key={c.name} value={c.name}>{c.name} · {c.origin}</option>)}</select>;
 return <input aria-label={label||parameter.label} value={value} type={parameter.kind==='url'?'url':'text'} required={parameter.required} disabled={disabled} maxLength={4000} inputMode={parameter.kind==='number'?'decimal':'text'} onChange={e=>onChange(e.target.value)}/>;
}
