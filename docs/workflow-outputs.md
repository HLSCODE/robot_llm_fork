# 流程节点输出与远程结构化结果

## 能力边界

已接通动作结果、流程汇总、执行完成事件、运行回执和 TCP/WebSocket 响应。新增视觉算法**不在此功能内**：系统不会自动产生离心管液面高度、置信度或单位，也不会把日志解析为检测数据。

现有视觉动作可输出处理帧数 `frames_processed`、推理次数 `inference_count`、处理耗时 `duration_seconds`；以后视觉管线显式提供的其他输出也可沿同一链路返回。图片、本地绝对路径、SDK 对象和内部 `metadata` 不会自动导出。

## 在界面配置

1. 将视觉动作放入流程画布，选择节点并打开“修改动作参数”。
2. 勾选“将结果作为流程输出”。该设置只属于这个流程节点，不写入基础动作库。
3. 输入对外名称，例如 `tube_liquid_level`。名称必须以英文字母或下划线开头，只含字母、数字、下划线，长度 1～64，流程内不同节点不得重复。
4. “输出内容”可选全部结果或现有统计字段；也可输入 JSON Pointer，例如 `/height`、`/measurements/0/height`。JSON Pointer 中 `~0` 代表 `~`，`~1` 代表 `/`。这是对动作实际输出的选择，不是填写检测结果，也不支持表达式求值。
5. 选择结果收集方式：单次结果、收集全部（循环）、仅最后一次。循环内节点不能使用单次结果，执行前会明确拒绝，避免隐式覆盖。
6. 按需勾选“必需”。勾选后，动作成功但没有产生指定输出，会使流程以 `invalid_output` 失败；不勾选则允许省略。字段存在且值为 `null`、`false` 或 `0` 不等于字段缺失。
7. 保存流程。取消对话框不生效，撤销/重做、草稿、保存、重新加载和子流程快照会保留输出设置。移动、等待等当前没有业务输出的动作不显示该区域。

“仅最后一次”的最后一轮若缺少可选输出，会省略该输出，而不是把更早一轮的测量当作最新结果。失败或取消时可保留之前已经完成的结果，但标记不完整。

## 流程文件

输出声明是动作节点上的可选 `output`，不是 `definition.parameters`。例如：

```json
{
  "kind": "action",
  "node_id": "measure-level",
  "item_uuid": "measure-level-item",
  "definition": {"id":"vision-1","name":"测量","type":"VISION_RELOCALIZE","parameters":{},"robot_profile_id":"example-profile"},
  "output": {
    "name": "tube_liquid_level",
    "field": "/height",
    "mode": "single",
    "required": true
  }
}
```

此片段仅说明输出字段位置，不是完整可执行视觉动作；需要填写合法的动作参数。`field=""` 表示全部业务输出，`mode` 支持 `single` / `all` / `last`，缺省为单次结果，`required` 缺省为 true。

这是 workflow schema 6 的可选扩展：已有流程未声明 `output` 时行为不变，不要求迁移旧数据。应使用支持此扩展的版本编辑带输出设置的流程，旧版本可能丢弃该字段。流程编译和直接执行入口均校验重复名称及循环收集模式。

## 开发者接入新的动作/视觉能力

通用处理器返回结构化数据：

```python
return context.success(
    operation="vision.measure_liquid_level",
    device_id="camera",
    outputs={"detected": True, "height": 23.6, "unit": "mm", "confidence": 0.96},
)
```

新视觉管线返回 `VisionPipelineResult(..., outputs={...})`，`VisionService` 将其交给 `VisionResult.outputs`，视觉动作处理器再传入 `ActionHandlerResult.outputs`。`metadata` 仍是内部诊断数据，不作为自动公开接口。新增动作类型时需更新 `domain.outputs.supports_action_output` 能力目录，才能在节点表单显示导出设置；它不应要求 TCP 增加专用字段或专用指令。

输出仅允许 JSON 对象及其嵌套的字符串、有限数值、布尔值、null、数组、对象。NumPy 数据需要算法显式转换成原生 Python 类型，不隐式 `str()`。NaN、Infinity、循环引用、设备对象、图片二进制和非法键拒绝输出。

单动作输出最多 64 KiB，最终流程输出最多 512 KiB；单份验证对象最多 10000 个 JSON 值、嵌套深度最多 16。超限明确失败，不截断、不伪装成功；图片等大制品应返回安全的引用，由单独的制品下载能力处理。当前并未新增下载接口。

执行器在每次运行开始创建独立输出收集器；并行分支同步写入，循环/并行按结构执行序号排序，不以线程完成先后决定结果。仅导出用户选择的字段，未勾选节点的输出不会进入对外结果。

## TCP、WebSocket 与查询

TCP 指令仍为 `task.run`，最终响应增加 `result.outputs` 与 `result.outputs_complete`，不改变“先受理、后完成”的语义。例如选择全部液面结果时：

```json
{
  "version": 1,
  "event": "run.finished",
  "run_id": "abc123",
  "status": "succeeded",
  "result": {
    "outputs": {
      "tube_liquid_level": {"detected":true,"height":23.6,"unit":"mm","confidence":0.96}
    },
    "outputs_complete": true,
    "message": ""
  }
}
```

`run.status` 查询持久化的同一份 `result`；客户端断线不会丢弃任务，重连后可查询。最终输出在流程结束时发布；运行中的进度回执不代表最终业务结果。进程异常退出时仍按原规则标记 `unknown`，不能推断物理动作完成或测量结果已经完整写入。

WebSocket 的 `execution_finished` 顶层增加 `outputs` / `outputs_complete`，值与同次任务的 TCP/运行查询结果一致；其他消息及旧字段保留。未来 HTTP 可复用同一运行查询服务，无需重新提取视觉结果。

启用服务端认证时，未认证 WebSocket 旁观连接只收到旧有状态及空输出，不会收到导出的业务测量数据；已认证客户端按现有单 API Key 操作身份访问。TCP 查询继续校验运行归属。

`all` 模式返回数组，每一项包含 `value` 和 `source`：

```json
{
  "tube_liquid_level": [
    {"value":23.6,"source":{"node_id":"measure-level","loop_iteration":1,"branch_id":"","path":"root/0/iteration/1/0","timestamp":0}},
    {"value":22.9,"source":{"node_id":"measure-level","loop_iteration":2,"branch_id":"","path":"root/0/iteration/2/0","timestamp":0}}
  ]
}
```

实际 source 还包含 step_id、loop_id、parallel_id 等身份字段；时间为采集到动作结果时的服务端 Unix 秒，不是相机硬件时间戳。可选字段缺失的轮次不会伪造为 0 或 null，可通过来源轮次区分；`last` / `single` 则直接返回所选值。

流程失败或取消时，已有导出值会保留，但 `outputs_complete=false`；客户端必须先检查 status 和完整性标志。完整性表示本次执行范围内必需输出齐全，**不证明算法判断正确**。无液面应由算法显式返回 `detected=false`、`height=null`，相机或算法异常应按动作失败返回，不能混为高度 0。按指定节点开始执行时，仅汇总本次实际执行范围的输出。
