# 视觉服务与数据治理架构

> 文档类型：Current Architecture  
> 最近更新：2026-08-04  
> 状态：Active

## 1. 依赖与执行边界

```text
ActionEngine
    -> VisionCapture/RelocalizationActionHandler
        -> VisionService
            -> production pipeline | simulation fixture
            -> VisionArtifactStore
            -> VisionStationStorage
```

- 组合根创建唯一 `ExecutionContext` 和 `VisionService`，两者同时注入
  `ActionEngine`；视觉重定位和后续位姿补偿共享同一状态源。
- handler 只获取运行时拥有的机械臂/相机能力，调用 `VisionService`，再把
  `VisionResult` 映射为稳定的 action result。
- 生产模式延迟加载真实抓取与重定位 pipeline；simulation 模式注入
  `VisionPipelineFixture`，不加载模型、不访问真实相机。
- 不保留旧的可注入 bool executor 或第二套视觉执行入口。

## 2. Typed result

`VisionResult` 明确包含：

- `VisionOperation`：capture 或 relocalization；
- `VisionResultCode`：succeeded 或 rejected；
- 用户/执行层可消费的 message；
- 本次运行产生的 `VisionArtifact`；
- 不可变 metadata，目前包含 run_id。

pipeline 抛出的异常不会被 VisionService 吞掉：产物会以失败 manifest 发布，异常继续
交给 action handler 映射为设备操作失败。pipeline 返回 false 表示可预期业务拒绝。

## 3. 模型、标定和工位版本

活动配置必须提供：

- `VISION_SCHEMA_VERSION`；
- `VISION_MODEL_VERSION`；
- `VISION_CALIBRATION_VERSION`。

工位文件使用 `robot-llm.vision-stations` schema v1，原子替换写入。每个 profile
必须携带 `profile_version`、`model_version` 和 `calibration_version`。读取时模型或
标定版本与活动配置不一致会显式失败，禁止混用旧工位基准。

未版本化的列表或旧 `{profiles: [...]}` 文档会被拒绝；项目不提供兼容读取路径。

## 4. 调试产物生命周期

所有抓取和重定位产物统一位于 `VISION_DEBUG_SAVE_DIR`：

1. 每次操作创建独立的隐藏 staging 目录；
2. pipeline 只向本次目录写入图片或诊断文件；
3. 结束时写入 `robot-llm.vision-artifacts` manifest；
4. staging 目录原子改名为正式 run 目录；
5. `VISION_DEBUG_RETENTION_DAYS` 控制保留天数，`VISION_DEBUG_MAX_RUNS`
   控制最大运行数；超过一小时的遗留 staging 目录会被清理。

旧的 `VISION_RELOCALIZATION_DEBUG_DIR` 双入口已删除。

## 5. 扩展规则

- 替换检测/分割模型时更新 model version，并运行 simulation、fixture 和真实图片回归。
- 更新内参、畸变、手眼矩阵或参考系时更新 calibration version，重新采集工位 profile。
- 新 pipeline 实现消费者需要的最小 callable contract，不得自行创建设备或管理相机生命周期。
- 性能、帧率、延迟和模型质量指标后续由 F-V-008 纳入统一观测与性能门禁。
