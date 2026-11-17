import {store,committedTxs} from '../stores/entities.js';
import {session} from '../stores/session.js';
import {rollbacksAPI} from '../api/entities.js';
import {downloadRollback} from '../api/client.js';
import {table} from '../components/common/table.js';
import {ROLLBACK_STATUSES} from '../constants/enums.js';
import {esc,formObject} from '../utils/html.js';
import {action,message} from '../hooks/action.js';

const sourceName=source=>source==='INITIAL'?'初始账本':esc(source);
const rbBadge=status=>`<span class="badge ${esc(status)}">${esc(ROLLBACK_STATUSES[status]||status)}</span>`;

export function rollbacksPage(selected){
 const canWrite=session.role!=='viewer';
 const list=table(['编号','源日志集','撤回目标','连带撤回','派生集','状态','复用','证据'],
  store.rollbacks.map(rb=>{
   const src=store.logs.find(l=>l.id===rb.log_set_id);
   const derived=store.logs.find(l=>l.id===rb.derived_log_set_id);
   return [rb.id,esc(src?.name||rb.log_set_id),esc(rb.target_tx),
     (rb.result?.removed||[]).length,esc(derived?.name||'—'),rbBadge(rb.status),
     rb.reused?'重试复用':'首次生成',`<a href="#rollbacks/${rb.id}">核对依赖与新账本 →</a>`];
  }));
 const form=canWrite?`<form id="rollback-form">
  <label>源日志集<select name="log_set_id" id="rb-source">${store.logs.map(l=>`<option value="${l.id}">${esc(l.name)} · ${l.records.length}条</option>`).join('')}</select></label>
  <label>撤回目标（仅限已提交事务）<select name="target_tx" id="rb-target"></select></label>
  <label>记录标签（可选）<input name="label" placeholder="默认：撤回 T1 及连带依赖" maxlength="120"></label>
  <button type="submit">计算依赖并生成派生日志集 →</button>
  <p class="readonly" style="margin-top:12px">同一来源/目标重试会复用既有派生集，不重复生成；失败的校验不会留下任何派生集。</p>
 </form>`:'<p class="readonly">观察员可查看与导出撤回证据，生成需要操作员或管理员。</p>';
 return `<section class="split compact"><div><h2>选择性撤回，不做整库回退</h2>
 <p>日志中的 <b>read</b> 记录事务读取的键与值：脏读（读未提交写入）与读值不符都会被拒绝；每条读取按<b>实际读到的提交来源</b>追踪依赖，同值覆盖也按来源区分。选择一个已提交事务后，直接及间接依赖它的已提交事务连带撤回，无关的后续写入（含同键覆盖）保留。</p>
 <p class="big-note">撤的是依赖链，不是时间点之后的全部。</p></div>${form}</section>
 <div class="section-title"><h2>撤回记录</h2><button class="secondary" id="refresh">刷新</button></div>${list}
 ${selected?detailSection(selected):'<p class="notice">选择上方记录查看依赖原因与撤回后新账本。</p>'}`;
}

function detailSection(rb){
 const r=rb.result;if(!r)return '<p class="notice">撤回结果计算中或未生成，请刷新。</p>';
 const src=store.logs.find(l=>l.id===rb.log_set_id);
 const derived=store.logs.find(l=>l.id===rb.derived_log_set_id);
 const reasonRows=r.removal_reasons.map(x=>[esc(x.tx),x.kind==='selected'?'选定目标':'级联撤回',
   x.depends_on?esc(x.depends_on):'—',esc(x.reason),esc((x.path||[]).join(' → '))]);
 const edgeRows=r.edges.map(e=>[esc(e.reader),esc(e.source==='INITIAL'?'初始账本':e.source),esc(e.key),e.value,e.lsn]);
 const readRows=r.read_log.map(x=>[x.lsn,esc(x.tx),esc(x.key),x.actual,sourceName(x.source),x.actual===x.declared?'一致':'不符']);
 const compRows=(r.compensation_trace||[]).map(c=>[c.lsn,esc(c.tx),esc(c.key),c.removed_value,c.restored_value,sourceName(c.restored_source)]);
 const ledgerRows=Object.keys(r.ledger_after||{}).map(k=>[esc(k),r.ledger_before[k],r.ledger_after[k],r.oracle[k],
   esc(r.provenance_before[k]==='INITIAL'?'初始账本':r.provenance_before[k]),
   esc(r.provenance_after[k]==='INITIAL'?'初始账本':r.provenance_after[k])]);
 return `<div class="section-title"><h2>${esc(rb.label)} <small>#${rb.id}</small></h2>${rbBadge(rb.status)}${rb.reused?'<span class="badge" style="margin-left:8px">重试复用，未重复生成</span>':''}</div>
 <section class="verdict"><div><p>双路核对（派生重放 vs 删除来源写入）</p><h2>${r.invariant_passed?'新账本一致':'核对失败'}</h2></div>
 <p>目标 ${esc(rb.target_tx)} · 撤回 ${r.removed.length} 个事务，保留 ${r.survivors.length} 个<br>
 检查点 LSN ${esc((r.checkpoints||[]).join(', ')||'无')} · 已中止 ${esc((r.aborted||[]).join(', ')||'无')} · 未完成 ${esc((r.losers||[]).join(', ')||'无')}<br>
 派生日志集：${esc(derived?.name||r.derived_name||'—')}（${derived?derived.records.length+'条':'—'}），源日志集与旧故障场景保持原样</p></section>
 <h2>撤回原因与依赖路径</h2>
 ${table(['事务','类型','直接依赖','原因','依赖链'],reasonRows)}
 <h2>读取依赖边（按实际读到的提交来源）</h2>
 ${table(['读取事务','值来源','键','读到值','读取LSN'],edgeRows)}
 <h2>全部读取记录</h2>
 ${table(['LSN','事务','键','实际值','提交来源','声明核对'],readRows)}
 <h2>撤回写入补偿视图</h2>
 ${table(['写入LSN','撤回事务','键','移除值','恢复为','恢复来源'],compRows)}
 <h2>撤回前后账本与来源</h2>
 ${table(['账本键','撤回前值','撤回后值','独立oracle','撤回前来源','撤回后来源'],ledgerRows)}
 <p class="checksum">输入摘要 ${esc(r.input_sha256||'无')}<br>结果摘要 ${esc(r.result_sha256||'无')}</p>
 <div class="toolbar"><button id="rb-export">下载撤回证据（含派生日志集）</button>
 <a class="secondary" style="padding:11px 19px;border:1px solid var(--line)" href="#logs">去日志集查看派生集 →</a></div>
 <details><summary>查看冻结输入</summary><pre>${esc(JSON.stringify(rb.snapshot,null,2))}</pre></details>`;
}

function fillTargets(){
 const sel=document.querySelector('#rb-source');const target=document.querySelector('#rb-target');
 if(!sel||!target)return;
 const logs=store.logs.find(l=>l.id===Number(sel.value));
 const txs=committedTxs(logs);
 target.innerHTML=txs.length?txs.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('')
  :'<option value="">（该日志集没有已提交事务）</option>';
}

export function bindRollbacks(selected,render){
 document.querySelector('#refresh')?.addEventListener('click',render);
 document.querySelector('#rb-source')?.addEventListener('change',fillTargets);
 fillTargets();
 action(document.querySelector('#rollback-form'),async f=>{
  const d=formObject(f);d.log_set_id=Number(d.log_set_id);
  if(!d.target_tx)throw new Error('请选择一个已提交事务作为撤回目标');
  const obj=await rollbacksAPI.create(d);
  // Navigate before refreshing so the hashchange renders the new record's detail once.
  location.hash='#rollbacks/'+obj.id;
 });
 document.querySelector('#rb-export')?.addEventListener('click',()=>downloadRollback(selected.id).catch(e=>message(e.message,true)));
}
