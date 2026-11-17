"""Route-layer test with stubbed FastAPI/auth/DB (stdlib only).

Exercises the /rollbacks call chain end to end: create, detail, export with the
embedded derived log set, idempotent retry, and the 422/404 failure paths.
"""
import os,sys,types,unittest,json
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
from tests.stubs import install,FakeDB

class RollbackRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ctx=install()
        cls.models=ctx.models;cls.http=ctx.http;cls.RollbackInput=ctx.RollbackInput
        from src.routes import rollbacks as routes
        cls.routes=routes
        from tests.test_rollback import INITIAL,RECORDS
        cls.initial=INITIAL;cls.records=RECORDS
    def _db(self):
        db=FakeDB()
        src=self.models.LogSet(id=1,name='依赖账本',initial=self.initial,records=self.records,description='')
        db.log_sets[1]=src;db._log_id=1;db.scalar_returns=None
        return db
    def test_full_chain_create_detail_export_retry_and_failures(self):
        db=self._db()
        user=types.SimpleNamespace(email='operator@haili.local')
        created=self.routes.create(self.RollbackInput(log_set_id=1,target_tx='T1',label=''),db,user)
        self.assertFalse(created['reused'])
        self.assertIn('id',created)
        detail=self.routes.detail(created['id'],db,user)
        self.assertEqual(detail['target_tx'],'T1')
        self.assertTrue(detail['result']['invariant_passed'])
        exported=self.routes.export(created['id'],db,user)
        payload=json.loads(exported.content)
        self.assertEqual(payload['derived_log_set']['name'],'依赖账本 - 撤回T1派生集')
        self.assertTrue(len(payload['derived_log_set']['records'])>=1)
        self.assertIn('attachment; filename="rollback-',exported.headers['Content-Disposition'])
        # Idempotent retry.
        db.scalar_returns=list(db.rollbacks.values())[0]
        retried=self.routes.create(self.RollbackInput(log_set_id=1,target_tx='T1',label=''),db,user)
        self.assertTrue(retried['reused']);self.assertEqual(retried['id'],created['id'])
        self.assertEqual(len(db.log_sets),2);self.assertEqual(len(db.rollbacks),1)
        # Failures: invalid target 422, missing source 404, missing record 404.
        db.scalar_returns=None
        with self.assertRaises(self.http.HTTPException) as ctx:
            self.routes.create(self.RollbackInput(log_set_id=1,target_tx='TX',label=''),db,user)
        self.assertEqual(ctx.exception.status_code,422)
        with self.assertRaises(self.http.HTTPException) as ctx:
            self.routes.create(self.RollbackInput(log_set_id=99,target_tx='T1',label=''),db,user)
        self.assertEqual(ctx.exception.status_code,404)
        with self.assertRaises(self.http.HTTPException) as ctx:
            self.routes.detail(999,db,user)
        self.assertEqual(ctx.exception.status_code,404)
        # Nothing was persisted by the failed attempts.
        self.assertEqual(len(db.rollbacks),1)

if __name__=='__main__':
    unittest.main()
