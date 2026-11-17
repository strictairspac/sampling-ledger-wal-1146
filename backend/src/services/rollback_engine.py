"""Selective rollback engine with read-tracked dependency provenance.

Given a validated WAL history that may contain read records, selecting one
committed transaction removes it together with every committed transaction
that directly or indirectly depended on it through observed reads. Unrelated
later writes — including later overwrites of the same key — survive, so the
whole log set never has to be reset. The result is a derived, still-replayable
log set; the source log set and existing crash cases stay untouched.
"""
import copy,hashlib,json
from src.services.wal_engine import replay,validate,INITIAL_SOURCE

MODEL_VERSION='selective-rollback-read-provenance-v1'
BOUNDARY='Python教学模型：按事务读取时实际观察到的提交来源追踪依赖；同值覆盖按来源区分；撤回仅连带直接及间接依赖目标的已提交事务，不做整库回退。'

def derived_set_name(source_name,target):
    return f'{source_name} - 撤回{target}派生集'

def _unique_edges(edges):
    seen=set();out=[]
    for edge in edges:
        key=(edge['reader'],edge['source'],edge['key'])
        if key in seen:continue
        seen.add(key);out.append(dict(edge))
    return out

def plan_rollback(initial,records,target):
    if not isinstance(target,str) or not target:raise ValueError('需要选择一个已提交事务')
    analysis=replay(initial,records)
    if target not in analysis['committed']:
        raise ValueError(f'事务 {target} 不是已提交事务，撤回目标必须已提交')
    edges=_unique_edges(analysis['edges'])
    dependents={}
    for edge in edges:
        dependents.setdefault(edge['source'],set()).add(edge['reader'])
    # BFS closure over the read-dependency graph, recording the shortest reason path.
    removed=[target];seen={target};reason={target:None};frontier=[target]
    while frontier:
        nxt=[]
        for source in frontier:
            for reader in sorted(dependents.get(source,set())):
                if reader in seen:continue
                seen.add(reader);removed.append(reader);nxt.append(reader)
                reason[reader]=source
        frontier=nxt
    removed_set=set(removed)
    removal_reasons=[]
    for tx in removed:
        if tx==target:
            removal_reasons.append({'tx':tx,'kind':'selected','depends_on':None,'reason':'用户选择的撤回目标','path':[target]})
        else:
            chain=[];cur=tx
            while cur is not None:chain.append(cur);cur=reason[cur]
            chain.reverse()
            cause=reason[tx]
            edge=next(e for e in edges if e['reader']==tx and e['source']==cause)
            removal_reasons.append({'tx':tx,'kind':'cascade','depends_on':cause,
                                    'reason':f"事务 {tx} 在 LSN {edge['lsn']} 读取键 {edge['key']}={edge['value']}，该值来自已撤回事务 {cause}",
                                    'path':chain})
    survivors=[tx for tx in analysis['committed'] if tx not in removed_set]
    # Derived replayable history: keep only surviving transactions' operations.
    # Checkpoints and the initial ledger are preserved; aborted/loser records drop out.
    derived=[]
    for r in records:
        op=r.get('op')
        if op=='checkpoint':
            derived.append(dict(r))
        elif r.get('tx') in removed_set:
            continue
        elif r.get('tx') in survivors:
            derived.append(dict(r))
        # aborted or unfinished transactions are excluded from the derived committed history
    if not derived:raise ValueError('撤回后无可用日志，无法生成可继续演练的派生日志集')
    validate(initial,derived)
    # Oracle 1: replay the derived history from the same initial ledger.
    re_analysis=replay(initial,derived)
    # Oracle 2: remove every committed write originating from a removed transaction.
    oracle=copy.deepcopy(initial)
    for row in analysis['committed_writes']:
        if row['tx'] not in removed_set:
            oracle[row['key']]=row['after']
    new_ledger=re_analysis['state']
    invariant_passed=new_ledger==oracle
    # Compensation view keyed by the surviving committed writes in original LSN order.
    compensation=[]
    last_surviving={}
    for row in analysis['committed_writes']:
        if row['tx'] in removed_set:
            compensation.append({'tx':row['tx'],'key':row['key'],'removed_value':row['after'],
                                 'restored_value':last_surviving.get(row['key'],initial[row['key']]),
                                 'restored_source':last_surviving.get(row['key'],(INITIAL_SOURCE,None))[0],
                                 'lsn':row['lsn']})
        else:
            last_surviving[row['key']]=(row['tx'],row['after'])
    checksum=hashlib.sha256(json.dumps(new_ledger,sort_keys=True).encode()).hexdigest()
    source=json.dumps({'initial':initial,'records':records,'target':target},sort_keys=True)
    return {'simulation':True,'model_version':MODEL_VERSION,'boundary':BOUNDARY,
            'target':target,'committed':analysis['committed'],'aborted':analysis['aborted'],
            'losers':analysis['losers'],'checkpoints':analysis['checkpoints'],
            'edges':edges,'read_log':analysis['read_log'],
            'removed':removed,'survivors':survivors,'removal_reasons':removal_reasons,
            'derived_records':derived,'ledger_before':analysis['state'],'ledger_after':new_ledger,
            'oracle':oracle,'invariant_passed':invariant_passed,
            'compensation_trace':compensation,
            'provenance_before':analysis['provenance'],'provenance_after':re_analysis['provenance'],
            'result_sha256':checksum,'input_sha256':hashlib.sha256(source.encode()).hexdigest()}
