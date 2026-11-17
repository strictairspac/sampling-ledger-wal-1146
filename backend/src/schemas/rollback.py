from pydantic import BaseModel, Field
class RollbackInput(BaseModel):
    log_set_id: int=Field(gt=0)
    target_tx: str=Field(min_length=1,max_length=30,pattern=r'^[A-Za-z0-9_-]+$')
    label: str=Field(default='',max_length=120)
