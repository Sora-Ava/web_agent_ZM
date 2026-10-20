import {useEffect,useState} from 'react';
import {api} from './api';
export type CredentialInfo={name:string;origin:string;ready:boolean};
let pending:Promise<CredentialInfo[]>|null=null;
export function useCredentials(){
 const [items,setItems]=useState<CredentialInfo[]>([]);
 useEffect(()=>{let live=true;pending??=api<CredentialInfo[]>('/credentials').catch(()=>{pending=null;return [];});void pending.then(data=>{if(live)setItems(data);});return()=>{live=false;};},[]);
 return items;
}
