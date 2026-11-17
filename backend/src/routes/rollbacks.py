import json
from fastapi import APIRouter,Depends
from fastapi.responses import Response
from src.config.database import get_db
from src.middlewares.auth import current_user,writable
from src.schemas.rollback import RollbackInput
from src.services.rollback_service import list_rollbacks,get_rollback,create_rollback
from src.controllers.actions import audit
from src.models.entities import LogSet
from src.utils.serialize import record
router=APIRouter()
@router.get('/rollbacks')
def listing(db=Depends(get_db),user=Depends(current_user)):return list_rollbacks(db)
@router.post('/rollbacks',status_code=201)
def create(data:RollbackInput,db=Depends(get_db),user=Depends(writable)):
    obj,reused=create_rollback(db,data)
    audit(db,user,f'rollback:{"reuse" if reused else "create"}:{obj["id"]}')
    db.commit()
    return {**obj,'reused':reused}
@router.get('/rollbacks/{ident}')
def detail(ident:int,db=Depends(get_db),user=Depends(current_user)):return record(get_rollback(db,ident))
@router.get('/rollbacks/{ident}/export')
def export(ident:int,db=Depends(get_db),user=Depends(current_user)):
    obj=get_rollback(db,ident)
    payload=record(obj)
    if obj.derived_log_set_id:
        logs=db.get(LogSet,obj.derived_log_set_id)
        payload['derived_log_set']=record(logs) if logs else None
    return Response(json.dumps(payload,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="rollback-{ident}.json"'})
