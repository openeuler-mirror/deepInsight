# DeepInsight 贡献指南

欢迎为 DeepInsight 做出贡献！本指南首先给出“数据库变更指导”，帮助你安全地演进数据模型与迁移脚本。

## 数据库变更指导

数据库相关代码主要位于：
- ORM 模型：`deepinsight/databases/models/`
- 连接配置：`deepinsight/databases/connection.py` 与 `config.yaml` 中的 `database.url`
- 迁移工具：`alembic/`（`env.py`、`versions/` 目录）

### 变更流程（推荐）
1. 明确需求与兼容性目标
   - 优先采用“可向后兼容”的变更：新增字段默认值、可空字段、保留旧字段一段适配期。
   - 尽量避免直接删除/重命名字段，改为“弃用标记 + 迁移期”策略。

2. 修改 ORM 模型
   - 在 `deepinsight/databases/models/a`下 中新增/调整模型字段。
   - 命名规范：
     - 列名使用蛇形（snake_case），含义清晰且稳定；
     - 带有时间戳的列统一为：`created_at`、`updated_at`，类型为 `DateTime`；
     - 带有标志位的列建议为 `Boolean`，并设置合理的 `server_default`。

3. 生成迁移脚本（Alembic）
   - 确保本地或 CI 环境可连接到目标数据库（SQLite/PostgreSQL/MySQL），`config.yaml` 中 `database.url` 正确。
   - 命令：
     ```bash
     alembic revision -m "<简要描述>" --autogenerate
     ```
   - Alembic 会比较当前模型与数据库结构，生成 `alembic/versions/<revision_id>_<slug>.py`。

4. 审阅迁移脚本
   - 检查 `upgrade()` 与 `downgrade()` 的操作是否正确：
     - 是否包含必要的 `server_default`（避免非空新增导致升级失败）；
     - 重命名/删除列是否提供回滚路径；
     - 复杂数据迁移（如合并列、拆分 JSON）需补充“数据迁移逻辑”。

5. 执行迁移
   - 升级到最新：
     ```bash
     alembic upgrade head
     ```
   - 如需回滚：
     ```bash
     alembic downgrade -1
     ```

6. 同步服务与 Schemas
   - 对应更新 Pydantic 请求/响应模型：`deepinsight/service/schemas/*.py`。
   - 服务层返回尽量使用响应模型（Response schemas），避免直接暴露 ORM 对象。
   - CLI/API 层统一使用请求/响应模型，保持入参与出参一致。

7. 兼容性与数据策略
   - 新增字段：
     - 设为可空（`nullable=True`）或提供 `server_default`；
     - 在服务层为旧数据赋默认值，避免空值导致 NPE。
   - 删除字段：
     - 先标记弃用，不在业务代码使用；
     - 下一次大版本迁移时再删除（并在 `downgrade()` 中保留回退逻辑）。
   - JSON/Dict 字段：
     - 统一校验与解析（在 Pydantic validator 中处理字符串 → dict）。

### 常见问题与解决
- 无法生成/安装 WeasyPrint 等依赖：
  - macOS 请安装系统库：`brew install cairo pango gdk-pixbuf libffi`。
- 迁移脚本未包含预期变更：
  - 检查 `env.py` 是否正确加载目标数据库；
  - 确认已保存模型改动，且导入路径未因重命名（如 `papers.py` → `academic.py`）遗漏更新。
- 升级后出现非空约束错误：
  - 需要为新增非空列提供 `server_default` 或在升级前补数据脚本。

### 提交与评审
- 提交信息建议采用约定式：
  - `feat(db): add column <xxx> to <table>`
  - `fix(db): correct type of <xxx> from <old> to <new>`
- PR 清单：
  - [ ] 更新 ORM 模型
  - [ ] 生成并审阅 Alembic 迁移脚本
  - [ ] 更新 Pydantic schemas 与服务层返回
  - [ ] API/CLI 层适配与手动验证
  - [ ] 文档（本文件或 `docs/` 下其他文档）更新

### 本地验证建议
- 使用 SQLite 快速本地验证：在 `config.yaml` 设置 `database.url: sqlite:///deepinsight.db`。
- 如需 PostgreSQL/MySQL，请确保本地或 Docker 已就绪，设置正确连接串。

---
感谢你的贡献！