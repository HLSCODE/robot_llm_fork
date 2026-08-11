# LLM Provider 治理与规划回归

> 状态：Active  
> 生效日期：2026-07-30  
> 对应总计划：E-010、E-011、E-012、E-013

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
                  ResponsePipeline
               text / native / text+TTS
                         |
                  RoutedLLMClient
              /          |           \
      candidate order  health      provenance
             |       + circuit          |
             +----------+---------------+
                        |
              concrete provider clients
```

- `ApplicationServices.llm` 是进程内唯一 `LLMRegistry`；Registry 是 provider
  实例和关闭生命周期的唯一所有者。
- `RoutedLLMClient` 是绑定 `TaskProfile` 的组合式代理，不拥有或关闭 provider。
- `ProviderHealthTracker` 在 registry 内共享，因此不同 task 对同一 provider
  观察到一致的健康和熔断状态。
- provider adapter 只负责协议转换和统一异常，不感知业务 task。

## 3. 路由、降级与熔断规则

主 provider 的优先级为：

1. 单次调用显式传入的 `provider`。
2. `[model_routing.<TaskProfile.name>].provider`。
3. `LLM_DEFAULT_PROVIDER`（仅用于未登记的自定义 profile）。

只有调用方没有显式指定 provider 时，才允许继续按该 task 的
`fallback_providers` 顺序尝试降级。未登记的自定义 profile 使用
`LLM_FALLBACK_PROVIDERS`。默认配置为空，即默认不把请求跨厂商
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

命令规划会额外记录：

```json
{
  "name": "command_catalog",
  "version": "2",
  "sha256": "<规划时统一命令目录的内容指纹>"
}
```

`CommandCatalog.entries()` 向规划器提供 Action、Skill、Workflow 和
ExecutionControl 的参数、示例与别名，因此目录指纹与实际进入 Prompt 的描述一致。
规划结果通过 `CommandPlanResult.to_dict()` 进入命令预览。

## 5. 运行指标

唯一 `LLMRegistry` 持有一个线程安全、生命周期内累计的指标实例；由该 registry
创建的所有 task client 共用它。`RoutedLLMClient` 是逻辑调用的唯一采集点：

- 调用总数及成功、失败、取消数，耗时总计、最大值、平均值和失败率；
- fallback 成功次数，以及按 task、成功 provider、成功 model 聚合的调用数；
- provider 实际报告的 input/output/total token 和美元成本；
- usage 与成本的覆盖调用数，用于识别供应商未返回统计的情况。

成本字段只累计 provider 响应中明确报告的值。项目不内置会随时间变化的模型价格，
也不把缺失成本估算成零。逻辑调用指标与 provider health 的逐次尝试指标职责不同：
前者用于业务 SLI，后者用于路由和熔断诊断。

指标不保存 prompt、响应、消息、原始 usage 或其他请求载荷。`ai_status.metrics` 返回
当前应用级 registry 快照；已认证客户端也可通过 `server_metrics.llm_metrics` 查询。
GUI、WebSocket 和 Voice 看到同一份进程级指标。指标聚合开销进入版本化无硬件性能预算。

## 6. 离线固定回归基线

固定数据位于
`data/regression/llm_planning_cases.json`，使用严格的
`schema_version: 1`。它覆盖：

- classifier 输出枚举和 session/execution 控制归一化；
- planner JSON 到四类 typed command 和 `CommandPlanResult` 的严格解析；
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
准确率。在线语义质量评测仍需单独建设版本化数据集；运行指标不能代替语义评测。

## 7. 配置

```toml
[model_routing.general_chat]
provider = "dashscope"
fallback_providers = []
output_mode = "text_then_tts"
speech_provider = "minicpm"
speech_fallback_providers = []

[model_routing.vision_fusion]
provider = "minicpm"
fallback_providers = []
output_mode = "native_audio"
speech_provider = ""
speech_fallback_providers = []

[llm]
llm_circuit_failure_threshold = 3
llm_circuit_recovery_seconds = 30.0
```

`output_mode` 支持 `text`、`native_audio`、`text_then_tts`。后者将最终文本交给
`speech_provider`，推理和语音拥有独立 fallback；前者不会加载语音 provider。
`native_audio` 要求推理 provider 自身声明 TTS 能力。

配置中的未知 provider、非法输出模式、不满足语音能力的组合、非正数恢复时间或小于 1 的失败阈值会在
`LLMRegistry` 初始化时立即报错，不使用隐式默认值掩盖错误。
