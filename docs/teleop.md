# 遥操作控制说明文档

## 概述

遥操作（Teleoperation）模式允许通过 WebSocket 实时发送关节角度指令，直接控制机械臂运动。该模式适用于主从遥操作场景，支持 50Hz 的高频指令流。

## 技术原理

### 控制方式
使用机械臂 SDK 的 `rm_movej_canfd` 函数实现关节透传控制：
- **完全透传模式**：`trajectory_mode=0`，指令立即执行
- **高跟随模式**：`follow=True`，机械臂快速响应指令变化

### 数据流
```
主臂（采集） → WebSocket Client → WebSocket Server → 从臂（执行）
  关节角度      JSON消息          rm_movej_canfd      机械臂运动
  50Hz         50Hz              50Hz                50Hz
```

`TeleoperationService` 是遥操作状态的唯一所有者。每个 WebSocket 控制客户端使用
`websocket:<client_id>` owner，数据采集使用独立的 `data-collection` owner。
多个 owner 可以共享同一机械臂资源租约，但只能控制自己声明的臂；owner 离开时
只释放自己的状态，最后一个 owner 离开后才释放机械臂租约。WebSocket Server
不再维护遥操作模式、消息计数或夹爪位置的镜像字典。

### 性能指标

- **指令频率目标**：50Hz（推荐）
- **本地网络延迟目标**：<20ms；这是部署目标，不是当前代码仓的硬件验收结果
- **关节角度精度**：0.001°
- **数据格式**：6个关节角度（度）

`TeleoperationService` 在应用层累计已应用/跳过/失败指令、单次处理耗时、最大
指令间隔、最大间隔抖动和观测吞吐。已认证客户端可以通过 `server_metrics` 的
`teleoperation_metrics` 字段读取进程生命周期快照。仓库性能门禁覆盖审计和指标
聚合本身的无硬件开销；网络端到端延迟、真实机械臂响应和停止时延必须另做硬件
验收，不能由该软件基准替代。

## WebSocket 协议

所有 `teleop_*` action 都是控制写操作。客户端必须先发送 `authenticate`，
再发送 `acquire_control`，并在会话期间持续发送 `control_heartbeat`。控制租约
超时、控制者断线或发送失败会自动停止其遥操作并释放设备资源；其他观察者断线
不会影响当前控制者。完整安全会话格式见
[WebSocket 接口手册](websocket-api.md#32-写操作认证与控制权)。

### 1. 遥操作初始化（可选）

在启动遥操作前，可以将机械臂移动到指定的初始关节姿态。通常用于将从臂移动到与主臂相同的起始位置。

**请求**
```json
{
  "action": "teleop_init",
  "arm": "左",
  "joints": [45.23, -30.15, 60.78, 0.0, 90.5, -45.3]
}
```

**参数说明**
- `arm`: 机械臂选择，"左" 或 "右"
- `joints`: 6个关节角度（度），[j1, j2, j3, j4, j5, j6]

**响应**
```json
{
  "event": "teleop_init_completed",
  "arm": "左",
  "message": "初始化完成"
}
```

**使用场景**
- 遥操作开始前，将从臂移动到与主臂相同的初始位置
- 避免遥操作启动时机械臂突然大幅度移动
- 确保主从臂起始姿态一致

---

### 2. 启动遥操作模式

**请求**
```json
{
  "action": "teleop_start",
  "arm": "左"
}
```

**参数说明**
- `arm`: 机械臂选择，"左" 或 "右"

**响应**
```json
{
  "event": "teleop_started",
  "arm": "左",
  "message": "遥操作模式已启动"
}
```

---

### 3. 发送关节指令

**请求**
```json
{
  "action": "teleop_joint",
  "arm": "左",
  "joints": [45.23, -30.15, 60.78, 0.0, 90.5, -45.3],
  "follow": true,
  "trajectory_mode": 0
}
```

**参数说明**
- `arm`: 机械臂选择，"左" 或 "右"（可选，默认使用启动时的臂）
- `joints`: 6个关节角度数组（单位：度）
  - `[j1, j2, j3, j4, j5, j6]`
  - 精度：0.001°
- `follow`: 跟随模式
  - `true`: 高跟随模式（推荐）
  - `false`: 普通跟随模式
- `trajectory_mode`: 轨迹模式
  - `0`: 完全透传（推荐）
  - `1`: 平滑轨迹

**响应**
```json
{
  "event": "teleop_error",
  "message": "关节指令执行失败"
}
```
仅在执行失败时返回错误消息。

服务端按连接顺序处理关节指令，不静默丢帧。每客户端请求频率受
`WEBSOCKET_MAX_REQUESTS_PER_SECOND` 限制，连接入站队列受
`WEBSOCKET_MAX_QUEUED_MESSAGES` 限制；队列饱和时由 WebSocket/TCP 向发送端
施加背压。机械臂 SDK 调用在线程池中执行，不阻塞其他连接的心跳、租约过期检测
和服务关闭。客户端应维持推荐的 50Hz，不应依赖服务端替其合并过期轨迹点。

---

### 4. 停止遥操作

**请求**
```json
{
  "action": "teleop_stop"
}
```

**响应**
```json
{
  "event": "teleop_stopped",
  "message": "遥操作模式已停止"
}
```

## 使用示例

### Python 客户端示例

```python
import asyncio
import json
import websockets
import time

class TeleopClient:
    def __init__(self, uri="ws://localhost:8765"):
        self.uri = uri
        self.ws = None
    
    async def connect(self):
        self.ws = await websockets.connect(self.uri)
    
    async def init_teleop(self, arm="左", joints=None):
        """遥操作初始化：移动到指定关节姿态"""
        if joints is None:
            # 如果没有提供关节角度，可以跳过初始化
            return
        
        await self.ws.send(json.dumps({
            "action": "teleop_init",
            "arm": arm,
            "joints": joints
        }))
        response = await self.ws.recv()
        data = json.loads(response)
        if data.get("event") == "teleop_init_completed":
            print("初始化完成")
        else:
            print("初始化失败:", data)
    
    async def start_teleop(self, arm="左"):
        """启动遥操作模式"""
        await self.ws.send(json.dumps({
            "action": "teleop_start",
            "arm": arm
        }))
        response = await self.ws.recv()
        print(json.loads(response))
    
    async def send_joint_command(self, joints, arm="左"):
        """发送关节指令"""
        await self.ws.send(json.dumps({
            "action": "teleop_joint",
            "arm": arm,
            "joints": joints,
            "follow": True,
            "trajectory_mode": 0
        }))
    
    async def stop_teleop(self):
        """停止遥操作"""
        await self.ws.send(json.dumps({
            "action": "teleop_stop"
        }))
        response = await self.ws.recv()
        print(json.loads(response))
    
    async def run_teleop_loop(self, joint_stream, arm="左", frequency=50, init_joints=None):
        """运行遥操作循环"""
        # 1. 初始化（可选）：移动到指定关节姿态
        if init_joints:
            await self.init_teleop(arm, init_joints)
        
        # 2. 启动遥操作模式
        await self.start_teleop(arm)
        
        # 3. 发送关节指令流
        dt = 1.0 / frequency
        for joints in joint_stream:
            await self.send_joint_command(joints, arm)
            await asyncio.sleep(dt)
        
        # 4. 停止遥操作
        await self.stop_teleop()

# 使用示例
async def main():
    client = TeleopClient()
    await client.connect()
    
    # 主臂初始关节角度（从主臂采集）
    init_joints = [45.0, -30.0, 60.0, 0.0, 90.0, -45.0]
    
    # 模拟关节角度流（从主臂采集）
    joint_stream = [
        [45.0, -30.0, 60.0, 0.0, 90.0, -45.0],
        [45.1, -30.1, 60.1, 0.1, 90.1, -45.1],
        # ... 更多关节角度
    ]
    
    # 运行遥操作（包含初始化）
    await client.run_teleop_loop(
        joint_stream, 
        arm="左", 
        frequency=50,
        init_joints=init_joints  # 先移动到初始位置
    )

asyncio.run(main())
```

### JavaScript 客户端示例

```javascript
class TeleopClient {
  constructor(uri = 'ws://localhost:8765') {
    this.uri = uri;
    this.ws = null;
  }
  
  connect() {
    this.ws = new WebSocket(this.uri);
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log('Received:', data);
    };
  }
  
  initTeleop(arm = '左', joints) {
    this.ws.send(JSON.stringify({
      action: 'teleop_init',
      arm: arm,
      joints: joints
    }));
  }
  
  startTeleop(arm = '左') {
    this.ws.send(JSON.stringify({
      action: 'teleop_start',
      arm: arm
    }));
  }
  
  sendJointCommand(joints, arm = '左') {
    this.ws.send(JSON.stringify({
      action: 'teleop_joint',
      arm: arm,
      joints: joints,
      follow: true,
      trajectory_mode: 0
    }));
  }
  
  stopTeleop() {
    this.ws.send(JSON.stringify({
      action: 'teleop_stop'
    }));
  }
  
  runTeleopLoop(jointStream, arm = '左', frequency = 50, initJoints = null) {
    // 1. 初始化（可选）
    if (initJoints) {
      this.initTeleop(arm, initJoints);
    }
    
    // 2. 启动遥操作模式
    this.startTeleop(arm);
    
    // 3. 发送关节指令流
    const dt = 1000 / frequency; // 毫秒
    let index = 0;
    
    const interval = setInterval(() => {
      if (index < jointStream.length) {
        this.sendJointCommand(jointStream[index], arm);
        index++;
      } else {
        clearInterval(interval);
        this.stopTeleop();
      }
    }, dt);
  }
}

// 使用示例
const client = new TeleopClient();
client.connect();

// 等待连接建立
setTimeout(() => {
  const jointStream = [
    [45.0, -30.0, 60.0, 0.0, 90.0, -45.0],
    [45.1, -30.1, 60.1, 0.1, 90.1, -45.1],
    // ... 更多关节角度
  ];
  
  client.runTeleopLoop(jointStream, '左', 50);
}, 1000);
```

## 安全注意事项

### 当前实现（Phase 1）
- ✅ 模式互斥：遥操作时禁止执行其他任务
- ✅ 关节数量验证：检查是否为6个关节角度
- ⚠️ **未实现**：关节限位检查
- ⚠️ **未实现**：速度限制检查
- ✅ **已实现**：WebSocket 控制租约心跳、超时释放和遥操作停止
- ✅ **已实现**：独立于控制租约的逐臂关节指令流 watchdog

### 后续增强（Phase 2）
需要添加以下安全措施：
1. **关节限位检查**：每个关节的角度范围限制
2. **速度限制检查**：相邻指令的变化率限制
3. **紧急停止**：继续完成真实硬件最大停止时延验收

### 使用建议
- 仅在安全环境下使用遥操作
- 确保主臂和从臂的运动空间无障碍物
- 建议先在模拟模式下测试
- 准备好紧急停止机制

## 配置说明

遥操作使用 WebSocket 认证和控制租约配置：

```toml
[server]
websocket_enabled = true
websocket_host = "127.0.0.1"
websocket_port = 8765
websocket_control_lease_seconds = 30.0
websocket_max_requests_per_second = 120
teleoperation_command_timeout_seconds = 1.0
```

认证密钥通过 `.env` 的 `WEBSOCKET_AUTH_TOKEN` 配置。

当前 WebSocket API 版本为 `2.0`。包括 50Hz 关节指令在内的每个请求都必须
携带 `api_version: "2.0"` 和唯一 `request_id`；默认每客户端上限为每秒
120 个请求，入站队列大小由 `WEBSOCKET_MAX_QUEUED_MESSAGES` 配置。
任一已启用机械臂超过 `TELEOPERATION_COMMAND_TIMEOUT_SECONDS` 未收到成功的
关节指令时，服务会停止该 WebSocket owner、撤销控制租约、清理关联数据采集，
并广播 `control_released(reason="teleoperation_watchdog")`。控制心跳不能代替
关节指令流 watchdog。

启动服务：
```bash
uv run robot-llm
```

## 调试和测试

### 测试步骤
1. 启动 WebSocket 服务
2. 连接 WebSocket 客户端
3. 发送 `authenticate` 和 `acquire_control`
4. 启动 `control_heartbeat`
5. 发送 `teleop_start` 启动遥操作
6. 发送关节指令流（50Hz）
7. 发送 `teleop_stop`，再发送 `release_control`

### 日志查看

服务端通过 `audit.teleoperation` 输出结构化 JSON 审计事件，覆盖会话启动/停止、
关节与夹爪指令、重复指令跳过、执行错误、watchdog 过期和安全释放。事件只记录
owner、臂、结果、稳定错误码、计数和时长，不记录关节数组或夹爪位置等控制载荷。
审计输出失败只记内部错误，不会改变已经发生的设备控制结果。

### 常见问题

**Q: 遥操作时机械臂不动？**
A: 检查：
- 机械臂是否已连接
- 关节角度是否在合理范围
- WebSocket 连接是否正常

**Q: 遥操作延迟很大？**
A: 检查：
- 网络连接质量
- 是否使用本地网络
- 指令频率是否过高

**Q: 如何切换机械臂？**
A: 先停止当前遥操作，再启动另一臂的遥操作。

## API 参考

完整 WebSocket API 文档请参考：[docs/websocket-api.md](websocket-api.md)

## 版本历史

- **v1.0** (2026-06-24): 初始实现，支持基本遥操作功能
