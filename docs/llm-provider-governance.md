# LLM Provider 治理与规划回归

> 状态：Active  
> 生效日期：2026-07-30  
> 对应总计划：E-010、E-011、E-012

## 1. 目标与边界

本模块统一处理所有 LLM task 的 provider 选择、运行时健康、熔断、显式降级和
调用来源追踪。聊天、指令分类、技能规划、视觉融合和语音复述仍各自拥有 Prompt
和业务结果解析，但不再自行实现 provider 失败策略。

LLM 只产生对话或计划，不持有设备能力，也不能绕过 `CommandRuntime` 的校验、
预览、风险确认和执行审批。

## 2. 运行时结构

```text
TaskRunner / Classifier / SkillPlanner / Vision / Repeat
                         |
                  RoutedLLMClient
              /          |           \
      candidate order  health      provenance
             |       + circuit          |
             +----------+---------------+
                        |
              concrete provider clients
```

- `LLMRegistry` 仍是 provider 实例和关闭生命周期的唯一所有者。
- `RoutedLLMClient` 是绑定 `TaskProfile` 的组合式代理，不拥有或关闭 provider。
- `ProviderHealthTracker` 在 registry 内共享，因此不同 task 对同一 provider
  观察到一致的健康和熔断状态。
- provider adapter 只负责协议转换和统一异常，不感知业务 task。

## 3. 路由、降级与熔断规则

主 provider 的优先级保持为：

1. 单次调用显式传入的 `provider`。
2. `TaskProfile.default_provider`。
3. `LLM_DEFAULT_PROVIDER`。

只有调用方没有显式指定 provider 时，才允许继续按
`LLM_FALLBACK_PROVIDERS` 的顺序尝试降级。默认配置为空，即默认不把请求跨厂商
转发。显式 provider 失败会直接返回错误，不会暗中改用其他厂商。

每个候选 provider 在调用前依次检查：

1. 是否满足 `TaskProfile.required_capabilities`。
2. client 是否完成有效配置。
3. 熔断器是否允许调用。

运行时调用失败会增加连续失败计数。达到
`LLM_CIRCUIT_FAILURE_THRESHOLD` 后，provider 进入 `open`；经过
`LLM_CIRCUIT_RECOVERY_SECONDS` 后进入 `half_open`，仅放行一个探测调用。
探测成功恢复 `healthy`，失败则重新进入 `open`。取消调用不计作 provider 失败。

流式调用只允许在尚未向调用方暴露任何事件前切换 provider。一旦已经输出文本、
音频、session 或其他事件，后续失败只返回当前 provider 的 error，避免拼接两个
模型的响应或重复执行上游语义。

当前状态可通过 `LLMRegistry.get_provider_health()` 获取，状态包括：
`unknown`、`healthy`、`degraded`、`open`、`half_open` 和 `unavailable`。
健康快照只记录计数和异常类型，不保存请求内容、密钥或 provider 错误正文。

## 4. 调用版本与来源追踪

每个成功的非流式结果和每个流式事件可携带 `LLMCallProvenance`：

| 字段 | 含义 |
|---|---|
| `task_profile` | task profile 稳定名称 |
| `prompt_version` | 显式 Prompt 语义版本 |
| `prompt_template_sha256` | Prompt 模板内容指纹 |
| `request_sha256` | 实际消息列表指纹，不保存原文 |
| `provider` / `model` | 实际完成调用的 provider 和模型 |
| `attempted_providers` | 实际发起过调用的 provider 顺序 |
| `fallback_used` | 是否由非主 provider 完成 |
| `artifacts` | task 使用的其他版本化输入 |

所有 `TaskProfile` 必须显式声明非空 `version`。修改 Prompt 的语义或输出契约时，
必须提升该版本；模板哈希用于发现忘记提升版本的变更。

技能规划会额外记录：

```json
{
  "name": "skill_catalog",
  "version": "1",
  "sha256": "<规划时技能摘要的内容指纹>"
}
```

`SkillEngine.list_all_skills()` 现在向规划器提供参数、单位、步骤摘要、示例和标签，
因此技能目录指纹与实际进入 Prompt 的能力描述一致。规划结果通过
`LLMPlanResult.to_dict()` 进入命令预览，避免嵌套 dataclass 无法序列化。

## 5. 离线固定回归基线

固定数据位于
`data/regression/llm_planning_cases.json`，使用严格的
`schema_version: 1`。它覆盖：

- classifier 输出枚举和 session/execution 控制归一化；
- planner JSON 到 `LLMPlanResult` 的解析；
- classifier/planner Prompt 版本和模板哈希；
- 默认技能目录内容指纹；
- 典型技能参数校验和动作序列展开。

运行方式：

```powershell
.venv\Scripts\python.exe -m src.llm.regression
```

安装项目命令后也可运行：

```text
robot-llm-regression
```

runner 完全离线，不读取 API Key、不访问网络，输出稳定 JSON 并以退出码 `0/1`
表示通过或失败。修改 Prompt 或默认技能后，应先提升对应版本，再运行回归；只有
确认行为变化符合预期后才能更新 golden 数据和哈希。

当前基线验证解析、契约和技能展开的确定性，不声称验证在线模型对自然语言的语义
准确率。在线评测、延迟、token、失败率和成本指标属于 E-013。

## 6. 配置

```dotenv
# 未显式指定 provider 的调用才允许按此顺序降级；空值表示关闭跨厂商降级。
LLM_FALLBACK_PROVIDERS=

# 连续运行时失败达到阈值后熔断。
LLM_CIRCUIT_FAILURE_THRESHOLD=3

# 熔断后等待多少秒进入半开探测。
LLM_CIRCUIT_RECOVERY_SECONDS=30
```

配置中的未知 provider、非正数恢复时间或小于 1 的失败阈值会在
`LLMRegistry` 初始化时立即报错，不使用隐式默认值掩盖错误。
