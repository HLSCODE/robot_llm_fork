# 依赖、配置与用户数据治理

> 状态：当前实现  
> 最近更新：2026-07-30

## 1. 依赖单一事实来源

项目只使用 `pyproject.toml` 声明直接依赖，使用 `uv.lock` 固定完整依赖图。
`requirements.txt` 已删除，不再维护第二份容易漂移的依赖列表。

```powershell
uv sync --frozen
uv sync --frozen --group dev
uv sync --frozen --extra voice
```

修改依赖后必须运行 `uv lock`，并通过统一质量门禁。可选能力继续由
`[project.optional-dependencies]` 管理，后续按 GUI、视觉和硬件域进一步拆分。

## 2. Built-in 与用户数据

内置动作和技能定义属于应用版本，由代码中的不可变目录交付。用户数据属于运行环境，默认
位于 `data/`，不提交到版本库：

```text
data/
├── actions_library.json
├── tasks/
└── skills/
    └── skill_library.json
```

应用组合根启动时只安装缺失的动作库或技能库；已存在的用户文件永不被内置目录覆盖。
内置动作仅包含可在 simulation 中安全执行的等待动作，内置技能继续经过统一 action schema
验证。自动化测试使用独立临时数据根，不读取或迁移工作站的真实 `data/`。

数据路径配置：

```env
ROBOT_DATA_DIR=data
ACTIONS_LIBRARY_PATH=
TASKS_DIRECTORY=
SKILL_LIBRARY_PATH=
```

后三项留空时从 `ROBOT_DATA_DIR` 推导。显式相对路径以项目根目录为基准；生产部署可以使用
绝对路径将用户数据放在独立持久卷。

## 3. 文档格式

动作库：

```json
{
  "schema": "robot_llm.actions",
  "schema_version": 1,
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

技能库：

```json
{
  "schema": "robot_llm.skills",
  "schema_version": 1,
  "skills": []
}
```

所有新写入均使用 schema v1。未知 schema、未来版本、缺失稳定 ID、损坏条目和非法任务路径
会被显式拒绝，不会被当成空库或自动回退成内置数据。

## 4. 一次性前向迁移

旧版动作/任务裸数组和旧版 `{"skills": [...]}` 在首次读取时执行一次 v0 → v1 前向迁移：

1. 在内存中补齐 v1 新增字段，并完整解析、校验全部领域对象。
2. 校验成功后在同目录保存原始字节副本 `<文件名>.v0.bak`。
3. 刷新临时文件并使用同目录原子替换发布。
4. 后续读取只接受当前 schema，不运行旧版业务分支。

迁移不是长期兼容双栈。未来版本高于当前程序时直接失败，必须先升级应用；迁移或解析失败
不会覆盖原文件。需要人工恢复时，先停止应用，保留故障文件，再从 `.v0.bak` 复制恢复并
根据错误信息处理。

## 5. 启动配置校验

可以在不启动 Qt、网络服务和硬件的情况下检查配置：

```powershell
python run.py --check-config --simulation --disable-websocket
```

返回码：

- `0`：配置通过；可能仍有不阻塞启动的警告。
- `2`：配置无法解析或存在阻塞启动的错误。

当前集中检查日志级别、有效端口、正数超时和容量、数据路径冲突、活动硬件端口、
WebSocket 暴露方式以及示例占位凭据。配置解析错误不会回显被拒绝的原始值。

## 6. 敏感信息策略

- `config.env` 仅属于本机环境并被版本库忽略。
- 密钥、token、password、secret 和 credential 字段在诊断映射中统一显示为
  `<redacted>`。
- 示例占位凭据会导致启动校验失败；空 WebSocket token 表示所有写操作保持锁定。
- 非本机 WebSocket 监听会提示只读暴露或 `wss://` 反向代理要求。
- 日志、异常和迁移错误只记录字段名、错误类别和文件名，不记录凭据或完整配置快照。

本批没有拆分超过 1000 行的 `Config` 单例；按领域拆分 typed settings 仍由 G-009 跟踪。
