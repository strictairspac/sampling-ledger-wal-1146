"""Service-layer test with stubbed ORM models and DB session (stdlib only).

Verifies selective-rollback idempotency: the first request persists a derived
log set and a rollback row; retrying the same source/target reuses it and never
generates a second derived set. Invalid targets surface as HTTP 422.
"""
import os,sys,types,unittest
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
from tests.stubs import install,FakeDB

class RollbackServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx=install()
        cls.models=ctx.models;cls.http=ctx.http
        from src.services import rollback_service
        cls.svc=rollback_service
        from tests.test_rollback import INITIAL,RECORDS
        cls.initial=INITIAL;cls.records=RECORDS
    def _db_with_source(self):
        db=FakeDB()
        src=self.models.LogSet(id=1,name='依赖账本',initial=self.initial,records=self.records,description='')
        db.log_sets[1]=src;db._log_id=1
        # No existing rollback row and no name collision by default.
        db.scalar_returns=None
        return db
    def test_first_call_creates_derived_set_and_rollback_row(self):
        db=self._db_with_source()
        data=types.SimpleNamespace(log_set_id=1,target_tx='T1',label='')
        obj,reused=self.svc.create_rollback(db,data)
        self.assertFalse(reused)
        self.assertEqual(len(db.log_sets),2)
        derived=db.log_sets[obj['derived_log_set_id']]
        self.assertIn('撤回T1',derived.name)
        self.assertEqual(obj['target_tx'],'T1')
        self.assertTrue(obj['result']['invariant_passed'])
    def test_retry_returns_existing_without_new_derived_set(self):
        db=self._db_with_source()
        data=types.SimpleNamespace(log_set_id=1,target_tx='T1',label='')
        first,_=self.svc.create_rollback(db,data)
        # Second attempt finds the existing rollback row via .scalar.
        existing=list(db.rollbacks.values())[0]
        db.scalar_returns=existing
        second,reused2=self.svc.create_rollback(db,data)
        self.assertTrue(reused2)
        self.assertEqual(second['id'],first['id'])
        self.assertEqual(len(db.log_sets),2)
        self.assertEqual(len(db.rollbacks),1)
    def test_invalid_target_raises_422_without_persisting(self):
        db=self._db_with_source()
        data=types.SimpleNamespace(log_set_id=1,target_tx='TX',label='')
        with self.assertRaises(self.http.HTTPException) as ctx:
            self.svc.create_rollback(db,data)
        self.assertEqual(ctx.exception.status_code,422)
        self.assertEqual(len(db.rollbacks),0);self.assertEqual(len(db.log_sets),1)
    def test_missing_log_set_raises_404(self):
        db=FakeDB()
        data=types.SimpleNamespace(log_set_id=99,target_tx='T1',label='')
        with self.assertRaises(self.http.HTTPException) as ctx:
            self.svc.create_rollback(db,data)
        self.assertEqual(ctx.exception.status_code,404)

if __name__=='__main__':
    unittest.main()
