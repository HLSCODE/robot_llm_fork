# 依赖、配置与用户数据治理

> 状态：当前实现 + M8 目标迁移
> 最近更新：2026-08-07

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
├── actions/
│   └── library.json
├── tasks/
├── skills/
│   └── <domain>/
│       └── <id>.skill.json
└── schemas/
    ├── action-library.schema.json
    └── skill.schema.json
```

应用组合根启动时只安装完全缺失的动作库或空技能目录；已存在的用户文件永不被内置目录
覆盖。内置 Action/Skill 和编辑器 JSON Schema 均来自 `src/builtin_catalogs/` 的版本化 JSON
资源，Python 不再维护第二份完整技能数据。自动化测试使用独立临时数据根，不读取或迁移
工作站的真实 `data/`。

数据路径配置：

```env
ROBOT_DATA_DIR=data
ACTIONS_LIBRARY_DIRECTORY=
TASKS_DIRECTORY=
SKILL_LIBRARY_DIRECTORY=
```

后三项留空时从 `ROBOT_DATA_DIR` 推导。显式相对路径以项目根目录为基准；生产部署可以使用
绝对路径将用户数据放在独立持久卷。

## 3. 文档格式

动作库：

```json
{
  "$schema": "../schemas/action-library.schema.json",
  "schema": "robot_llm.actions",
  "schema_version": 2,
  "actions": []
}
```

任务文件：

```json
{
  "schema": "robot_llm.task",
  "schema_version": 1,
  "entries": []
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

Action/Skill 新写入使用 schema v2，Task 暂时仍为 schema v1。未知 schema、未来版本、
重复/缺失稳定 ID、重复动作名称、损坏条目和非法任务路径会被显式拒绝，不会被当成空库
或自动回退成内置数据。

## 4. 一次性前向迁移

普通 Repository/Registry 的 `load`、`list` 操作保持只读。旧版 Action schema v1 集合和
`robot_llm.skills` 集合只允许由显式工具迁移为 v2 目录：

```powershell
robot-library-data validate
robot-library-data migrate
```

`validate` 只校验活动 v2 目录并输出数量和 SHA-256 语义指纹，不创建目录、备份或改写源
文件；`migrate` 读取显式 legacy 输入，在临时目录生成并重新加载全部 v2 文件，指纹一致后
才发布。增加 `--archive-legacy` 时，旧集合文件移动到
`data/migration-backups/catalog-v1/`：

1. 在内存中补齐旧技能缺失的 v1 字段，并完整解析、校验全部领域对象。
2. 在临时目录生成 Action 集合和按领域拆分的 Skill 单文件。
3. 重新加载并比较数量、稳定 ID、完整参数及规范 JSON 语义指纹。
4. 逐文件原子发布；可选移动旧源到可恢复备份目录。
5. runtime 只扫描 v2 目录，不包含 v0/v1 集合解析分支。

迁移不是长期兼容双栈。未来版本高于当前程序时直接失败，必须先升级应用；迁移或解析失败
不会覆盖原文件。需要人工恢复时，先停止应用，保留故障文件，再从 `.v0.bak` 复制恢复并
根据错误信息处理。

> M8 当前进度：Action/Skill v2 目录迁移已经完成；活动数据为 46 actions / 13 skills，
> 两个旧集中式文件仅保留在迁移备份中。G-032 剩余范围是 `.task`/`.workflow` 到唯一
> `*.workflow.json` 的迁移。

## 5. M8 剩余目标用户数据结构

M8 将用户数据直接切换为以下结构：

```text
data/
├── actions/
│   └── library.json
├── workflows/
│   └── <name>.workflow.json
├── drafts/
│   └── <workflow-id>.draft.workflow.json
├── skills/
    ├── manipulation/
    │   └── <name>.skill.json
    └── <domain>/
        └── <name>.skill.json
└── schemas/
    ├── action-library.schema.json
    ├── skill.schema.json
    └── workflow.schema.json
```

约束：

- 用户可见“任务”只有 `*.workflow.json` 一种正式格式；`.task` 不再保存派生执行快照。
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
```

切换步骤固定为 dry-run、备份、转换、重新加载、数量/ID/参数/语义指纹比对、原子发布。
只有全部验证成功才能更新活动配置；随后删除旧路径配置、旧格式读取和隐式迁移，不设置
兼容开关。

## 6. 启动配置校验

可以在不启动 Qt、网络服务和硬件的情况下检查配置：

```powershell
uv run robot-llm --check-config --simulation --disable-websocket
```

返回码：

- `0`：配置通过；可能仍有不阻塞启动的警告。
- `2`：配置无法解析或存在阻塞启动的错误。

当前集中检查日志级别、日志目录与保留周期、有效端口、正数超时和容量、数据路径冲突、活动硬件端口、
WebSocket 暴露方式以及示例占位凭据。配置解析错误不会回显被拒绝的原始值。

## 7. 敏感信息策略

- `config.env` 仅属于本机环境并被版本库忽略。
- 密钥、token、password、secret 和 credential 字段在诊断映射中统一显示为
  `<redacted>`。
- 示例占位凭据会导致启动校验失败；空 WebSocket token 表示所有写操作保持锁定。
- 非本机 WebSocket 监听会提示只读暴露或 `wss://` 反向代理要求。
- 日志、异常和迁移错误只记录字段名、错误类别和文件名，不记录凭据或完整配置快照。

环境解析仅在组合根执行一次，结果冻结为 Runtime、Data、DataCollection、Server、
Secret、Execution、LLM、Robot、Device、Vision 和 Voice settings。业务模块只接收
所需快照，不允许读取全局配置或在构造器中回退到环境变量。数据采集配置和视觉天平
凭据也遵循同一入口；原 `Config` 公共单例和领域 `get_*` 方法已删除。
