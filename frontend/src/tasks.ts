import type { Definition, Locator } from './workflows';

export const taskStatus:Record<string,string>={queued:'排队中',running:'运行中',pausing:'暂停中',paused:'已暂停',stopping:'停止中',stopped:'已停止',completed:'已完成',failed:'失败',interrupted:'已中断',success:'成功',no_data:'无数据',skipped:'未执行'};
export type TaskSummary={id:string;flow_id:string;version:number;name:string;status:string;created_at:string;updated_at:string;total:number;counts:Record<string,number>;model_calls:number};
export type TaskStep={id:string;name:string;index:number;result:{status:string;message:string}};
export type TaskRow={row_index:number;parameters:Record<string,string>;status:string;steps:TaskStep[];file_ids:string[];result:{message:string;file_ids:string[];model_calls:number}|null};
export type TaskRecord={id:string;flow_id:string;version:number;name:string;definition:Definition;status:string;created_at:string;rows:TaskRow[];model_calls:number};
export type QueueState={authoring:boolean;active_id:string|null;fault?:string|null};
export type RepairRecord={id:string;task_id:string;row_index:number;step_id:string;step_name:string;original:Locator;replacement:Locator;reason:string;approved_version:number|null};
