"""Isolated test runner: python3 -I tests/run_tests.py (run from the backend directory)."""
import os,sys,unittest
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
if __name__=='__main__':
    suite=unittest.defaultTestLoader.discover(os.path.join(ROOT,'tests'),pattern='test_*.py')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
