"""Quiescent-checkpoint WAL teaching model with strict write locks, reads and redo/undo."""
import copy,hashlib,json,re

INITIAL_SOURCE='INITIAL'

def replay(initial,records):
    """Canonical replay: validates the whole history and tracks value provenance.

    Every read is checked against the current state: dirty reads (another active
    transaction holds the key) and declared/actual value mismatches are rejected.
    Each accepted read records the committed source of the value it observed, so
    same-value overwrites stay distinguishable by their originating transaction.
    """
    if not initial or len(initial)>30 or not records or len(records)>200: raise ValueError('需1—30个键及1—200条日志')
    for key,value in initial.items():
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}',key) or isinstance(value,bool) or not isinstance(value,int) or abs(value)>1000000000:
            raise ValueError('初始键为字母数字下划线短横线，值为绝对值不超过10亿的整数')
    state=copy.deepcopy(initial); provenance={key:INITIAL_SOURCE for key in initial}
    active={};used=set();locks={}
    committed=[];aborted=[];checkpoints=[];read_log=[];edges=[];committed_writes=[]
    for lsn,r in enumerate(records,1):
        op=r.get('op');tx=r.get('tx')
        if op=='checkpoint':
            if active:raise ValueError(f'LSN {lsn}: 仅支持无活动事务的静止检查点')
            checkpoints.append(lsn);continue
        if not tx:raise ValueError(f'LSN {lsn}: 需要事务标识')
        if op=='begin':
            if tx in used:raise ValueError(f'LSN {lsn}: 事务标识不得重复')
            active[tx]={'writes':[],'reads':[]};used.add(tx)
        elif op=='write':
            if tx not in active:raise ValueError(f'LSN {lsn}: 写入事务尚未begin')
            key=r.get('key');value=r.get('value')
            if key not in initial:raise ValueError(f'LSN {lsn}: 写入键必须存在于初始账本')
            if isinstance(value,bool) or not isinstance(value,int) or abs(value)>1000000000:raise ValueError(f'LSN {lsn}: 数值超限')
            if key in locks and locks[key]!=tx:raise ValueError(f'LSN {lsn}: 违反严格写锁，活动事务持有同键')
            row={'lsn':lsn,'tx':tx,'key':key,'before':state[key],'after':value,'before_source':provenance[key]}
            locks[key]=tx;active[tx]['writes'].append(row);state[key]=value
        elif op=='read':
            if tx not in active:raise ValueError(f'LSN {lsn}: 读取事务尚未begin')
            key=r.get('key');value=r.get('value')
            if key not in initial:raise ValueError(f'LSN {lsn}: 读取键必须存在于初始账本')
            if isinstance(value,bool) or not isinstance(value,int) or abs(value)>1000000000:raise ValueError(f'LSN {lsn}: 读取数值超限')
            if key in locks and locks[key]!=tx:raise ValueError(f'LSN {lsn}: 拒绝脏读，键 {key} 正被活动事务 {locks[key]} 持有未提交写入')
            if state[key]!=value:raise ValueError(f'LSN {lsn}: 读值不符，事务 {tx} 声明读取 {value}，当前实际值为 {state[key]}')
            # A read of the transaction's own uncommitted write is attributed to itself;
            # only reads of committed values carry a committed-source dependency.
            source=tx if locks.get(key)==tx else provenance[key]
            row={'lsn':lsn,'tx':tx,'key':key,'declared':value,'actual':state[key],'source':source}
            active[tx]['reads'].append(row);read_log.append(row)
        elif op in ('commit','abort'):
            if tx not in active:raise ValueError(f'LSN {lsn}: 事务未开启或已结束')
            if op=='abort':
                for row in reversed(active[tx]['writes']):
                    state[row['key']]=row['before'];provenance[row['key']]=row['before_source']
                aborted.append(tx)
            else:
                for row in active[tx]['writes']:
                    provenance[row['key']]=tx
                    committed_writes.append(dict(row))
                for row in active[tx]['reads']:
                    if row['source'] not in (INITIAL_SOURCE,tx):
                        edges.append({'reader':tx,'source':row['source'],'key':row['key'],'value':row['actual'],'lsn':row['lsn']})
                committed.append(tx)
            del active[tx]
            locks={k:v for k,v in locks.items() if v!=tx}
        else:raise ValueError(f'LSN {lsn}: 未知操作')
    return {'state':state,'provenance':provenance,'active':active,'committed':committed,'aborted':aborted,
            'losers':sorted(active),'checkpoints':checkpoints,'read_log':read_log,'edges':edges,
            'committed_writes':committed_writes}

def validate(initial,records):
    replay(initial,records)
    return True

def recover(initial,records,crash_after):
    validate(initial,records)
    if not isinstance(crash_after,int) or not 0<=crash_after<=len(records):raise ValueError('崩溃点超出日志范围')
    prefix=records[:crash_after]; state=copy.deepcopy(initial); active={}; winners=[];aborted=[];checkpoint=0;durable=copy.deepcopy(initial);trace=[];writes=[]
    for lsn,r in enumerate(prefix,1):
        op=r['op'];tx=r.get('tx')
        if op=='begin':active[tx]=[]
        elif op=='write':
            row={'lsn':lsn,'tx':tx,'key':r['key'],'before':state[r['key']],'after':r['value']}
            active[tx].append(row);writes.append(row);state[r['key']]=r['value']
            trace.append(dict(row,phase='redo'))
        elif op=='read':
            continue
        elif op=='abort':
            for row in reversed(active[tx]):
                state[row['key']]=row['before'];trace.append(dict(row,phase='redo_abort_undo'))
            del active[tx];aborted.append(tx)
        elif op=='commit':winners.append(tx);del active[tx]
        elif op=='checkpoint':
            checkpoint=lsn;durable=copy.deepcopy(state);trace=[];writes=[];winners=[];aborted=[]
    # Simulated crash durable pages are exactly the last quiescent checkpoint.
    # Recovery repeats history after that point, then undoes all unfinished writes.
    before_undo=copy.deepcopy(state);losers=sorted(active)
    undo=sorted([w for tx in active for w in active[tx]],key=lambda w:w['lsn'],reverse=True)
    for row in undo:
        state[row['key']]=row['before'];trace.append(dict(row,phase='undo_loser'))
    # Independent winner-only oracle from durable checkpoint excludes aborted/unfinished transactions.
    oracle=copy.deepcopy(durable)
    for row in writes:
        if row['tx'] in winners:oracle[row['key']]=row['after']
    checksum=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
    source=json.dumps({'initial':initial,'records':records,'crash_after':crash_after},sort_keys=True)
    return {'simulation':True,'model_version':'strict-wal-quiescent-v1','crash_after':crash_after,'checkpoint_lsn':checkpoint,
            'durable_checkpoint':durable,'after_redo':before_undo,'recovered':state,'oracle':oracle,'invariant_passed':state==oracle,
            'committed':winners,'aborted':aborted,'losers':losers,'redo_count':len([t for t in trace if t['phase']=='redo']),
            'undo_count':len(undo),'trace':trace,'result_sha256':checksum,'input_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'boundary':'Python WAL教学模型；严格写锁、静止检查点、整数键值、日志前缀持久。没有注入真实PostgreSQL崩溃，未执行数据库物理恢复。'}
