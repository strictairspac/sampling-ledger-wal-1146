"""Shared in-process stubs for service/route tests (stdlib only, no third-party deps).

Installs fake sqlalchemy/fastapi/model modules once so every test class shares
the same HTTPException identity and fake ORM classes.
"""
import sys,types

class FakeDB:
    def __init__(self):
        self.log_sets={};self.rollbacks={};self._log_id=0;self._rb_id=0
        self.scalar_returns=None
    def get(self,model,ident):
        table=self.log_sets if model.__name__=='LogSet' else self.rollbacks
        return table.get(ident)
    def scalar(self,query):return self.scalar_returns
    def add(self,obj):
        if type(obj).__name__=='LogSet':
            self._log_id+=1;obj.id=self._log_id;self.log_sets[obj.id]=obj
        else:
            self._rb_id+=1;obj.id=self._rb_id;self.rollbacks[obj.id]=obj
    def flush(self):pass
    def commit(self):pass

class HTTPException(Exception):
    def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail

class _Column:
    def __eq__(self,other):return None
    def __hash__(self):return 0

class _ColumnMeta(type):
    def __getattr__(cls,name):return _Column()

class FakeSelect:
    def where(self,*a,**k):return self
    def order_by(self,*a,**k):return self
    def limit(self,*a,**k):return self

class Router:
    def __init__(self,**kw):pass
    def get(self,*a,**k):return lambda f:f
    def post(self,*a,**k):return lambda f:f

class Depends:
    def __init__(self,*a):pass

class Response:
    def __init__(self,content,media_type=None,headers=None):
        self.content=content;self.media_type=media_type;self.headers=headers

class RollbackInput:
    def __init__(self,**kw):self.__dict__.update(kw)

_INSTALLED=False
_CONTEXT=None

def install():
    global _INSTALLED,_CONTEXT
    if _INSTALLED:return _CONTEXT
    models=types.ModuleType('src.models.entities')
    class LogSet(metaclass=_ColumnMeta):
        def __init__(self,**kw):self.__dict__.update(kw)
    class Rollback(metaclass=_ColumnMeta):
        def __init__(self,**kw):self.__dict__.update(kw)
    models.LogSet=LogSet;models.Rollback=Rollback
    engine_mod=types.ModuleType('src.services.rollback_engine')
    from src.services.rollback_engine import plan_rollback as real_plan,derived_set_name
    engine_mod.plan_rollback=real_plan
    engine_mod.derived_set_name=derived_set_name
    serialize=types.ModuleType('src.utils.serialize')
    serialize.record=lambda obj:dict(obj.__dict__)
    http=types.ModuleType('fastapi')
    http.HTTPException=HTTPException;http.APIRouter=Router;http.Depends=Depends
    responses=types.ModuleType('fastapi.responses');responses.Response=Response
    sqlalchemy=types.ModuleType('sqlalchemy');sqlalchemy.select=lambda *a:FakeSelect()
    cfg=types.ModuleType('src.config.database');cfg.get_db=lambda:None
    mw=types.ModuleType('src.middlewares.auth');mw.current_user=lambda:None;mw.writable=lambda:None
    schema=types.ModuleType('src.schemas.rollback');schema.RollbackInput=RollbackInput
    ctrl=types.ModuleType('src.controllers.actions');ctrl.audit=lambda *a:None
    for name,mod in [('src.models.entities',models),('src.services.rollback_engine',engine_mod),
                     ('src.utils.serialize',serialize),('fastapi',http),('fastapi.responses',responses),
                     ('sqlalchemy',sqlalchemy),('src.config.database',cfg),('src.middlewares.auth',mw),
                     ('src.schemas.rollback',schema),('src.controllers.actions',ctrl)]:
        sys.modules[name]=mod
    _INSTALLED=True
    _CONTEXT=types.SimpleNamespace(models=models,http=http,LogSet=LogSet,Rollback=Rollback,
                                   RollbackInput=RollbackInput,Response=Response)
    return _CONTEXT
