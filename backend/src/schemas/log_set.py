from typing import Literal
from pydantic import BaseModel, Field, model_validator
class WalRecord(BaseModel):
    op: Literal['begin','write','read','commit','abort','checkpoint']
    tx: str | None = Field(default=None,max_length=30,pattern=r'^[A-Za-z0-9_-]+$')
    key: str | None = Field(default=None,max_length=50,pattern=r'^[A-Za-z0-9_-]+$')
    value: int | None = Field(default=None,ge=-1000000000,le=1000000000)
    @model_validator(mode='after')
    def _check_payload(self):
        if self.op in ('write','read'):
            if self.key is None:raise ValueError(f'{self.op} 操作必须包含键 key')
            if self.value is None:raise ValueError(f'{self.op} 操作必须包含整数值 value')
        if self.op!='checkpoint' and not self.tx:
            raise ValueError(f'{self.op} 操作必须包含事务标识 tx')
        return self
class LogSetInput(BaseModel):
    name: str=Field(min_length=2,max_length=100)
    initial: dict[str,int]=Field(min_length=1,max_length=30)
    records: list[WalRecord]=Field(min_length=1,max_length=200)
    description: str=Field(default='',max_length=500)
