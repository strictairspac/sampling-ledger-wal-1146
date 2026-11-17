# 海砾 · 采样账本 WAL 恢复演练
海砾计量数据保障组（8人）用可重放日志替代恢复演练时手工拼接的SQL片段和账本截图。

## 场景与模块
上游为事务日志样例，下游为值班复核人。原流程人工标记提交、表格算恢复余额、截图写结论；痛点是崩溃点不清、撤销顺序容易错、恢复证据缺失。模块为事务日志集 / 故障注入点 / 恢复队列 / 恢复证据 / 选择性撤回，实体LogSet/CrashCase/Recovery/Rollback贯穿全栈。

**Python WAL教学模型，不注入真实PostgreSQL故障，不执行数据库物理恢复。** PostgreSQL真实保存业务输入、冻结快照、任务和结果，Redis实际用于worker互斥，后台Python实际演算恢复历史。

## 启动
```bash
cp .env.example .env
docker compose up -d --build
# http://localhost:18246
# http://localhost:19246/docs
docker compose exec -T backend python -m scripts.seed_demo
docker compose down --remove-orphans
```
SEED_DEMO=true时启动自动执行种子；自然键查重，不覆盖用户修改。初始化失败显式阻止启动，不隐藏结构错误。删除数据需显式down -v。

|账号|密码|权限|典型动作|
|---|---|---|---|
|admin@haili.local|Recovery2026!|读写/导出/审计|查询审计记录|
|operator@haili.local|Recovery2026!|新增日志/场景/运行/导出|设置断电点10|
|viewer@haili.local|Recovery2026!|只读/导出|核对恢复账本|

## 一天的复核剧本
09:00操作员登录；09:05查看海砾采样账本-晚班（A100、B50）；09:10复制日志集保存shift-20261117；09:15在第10条日志后注入断电点；09:20提交恢复；09:25核对检查点LSN4、已提交T3、未提交T2两条写入逆序撤销，最终A60/B50；09:30下载JSON；09:40观察员核对且写操作403；10:00管理员查看/api/audits。

## 算法、格式和失败态
日志为JSON数组，操作为begin/write/read/commit/abort/checkpoint；write与read包含tx/key/value，其他事务操作包含tx，checkpoint不带tx。read按当前实际值核对：键被其他活动事务持有时拒绝脏读，声明值与实际值不符拒绝读值不符；每条接受的read记录实际读到的值及其提交来源（INITIAL或某已提交事务），同值覆盖按来源区分。键必须在初始整数账本存在。事务ID不可复用；严格写锁直到commit/abort释放，同键重叠活动事务拒绝；检查点仅在无活动事务时允许。最多200条日志/30键/整数绝对值不超过10亿。

crash_after=N表示前N条日志已持久化。最近静止检查点保存全部数据页，检查点后数据页假定均未写回（no-force）。恢复重放日志写入和已abort的撤销，再按LSN倒序撤销未结束事务。独立oracle从检查点账本仅应用已提交事务写入，结果须相同。输出重做/撤销轨迹、事务集合、各阶段账本、SHA256和模型版本。空输入、未知键、事务顺序非法、检查点有活动事务、写锁冲突、脏读、读值不符、崩溃点超限均拒绝。模型不涉及页LSN、CLR、模糊检查点或真实磁盘故障；不是完整ARIES实现。

## 选择性撤回（不做整库回退）
误提交已进入检查点时，在「选择性撤回」页选择一个已提交事务。引擎以read记录为依据构建依赖图：事务读取的每个值都按读取瞬间实际观察到的提交来源连边，因此跨检查点依赖照常追踪，同值不同来源的覆盖不会被混并。撤回集合=目标+沿依赖边多跳可达的全部已提交事务（直接及间接依赖），无关的后续写入——包括后来对同键的独立覆盖——一律保留。撤回原因表给出每个级联事务的直接依赖、读取LSN/键/值与完整依赖链。

派生集为全新log_sets行：仅保留幸存事务的操作和检查点，初始账本不变，可再次录入故障场景、恢复或继续撤回；源日志集与旧CrashCase行保持原样。新账本经双路核对（派生历史重放 vs 仅删除撤回来源的提交写入），并输出补偿视图（每条被移除写作用哪个幸存来源/初始值兜底）。同一来源+目标重试返回既有派生集并标记reused，不重复生成；校验失败不落任何数据。撤回仅接受已提交事务，未提交/未知目标返回422。API：GET/POST /rollbacks、GET /rollbacks/{id}、GET /rollbacks/{id}/export。

## 工程与开发
前端原生HTML/CSS/JS+Nginx1.27；后端Python3.11+FastAPI+SQLAlchemy+PyJWT；PostgreSQL16和Redis7；5服务均healthcheck。持久任务队列在PG，单worker通过Redis锁互斥。20条待执行配额，审计记录写入操作，JWT8小时、PBKDF2随机盐密码。后端路由不含/api，Nginx/api/去前缀代理。

目录frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}与backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}。本地开发安装backend/requirements.txt并设DATABASE_URL/REDIS_URL/JWT_SECRET，再运行uvicorn src.main:app；前端需代理/api。migrations记录初始化结构，暂无在线升级器。

环境COMPOSE_PROJECT_NAME控制命名，FRONTEND_PORT/BACKEND_PORT/DB_PORT为18246/19246/17746，DB_PASSWORD控制演示数据库密码，JWT_SECRET签名密钥，SEED_DEMO控制种子。命名卷db-data/redis-data/artifacts；共享artifacts预留，导出由冻结快照即时生成。Google Fonts需外网，业务离线可运行。默认凭据仅本地演示。

共享枚举roles与任务states定义于backend/src/constants/enums.py、frontend/src/constants/enums.js，状态使用models/entities.py/services/recovery_service.py/worker.py/components/common/status.js；日志操作类型在schemas/log_set.py和pages/logs.js中声明/说明。MIT许可。引擎纯标准库，可用`python3 -I tests/run_tests.py`（backend目录）运行25项离线测试，覆盖脏读/读值不符拒绝、跨检查点依赖、同值不同来源、多跳级联、无关同键覆盖、双oracle核对与撤回幂等。
