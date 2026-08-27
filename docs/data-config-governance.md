# 依赖、配置与用户数据治理

> 状态：当前实现（M8 数据格式切换已完成）
> 最近更新：2026-08-26

## 1. 依赖单一事实来源

项目只使用 `pyproject.toml` 声明直接依赖，使用 `uv.lock` 固定完整依赖图。
`requirements.txt` 已删除，不再维护第二份容易漂移的依赖列表。

```powershell
uv sync --frozen --extra gui --extra server --extra ai
uv sync --frozen --all-extras --group dev
uv sync --frozen --extra voice --extra kws
```

修改依赖后必须运行 `uv lock`，并通过统一质量门禁。可选能力继续由
`[project.optional-dependencies]` 管理，并按 GUI、Server、AI、数据、视觉、语音、KWS
和硬件域拆分；`full` 用于需要全部能力的开发机和集成环境。

## 2. Built-in 与用户数据

内置动作和技能定义属于应用版本，由代码中的不可变目录交付。用户数据属于运行环境，默认
位于 `data/`，不提交到版本库：

```text
data/
├── profiles/
│   └── <robot-profile-id>/
│       ├── actions/library.json
│       ├── workflows/<name>.workflow.json
│       ├── drafts/<workflow-id>.draft.workflow.json
│       └── trajectories/<arm>/
├── skills/
│   └── <domain>/
│       └── <id>.skill.json
└── schemas/
    ├── action-library.schema.json
    ├── skill.schema.json
    └── workflow.schema.json
```

应用组合根启动时只安装完全缺失的动作库或空技能目录；已存在的用户文件永不被内置目录
覆盖。内置 Action/Skill 和编辑器 JSON Schema 均来自 `src/builtin_catalogs/` 的版本化 JSON
资源，Python 不再维护第二份完整技能数据。自动化测试使用独立临时数据根，不读取或迁移
工作站的真实 `data/`。

数据路径配置：

```toml
[data]
robot_data_dir = "data"
actions_library_directory = ""
workflows_directory = ""
workflow_drafts_directory = ""
skill_library_directory = ""
trajectories_directory = ""
```

覆盖项留空时，Action、Workflow、Draft 和 Trajectory 从活动 Robot Profile 目录推导；
Skill 保持跨机械臂共享。显式相对路径以项目根目录为基准，且目标文档中的
`robot_profile_id` 仍必须与活动 Profile 完全一致。

轨迹录制由应用服务在 `profiles/<robot-profile-id>/trajectories/<arm>/` 下分配递增文件名并自动保存。GUI 不得把轨迹
写入 `src/`，也不在每次录制前要求用户选择文件系统路径；文件选择器仅用于选择已有轨迹。

## 3. 文档格式

动作库：

```json
{
  "$schema": "../../../schemas/action-library.schema.json",
  "schema": "robot_llm.actions",
  "schema_version": 3,
  "robot_profile_id": "realman-rm75-dual",
  "actions": []
}
```

工作流文件（GUI 中仍称“任务”）：

```json
{
  "$schema": "../../../schemas/workflow.schema.json",
  "schema": "robot_llm.workflow",
  "schema_version": 5,
  "robot_profile_id": "realman-rm75-dual",
  "workflow_id": "demo",
  "name": "demo",
  "revision": 1,
  "root": {"kind": "sequence", "children": []},
  "presentation": {"positions": {}}
}
```

单个技能文件：

```json
{
  "$schema": "../../schemas/skill.schema.json",
  "schema": "robot_llm.skill",
  "schema_version": 2,
  "skill": {}
}
```

Action 使用 schema v3、Skill 使用 schema v2、Workflow 使用 schema v5。未知 schema、未来版本、重复/缺失稳定 ID、重复
动作名称、损坏节点和非法文件名会被显式拒绝，不会被当成空库或自动回退成内置数据。
Action 库、Workflow 顶层和 Workflow 内每个 Action 快照都携带同一个
`robot_profile_id`。Workflow 的 `root` 保存结构化 Sequence/Action/Loop/Parallel/Subworkflow，`presentation` 只保存布局；运行状态由
ExecutionRuntime 持有，不进入定义文件。

## 4. Robot Profile 隔离与迁移

Robot Profile 默认由 `robot.provider + provider model` 生成，也可通过
`[robot].profile_id` 显式指定。启动时只挂载活动 Profile 的动作库、工作流、草稿和轨迹；
Repository、WorkflowCompiler 与 ActionEngine 会分别在持久化、编译和硬件执行前拒绝
Profile 不一致的数据，不能把 RealMan 的 Action/Workflow 直接交给 Tianji 执行。

首次使用 RealMan Profile 时，旧共享目录中的 Action、Workflow、Draft 和 Trajectory
会以“目标不存在才复制”的方式写入对应 Profile，并补齐 Profile 标识；原文件不删除，重复
启动不重复迁移。Tianji 等其他 Provider 不继承这批旧 RealMan 数据，初始动作库为空。

## 5. 一次性前向迁移

普通 Repository/Registry 的 `load`、`list` 操作保持只读。历史 Action/Skill 集合只允许
由显式工具迁移到当前目录格式：

```powershell
robot-library-data validate
robot-library-data migrate
robot-workflow-data
robot-workflow-data --apply
```

`validate` 只校验活动目录并输出数量和 SHA-256 语义指纹，不创建目录、备份或改写源
文件；`migrate` 读取显式 legacy 输入，在临时目录生成并重新加载当前格式，指纹一致后
才发布。增加 `--archive-legacy` 时，旧集合文件移动到
`data/migration-backups/catalog-v1/`：

1. 在内存中补齐旧技能缺失的 v1 字段，并完整解析、校验全部领域对象。
2. 在临时目录生成 Action 集合和按领域拆分的 Skill 单文件。
3. 重新加载并比较数量、稳定 ID、完整参数及规范 JSON 语义指纹。
4. 逐文件原子发布；可选移动旧源到可恢复备份目录。
5. runtime 只扫描当前目录，不包含历史集合解析分支。

`robot-workflow-data` 默认 dry-run，完整解析 `data/tasks` 中的 `.task`/旧 `.workflow`，检查
目标冲突；`--apply` 在临时目录生成并重新加载全部 WorkflowDocument v5，验证成功后原子
发布到目标 Profile 的 `workflows/`，再把所有旧任务及 `.bak` 移入
`data/migration-backups/workflow-v5/`。正常 Repository 只认识 `*.workflow.json`。

迁移不是长期兼容双栈。未来版本高于当前程序时直接失败，必须先升级应用；迁移或解析失败
不会覆盖原文件。需要人工恢复时，先停止应用，保留故障文件，再从 `.v0.bak` 复制恢复并
根据错误信息处理。

> M8 迁移已完成：活动数据为 46 actions / 13 skills / 17 workflows；旧 Action/Skill
> 集合和 `.task`/旧 `.workflow` 仅保留在迁移备份中，runtime 不包含旧格式入口。

## 6. 当前用户数据结构

M8 已将用户数据直接切换为以下结构：

```text
data/
├── profiles/
│   ├── realman-rm75-dual/
│   │   ├── actions/library.json
│   │   ├── workflows/<name>.workflow.json
│   │   ├── drafts/<workflow-id>.draft.workflow.json
│   │   └── trajectories/<arm>/
│   └── tianji-tianji-dual/
│       └── ...
├── skills/
│   ├── manipulation/
│   │   └── <name>.skill.json
│   └── <domain>/
│       └── <name>.skill.json
└── schemas/
    ├── action-library.schema.json
    ├── skill.schema.json
    └── workflow.schema.json
```

约束：

- 用户可见“任务”只有 `*.workflow.json` 一种正式格式；`.task` 不再保存派生执行快照。
- Robot Profile 是 Action、Workflow、Draft、Trajectory 的强隔离边界，不是展示标签。
- WorkflowDocument 区分结构化控制流与 presentation 元数据，执行状态不写入定义。
- 动作 ID 全局唯一，参数使用稳定机器字段和规范 JSON 类型；中文标签由 ActionSchema 派生。
- 每个 `*.skill.json` 只定义一个 Skill；目录只用于组织，skill category 仍由文档字段声明。
- Skill Registry 递归、确定性扫描文件，并在全部文件、跨文件 ID、动作类型和参数绑定
  校验成功后一次替换内存目录；SkillStep 保存可执行快照，`action_name` 是展示名称而非
  Action Catalog 外键；不维护手写 index。
- Python 源码不再维护完整动作/技能数据副本；内置示例同样由版本化 JSON 资源交付。
- `*.workflow.json`/`*.skill.json` 使用 `$schema` 关联版本控制内 JSON Schema，获得编辑器
  高亮、补全和校验。

目标配置使用目录而非集合文件：

```env
ROBOT_DATA_DIR=data
ACTIONS_LIBRARY_DIRECTORY=
WORKFLOWS_DIRECTORY=
WORKFLOW_DRAFTS_DIRECTORY=
SKILL_LIBRARY_DIRECTORY=
TRAJECTORIES_DIRECTORY=
```

切换已按 dry-run、备份、转换、重新加载、数量/ID/参数/语义比对和原子发布完成。运行时
旧格式读取已删除；启动时只保留一次、窄范围且幂等的 RealMan 共享数据 Profile 迁移，
不会为 Tianji 或其他 Profile 推断兼容关系。

## 7. 启动配置校验

可以在不启动 Qt、网络服务和硬件的情况下检查配置：

```powershell
uv run robot-llm --check-config --simulation --disable-websocket
```

返回码：

- `0`：配置通过；可能仍有不阻塞启动的警告。
- `2`：配置无法解析或存在阻塞启动的错误。

当前集中检查日志级别、日志目录与保留周期、有效端口、正数超时和容量、数据路径冲突、活动硬件端口、
WebSocket 暴露方式以及示例占位凭据。配置解析错误不会回显被拒绝的原始值。

## 8. 敏感信息策略

- `config/config.toml` 保存本机非敏感配置，`.env` 保存密钥与部署覆盖；两者均被版本库忽略。
- 密钥、token、password、secret 和 credential 字段在诊断映射中统一显示为
  `<redacted>`。
- 示例占位凭据会导致启动校验失败；空 WebSocket token 表示所有写操作保持锁定。
- 非本机 WebSocket 监听会提示只读暴露或 `wss://` 反向代理要求。
- 日志、异常和迁移错误只记录字段名、错误类别和文件名，不记录凭据或完整配置快照。

TOML 与环境覆盖仅在组合根解析一次，结果冻结为 Runtime、Data、DataCollection、Server、
Secret、Execution、LLM、Robot、Device、Vision 和 Voice settings。业务模块只接收
所需快照，不允许读取全局配置或在构造器中回退到环境变量。数据采集配置和视觉天平
凭据也遵循同一入口；原 `Config` 公共单例和领域 `get_*` 方法已删除。
