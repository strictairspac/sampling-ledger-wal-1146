from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import Rollback, LogSet
from src.services.rollback_engine import plan_rollback, derived_set_name
from src.utils.serialize import record

def list_rollbacks(db):
    return [record(x) for x in db.scalars(select(Rollback).order_by(Rollback.id.desc()).limit(100))]

def get_rollback(db,ident):
    obj=db.get(Rollback,ident)
    if obj is None:raise HTTPException(404,'撤回记录不存在')
    return obj

def create_rollback(db,data):
    logs=db.get(LogSet,data.log_set_id)
    if logs is None:raise HTTPException(404,'日志集不存在')
    # Idempotency: retries for the same source/target return the existing derived
    # set instead of generating a duplicate (failed attempts are never persisted).
    existing=db.scalar(select(Rollback).where(Rollback.log_set_id==data.log_set_id,
                                              Rollback.target_tx==data.target_tx))
    if existing is not None:
        existing.reused=True
        return record(existing),True
    try:
        result=plan_rollback(logs.initial,logs.records,data.target_tx)
    except ValueError as exc:
        raise HTTPException(422,str(exc))
    name=derived_set_name(logs.name,data.target_tx)
    if db.scalar(select(LogSet.id).where(LogSet.name==name)) is not None:
        raise HTTPException(409,f'派生日志集名称已存在：{name}')
    label=data.label.strip() or f'撤回 {data.target_tx} 及连带依赖'
    derived=LogSet(name=name,initial=logs.initial,records=result['derived_records'],
                   description=f"源自「{logs.name}」选择性撤回 {data.target_tx} 的派生日志集，可继续演练")
    db.add(derived);db.flush()
    obj=Rollback(log_set_id=logs.id,target_tx=data.target_tx,derived_log_set_id=derived.id,
                 label=label,status='completed',reused=False,
                 snapshot={'log_set':record(logs),'target_tx':data.target_tx},
                 result={k:v for k,v in result.items() if k!='derived_records'}
                        | {'derived_log_set_id':derived.id,'derived_name':name})
    db.add(obj);db.flush()
    return record(obj),False
