# 初始化结构
SQLAlchemy metadata.create_all首次建users、log_sets、crash_cases、recoveries、rollbacks、audits。log_sets.name、crash_cases.name唯一，关联使用外键，入队冻结日志和崩溃点快照。暂无在线schema升级器。

## 增量：rollbacks 表（选择性撤回）
rollbacks 记录选择性撤回：log_set_id + target_tx 唯一（uq_rollback_source_target），同一来源/目标重试复用既有派生集，不重复生成；derived_log_set_id 外键指向派生的新 log_sets 行，源日志集与旧 crash_cases 行保持原样。已有命名卷的环境若未自动建表，需在 PG 手动执行：

```sql
CREATE TABLE IF NOT EXISTS rollbacks (
  id SERIAL PRIMARY KEY,
  log_set_id INTEGER NOT NULL REFERENCES log_sets(id),
  target_tx VARCHAR(30) NOT NULL,
  derived_log_set_id INTEGER REFERENCES log_sets(id),
  label VARCHAR(120) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'completed',
  snapshot JSON NOT NULL,
  result JSON,
  reused BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_rollback_source_target UNIQUE (log_set_id, target_tx)
);
```

log_sets.records 的 op 取值扩展为 begin/write/read/commit/abort/checkpoint；旧记录不含 read，引擎完全向后兼容。
