import os
from sqlalchemy import select
from src.config.database import Base,engine,Session
from src.models.entities import User,LogSet,CrashCase
from src.utils.security import hash_password
RECORDS=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},{'op':'commit','tx':'T1'},{'op':'checkpoint'},{'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},{'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':60},{'op':'commit','tx':'T3'},{'op':'write','tx':'T2','key':'sample_B','value':35},{'op':'commit','tx':'T2'}]
# Selective-rollback drill history: cross-checkpoint read dependency, two-hop
# cascade, equal values from different sources (T4/T5), and an unrelated later
# overwrite of the same key (T6 reads C instead of A).
ROLLBACK_RECORDS=[
 {'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':10},{'op':'commit','tx':'T1'},
 {'op':'checkpoint'},
 {'op':'begin','tx':'T2'},{'op':'read','tx':'T2','key':'sample_A','value':10},
 {'op':'write','tx':'T2','key':'sample_B','value':60},{'op':'commit','tx':'T2'},
 {'op':'begin','tx':'T3'},{'op':'read','tx':'T3','key':'sample_B','value':60},
 {'op':'write','tx':'T3','key':'sample_C','value':70},{'op':'commit','tx':'T3'},
 {'op':'begin','tx':'T4'},{'op':'write','tx':'T4','key':'sample_A','value':11},{'op':'commit','tx':'T4'},
 {'op':'begin','tx':'T5'},{'op':'write','tx':'T5','key':'sample_A','value':11},{'op':'commit','tx':'T5'},
 {'op':'begin','tx':'T6'},{'op':'read','tx':'T6','key':'sample_C','value':70},
 {'op':'write','tx':'T6','key':'sample_A','value':90},{'op':'commit','tx':'T6'},
]
ROLLBACK_INITIAL={'sample_A':100,'sample_B':50,'sample_C':20}
def seed():
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true').lower()!='true':return
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@haili.local'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('Recovery2026!')))
        logs=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-晚班'))
        if logs is None:
            logs=LogSet(name='海砾采样账本-晚班',description='A初始100份、B初始50份，T1提交后检查点，T2未提交，T3已提交',initial={'sample_A':100,'sample_B':50},records=RECORDS);db.add(logs);db.flush()
        for name,point in [('断电点10-撤销T2',10),('完整提交点11',11),('初始点0',0)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=logs.id,crash_after=point))
        drill=db.scalar(select(LogSet).where(LogSet.name=='选择性撤回演练-依赖账本'))
        if drill is None:
            drill=LogSet(name='选择性撤回演练-依赖账本',
                         description='含read读取记录：跨检查点依赖、多跳级联、同值不同来源T4/T5、无关同键覆盖T6',
                         initial=ROLLBACK_INITIAL,records=ROLLBACK_RECORDS);db.add(drill);db.flush()
        case_name='撤回演练-完整历史22条'
        if not db.scalar(select(CrashCase).where(CrashCase.name==case_name)):
            db.add(CrashCase(name=case_name,log_set_id=drill.id,crash_after=len(ROLLBACK_RECORDS)))
        db.commit()
if __name__=='__main__':seed()
