import { Download, FileSpreadsheet, FolderOpen } from 'lucide-react';
import type { ExportFile } from '../api';

export default function FileList({files}:{files:ExportFile[]}){
 return <section className="panel files-panel"><div className="panel-heading"><div className="file-title"><FolderOpen size={18}/><h2>导出文件</h2><span className="count">{files.length}</span></div><span className="muted">本次启动期间</span></div>{files.length===0?<div className="files-empty"><FileSpreadsheet size={22} strokeWidth={1.3}/><span>导出完成后，文件会显示在这里</span><small>自动保存到本地 · 同名文件不覆盖</small></div>:<div className="files-list">{files.map(file=><div className="file-row" key={file.id}><FileSpreadsheet size={23}/><div><strong title={file.name}>{file.name}</strong><small>{(file.size/1024).toFixed(1)} KB · 已完成非空校验</small></div><a href={'/api/files/'+file.id+'/download'} aria-label={'下载 '+file.name}><Download size={18}/></a></div>)}</div>}</section>;
}
