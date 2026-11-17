import {logSetsAPI,crashCasesAPI,recoveriesAPI,rollbacksAPI} from '../api/entities.js';
export const store={logs:[],cases:[],runs:[],rollbacks:[],async refresh(){[this.logs,this.cases,this.runs,this.rollbacks]=await Promise.all([logSetsAPI.list(),crashCasesAPI.list(),recoveriesAPI.list(),rollbacksAPI.list()]);}};
export function committedTxs(logSet){
 if(!logSet)return [];
 const open=new Set(),done=new Set(),out=[];
 for(const r of logSet.records){
  if(r.op==='begin')open.add(r.tx);
  else if(r.op==='commit'&&open.has(r.tx)){open.delete(r.tx);done.add(r.tx);out.push(r.tx);}
  else if(r.op==='abort'&&open.has(r.tx))open.delete(r.tx);
 }
 return out;
}
