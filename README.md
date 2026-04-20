# Robot Action Orchestrator (RAO)

基于 Python + PyQt6 的可视化机器人动作编排器，支持通过拖拽方式创建和执行机器人动作序列。

## 功能特性

- 动作库管理：支持三种动作类型（移动、执行、检测）
- 拖拽编排：通过拖拽方式组织动作序列
- 序列执行：支持开始、暂停、停止控制
- 数据持久化：保存和加载任务序列
- 实时日志：显示执行过程和状态

## 技术栈

- Python 3.9+
- PyQt6

## 项目结构

```
robotgui/
├── src/
│   ├── __init__.py      # 包初始化
│   ├── models.py        # 数据模型定义
│   ├── storage.py       # 数据存储管理
│   ├── dialogs.py       # 对话框组件
│   ├── execution.py     # 执行线程
│   ├── widgets.py       # 自定义UI组件
│   ├── main_window.py   # 主窗口
│   └── main.py          # 程序入口
├── data/                # 数据存储目录
│   ├── actions_library.json
│   └── tasks/
├── requirements.txt     # 依赖文件
├── run.py              # 运行脚本
└── README.md           # 说明文档
```

## 安装与运行

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行程序

```bash
python run.py
```

或

```bash
python -m src.main
```

## 使用说明

### 创建动作

1. 在左侧选择动作类别标签页（移动类/执行类/检测类）
2. 点击"新建动作"按钮
3. 在弹出的对话框中填写动作名称和参数
4. 点击确定保存动作

### 编排序列

1. 从左侧动作库中拖拽动作到右侧执行序列区
2. 使用上移/下移按钮调整动作顺序
3. 使用删除按钮移除不需要的动作

### 执行序列

1. 点击"开始执行"按钮开始运行序列
2. 执行过程中可点击"暂停"暂停执行
3. 点击"紧急停止"立即中断执行
4. 在日志窗口查看执行详情

### 保存/加载任务

- 使用菜单栏"文件 > 保存任务序列"保存当前序列
- 使用"文件 > 加载任务序列"加载已保存的任务

## 数据格式

动作库保存在 `data/actions_library.json` 文件中，任务序列保存在 `data/tasks/` 目录下。

## 扩展开发

### 添加新动作类型

1. 在 `models.py` 中的 `ActionType` 枚举添加新类型
2. 在 `dialogs.py` 中为该类型添加参数输入界面
3. 在 `execution.py` 中添加执行逻辑

### 连接真实硬件

修改 `execution.py` 中的 `_execute_move`、`_execute_manipulate` 和 `_execute_inspect` 方法，调用真实的硬件API。

## 许可证

MIT License
