"""Stdlib-only tests for the read-provenance WAL engine and selective rollback.

Run from the backend directory with:  python3 -I -m unittest tests.test_rollback -v
"""
import copy,unittest
from src.services.wal_engine import replay,validate,recover,INITIAL_SOURCE
from src.services.rollback_engine import plan_rollback,derived_set_name

# History covering the four required drill scenarios:
#   T1 write A (before checkpoint)
#   checkpoint -> cross-checkpoint dependency still traceable
#   T2 reads A=T1.value then writes B        (direct dependency, cross-checkpoint)
#   T3 reads B=T2.value then writes C        (second hop)
#   T4 writes A=11 (same key as T1, independent later overwrite)
#   T5 writes A=11 again, equal value but a different source
#   T6 writes A=90 reading initial C instead   (unrelated same-key overwrite)
INITIAL={'A':100,'B':50,'C':20}
RECORDS=[
 {'op':'begin','tx':'T1'},
 {'op':'write','tx':'T1','key':'A','value':10},
 {'op':'commit','tx':'T1'},
 {'op':'checkpoint'},
 {'op':'begin','tx':'T2'},
 {'op':'read','tx':'T2','key':'A','value':10},
 {'op':'write','tx':'T2','key':'B','value':60},
 {'op':'commit','tx':'T2'},
 {'op':'begin','tx':'T3'},
 {'op':'read','tx':'T3','key':'B','value':60},
 {'op':'write','tx':'T3','key':'C','value':70},
 {'op':'commit','tx':'T3'},
 {'op':'begin','tx':'T4'},
 {'op':'write','tx':'T4','key':'A','value':11},
 {'op':'commit','tx':'T4'},
 {'op':'begin','tx':'T5'},
 {'op':'write','tx':'T5','key':'A','value':11},
 {'op':'commit','tx':'T5'},
 {'op':'begin','tx':'T6'},
 {'op':'read','tx':'T6','key':'C','value':70},
 {'op':'write','tx':'T6','key':'A','value':90},
 {'op':'commit','tx':'T6'},
]

class ReadValidationTests(unittest.TestCase):
    def test_read_records_observed_value_and_source(self):
        out=replay(INITIAL,RECORDS)
        t2=next(r for r in out['read_log'] if r['tx']=='T2')
        self.assertEqual(t2['actual'],10);self.assertEqual(t2['source'],'T1')
        self.assertEqual(out['provenance']['A'],'T6')
        self.assertEqual(out['provenance']['B'],'T2');self.assertEqual(out['provenance']['C'],'T3')

    def test_dirty_read_rejected_even_if_value_matches(self):
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':10},
              {'op':'begin','tx':'T2'},{'op':'read','tx':'T2','key':'A','value':10}]
        with self.assertRaisesRegex(ValueError,'拒绝脏读'):
            replay(INITIAL,recs)

    def test_read_value_mismatch_rejected(self):
        recs=[{'op':'begin','tx':'T1'},{'op':'read','tx':'T1','key':'A','value':99}]
        with self.assertRaisesRegex(ValueError,'读值不符'):
            replay(INITIAL,recs)

    def test_read_unknown_key_and_bad_value_rejected(self):
        with self.assertRaisesRegex(ValueError,'读取键必须存在'):
            replay(INITIAL,[{'op':'begin','tx':'T1'},{'op':'read','tx':'T1','key':'Z','value':1}])
        with self.assertRaisesRegex(ValueError,'读取数值超限'):
            replay(INITIAL,[{'op':'begin','tx':'T1'},{'op':'read','tx':'T1','key':'A','value':True}])

    def test_read_before_begin_rejected(self):
        with self.assertRaisesRegex(ValueError,'读取事务尚未begin'):
            replay(INITIAL,[{'op':'read','tx':'T9','key':'A','value':100}])

    def test_self_read_attributed_to_self_and_creates_no_external_edge(self):
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':10},
              {'op':'read','tx':'T1','key':'A','value':10},{'op':'commit','tx':'T1'}]
        out=replay(INITIAL,recs)
        self.assertEqual(out['read_log'][0]['source'],'T1')
        self.assertEqual(out['edges'],[])

    def test_initial_source_dependency_recorded(self):
        out=replay(INITIAL,RECORDS)
        t6=next(r for r in out['read_log'] if r['tx']=='T6')
        self.assertEqual(t6['source'],'T3')
        edge=next(e for e in out['edges'] if e['reader']=='T6')
        self.assertEqual(edge['source'],'T3');self.assertEqual(edge['key'],'C')

class ProvenanceTests(unittest.TestCase):
    def test_same_value_overwrite_keeps_distinct_source(self):
        out=replay(INITIAL,RECORDS)
        # T4 and T5 both wrote 11; provenance must point at T5, not collapse onto T4.
        self.assertEqual(out['state']['A'],90)
        self.assertEqual(out['provenance']['A'],'T6')
        recs=RECORDS[:-3]  # stop after T5 commit
        out=replay(INITIAL,recs)
        self.assertEqual(out['state']['A'],11);self.assertEqual(out['provenance']['A'],'T5')

    def test_abort_restores_value_and_provenance(self):
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':10},
              {'op':'commit','tx':'T1'},
              {'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'A','value':10},
              {'op':'abort','tx':'T2'}]
        out=replay(INITIAL,recs)
        self.assertEqual(out['state']['A'],10);self.assertEqual(out['provenance']['A'],'T1')

class RollbackTests(unittest.TestCase):
    def test_multi_hop_cascade_removes_direct_and_indirect_dependents(self):
        plan=plan_rollback(INITIAL,RECORDS,'T1')
        self.assertEqual(plan['removed'],['T1','T2','T3','T6'])
        self.assertEqual(plan['survivors'],['T4','T5'])
        kinds={r['tx']:r['kind'] for r in plan['removal_reasons']}
        self.assertEqual(kinds['T1'],'selected')
        self.assertEqual(kinds['T2'],'cascade');self.assertEqual(kinds['T3'],'cascade')
        t3=next(r for r in plan['removal_reasons'] if r['tx']=='T3')
        self.assertEqual(t3['path'],['T1','T2','T3'])
        self.assertIn('B=60',t3['reason'])

    def test_cross_checkpoint_dependency_is_cascaded(self):
        # T1 committed before the checkpoint; T2 reading it afterwards still depends on it.
        plan=plan_rollback(INITIAL,RECORDS,'T1')
        self.assertIn('T2',plan['removed'])
        edge=next(e for e in plan['edges'] if e['reader']=='T2')
        self.assertEqual(edge['source'],'T1');self.assertEqual(edge['lsn'],6)

    def test_unrelated_same_key_overwrite_survives(self):
        plan=plan_rollback(INITIAL,RECORDS,'T1')
        # T4/T5 overwrite key A independently and must survive; only dependent T6 is removed.
        self.assertNotIn('T4',plan['removed']);self.assertNotIn('T5',plan['removed'])
        self.assertEqual(plan['ledger_after']['A'],11)
        self.assertEqual(plan['provenance_after']['A'],'T5')

    def test_same_value_different_source_rollback(self):
        # Rolling back T4 (first of the two equal 11-writes) must leave T5's 11 intact.
        plan=plan_rollback(INITIAL,RECORDS,'T4')
        self.assertEqual(plan['removed'],['T4'])
        self.assertEqual(plan['ledger_after']['A'],90)
        self.assertEqual(plan['provenance_after']['A'],'T6')
        plan5=plan_rollback(INITIAL,RECORDS,'T5')
        self.assertEqual(plan5['removed'],['T5'])
        self.assertEqual(plan5['ledger_after']['A'],90)

    def test_derived_history_is_replayable_and_oracle_matches(self):
        plan=plan_rollback(INITIAL,RECORDS,'T1')
        self.assertTrue(validate(INITIAL,plan['derived_records']))
        self.assertTrue(plan['invariant_passed'])
        txs={r.get('tx') for r in plan['derived_records'] if r.get('op')!='checkpoint'}
        self.assertEqual(txs,{'T4','T5'})
        self.assertEqual(plan['ledger_after'],{'A':11,'B':50,'C':20})
        self.assertEqual(plan['oracle'],plan['ledger_after'])
        # Checkpoint survives the derivation.
        self.assertIn({'op':'checkpoint'},plan['derived_records'])

    def test_derived_history_can_feed_another_rollback(self):
        first=plan_rollback(INITIAL,RECORDS,'T3')
        self.assertEqual(first['removed'],['T3','T6'])
        second=plan_rollback(INITIAL,first['derived_records'],'T4')
        self.assertEqual(second['removed'],['T4'])
        self.assertTrue(second['invariant_passed'])

    def test_rollback_middle_hop_removes_downstream_only(self):
        plan=plan_rollback(INITIAL,RECORDS,'T2')
        self.assertEqual(plan['removed'],['T2','T3','T6'])
        self.assertIn('T1',plan['survivors'])
        # T6 cascaded away with T3, so A falls back to the independent T5 overwrite.
        self.assertEqual(plan['ledger_after']['A'],11);self.assertEqual(plan['ledger_after']['B'],50)
        self.assertEqual(plan['ledger_after']['C'],20)

    def test_non_committed_target_rejected(self):
        recs=RECORDS+[{'op':'begin','tx':'T7'}]
        with self.assertRaisesRegex(ValueError,'不是已提交事务'):
            plan_rollback(INITIAL,recs,'T7')
        with self.assertRaisesRegex(ValueError,'不是已提交事务'):
            plan_rollback(INITIAL,RECORDS,'TX')
        recs2=RECORDS+[{'op':'begin','tx':'T8'},{'op':'abort','tx':'T8'}]
        with self.assertRaisesRegex(ValueError,'不是已提交事务'):
            plan_rollback(INITIAL,recs2,'T8')

    def test_derived_name_is_deterministic(self):
        self.assertEqual(derived_set_name('晚班','T1'),'晚班 - 撤回T1派生集')

    def test_compensation_trace_explains_every_removed_write(self):
        plan=plan_rollback(INITIAL,RECORDS,'T1')
        removed_writes=[r for r in plan['compensation_trace']]
        txs={r['tx'] for r in removed_writes}
        self.assertEqual(txs,{'T1','T2','T3','T6'})
        t1=next(r for r in removed_writes if r['tx']=='T1')
        self.assertEqual(t1['removed_value'],10);self.assertEqual(t1['restored_value'],100)
        self.assertEqual(t1['restored_source'],INITIAL_SOURCE)

    def test_checkpoint_only_derived_history_is_replayable(self):
        init={'A':100}
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':1},
              {'op':'commit','tx':'T1'},{'op':'checkpoint'}]
        plan=plan_rollback(init,recs,'T1')
        self.assertEqual(plan['derived_records'],[{'op':'checkpoint'}])
        self.assertEqual(plan['ledger_after'],{'A':100})
        self.assertTrue(plan['invariant_passed'])

    def test_empty_derived_history_refused(self):
        init={'A':100}
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':1},
              {'op':'commit','tx':'T1'}]
        with self.assertRaisesRegex(ValueError,'撤回后无可用日志'):
            plan_rollback(init,recs,'T1')

    def test_repeated_reads_dedup_one_edge_per_source_key(self):
        init={'A':100,'B':50}
        recs=[{'op':'checkpoint'},
              {'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':1},
              {'op':'commit','tx':'T1'},
              {'op':'begin','tx':'T2'},{'op':'read','tx':'T2','key':'A','value':1},
              {'op':'read','tx':'T2','key':'A','value':1},
              {'op':'write','tx':'T2','key':'B','value':2},{'op':'commit','tx':'T2'}]
        plan=plan_rollback(init,recs,'T1')
        self.assertEqual([e for e in plan['edges'] if e['reader']=='T2'],
                         [{'reader':'T2','source':'T1','key':'A','value':1,'lsn':6}])
        self.assertEqual(plan['removed'],['T1','T2'])

    def test_aborted_transactions_never_become_rollback_targets_or_dependencies(self):
        init={'A':100}
        recs=[{'op':'checkpoint'},
              {'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':1},
              {'op':'abort','tx':'T1'},
              {'op':'begin','tx':'T2'},{'op':'read','tx':'T2','key':'A','value':100},
              {'op':'write','tx':'T2','key':'A','value':2},{'op':'commit','tx':'T2'}]
        plan=plan_rollback(init,recs,'T2')
        self.assertEqual(plan['removed'],['T2']);self.assertIn('T1',plan['aborted'])
        self.assertEqual(plan['edges'],[])

class LegacyRecoverRegressionTests(unittest.TestCase):
    def test_original_seed_scenario_unchanged(self):
        initial={'sample_A':100,'sample_B':50}
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},
              {'op':'commit','tx':'T1'},{'op':'checkpoint'},
              {'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},
              {'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':60},
              {'op':'commit','tx':'T3'},{'op':'write','tx':'T2','key':'sample_B','value':35},
              {'op':'commit','tx':'T2'}]
        out=recover(initial,recs,10)
        self.assertTrue(out['invariant_passed'])
        self.assertEqual(out['recovered'],{'sample_A':60,'sample_B':50})
        self.assertEqual(out['losers'],['T2']);self.assertEqual(out['checkpoint_lsn'],4)

    def test_recover_with_reads_in_prefix(self):
        recs=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'A','value':10},
              {'op':'commit','tx':'T1'},
              {'op':'begin','tx':'T2'},{'op':'read','tx':'T2','key':'A','value':10},
              {'op':'write','tx':'T2','key':'B','value':60}]
        out=recover(INITIAL,recs,6)
        self.assertEqual(out['recovered'],{'A':10,'B':50,'C':20})
        self.assertEqual(out['losers'],['T2'])

if __name__=='__main__':
    unittest.main()
