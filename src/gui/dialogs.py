from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                            QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                            QHBoxLayout, QLabel, QSpinBox, QWidget, QStackedLayout,
                            QGroupBox, QListWidget, QListWidgetItem, QPushButton)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from ..core.models import ActionType, ActionDefinition


class ActionPreviewDialog(QDialog):
    """
    动作预览对话框
    显示 AI 生成的技能展开后的完整动作序列，供用户确认执行
    """

    # 信号
    confirmed = pyqtSignal()  # 用户确认执行

    def __init__(self, items: list, skill_info: dict, parent=None):
        super().__init__(parent)
        self._items = items
        self._skill_info = skill_info
        self._init_ui()

    def _init_ui(self):
        skill_name = self._skill_info.get("name", "未知技能")
        icon = self._skill_info.get("icon", "🤖")
        step_count = len(self._items)
        estimated_time = self._skill_info.get("estimated_time", 0)

        self.setWindowTitle(f"动作预览 - {icon} {skill_name} ({step_count}步)")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        # 技能信息
        info_label = QLabel(f"技能：{icon} {skill_name}")
        info_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(info_label)

        desc_label = QLabel(f"描述：{self._skill_info.get('description', '')}")
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(desc_label)

        # 动作步骤列表
        steps_group = QGroupBox(f"动作步骤 ({step_count}步)")
        steps_layout = QVBoxLayout(steps_group)

        self.step_list = QListWidget()
        self._populate_step_list()
        steps_layout.addWidget(self.step_list)

        layout.addWidget(steps_group, stretch=1)

        # 预计时间
        time_label = QLabel(f"预计执行时间：~{estimated_time:.0f}秒")
        time_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(time_label)

        if estimated_time > 30:
            warning_label = QLabel("提示：动作较多，执行时间较长")
            warning_label.setStyleSheet("color: #f57c00; font-size: 12px;")
            layout.addWidget(warning_label)

        # 按钮
        button_layout = QHBoxLayout()

        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)

        button_layout.addStretch()

        confirm_button = QPushButton("确认执行")
        confirm_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px 24px;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
        """)
        confirm_button.clicked.connect(self.accept_and_emit)
        button_layout.addWidget(confirm_button)

        layout.addLayout(button_layout)

    def _populate_step_list(self):
        action_type_names = {
            "MOVE": "移动",
            "MOVE_TO_POINT": "移动",
            "BASE_MOVE": "底盘移动",
            "MANIPULATE": "执行",
            "ARM_ACTION": "执行",
            "INSPECT": "检测",
            "INSPECT_AND_OUTPUT": "检测",
            "WAIT": "Wait",
            "CHANGE_GUN": "换枪"
        }

        for idx, item in enumerate(self._items):
            definition = item.get("definition", {})
            action_name = definition.get("name", "未知")
            action_type = definition.get("type", "MOVE")
            parameters = definition.get("parameters", {})

            type_display = action_type_names.get(action_type, action_type)

            # 构建参数显示
            param_strs = [f"{k}={v}" for k, v in parameters.items()]
            param_text = ", ".join(param_strs) if param_strs else "无参数"

            step_num = idx + 1
            item_text = f"Step {step_num}: {action_name}"

            list_item = QListWidgetItem(item_text)

            tooltip = f"类型：{type_display}\n参数：{param_text}"
            list_item.setToolTip(tooltip)

            if step_num <= 3:
                list_item.setForeground(QColor("#4CAF50"))

            self.step_list.addItem(list_item)

    def accept_and_emit(self):
        """确认并发送信号"""
        self.confirmed.emit()
        self.accept()


class ActionConfigDialog(QDialog):
    def __init__(self, action_type: ActionType, action_data: dict = None, parent=None):
        super().__init__(parent)
        self.action_type = action_type
        self.action_data = action_data or {}
        
        # 动作类型与初始化方法的映射
        self.init_methods = {
            ActionType.MOVE: self._init_move_ui,
            ActionType.MANIPULATE: self._init_manipulate_ui,
            ActionType.INSPECT: self._init_inspect_ui,
            ActionType.WAIT: self._init_wait_ui,
            ActionType.BASE_MOVE: self._init_base_move_ui,
            ActionType.CHANGE_GUN: self._init_change_gun_ui,
            ActionType.VISION_CAPTURE: self._init_vision_capture_ui,
        }
        
        # 动作类型与参数构建方法的映射
        self.param_build_methods = {
            ActionType.MOVE: self._build_move_params,
            ActionType.MANIPULATE: self._build_manipulate_params,
            ActionType.INSPECT: self._build_inspect_params,
            ActionType.WAIT: self._build_wait_params,
            ActionType.BASE_MOVE: self._build_base_move_params,
            ActionType.CHANGE_GUN: self._build_change_gun_params,
            ActionType.VISION_CAPTURE: self._build_vision_capture_params,
        }
        
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(f"配置 {self.get_type_display()} 动作")
        self.setMinimumWidth(400)

        layout = QVBoxLayout()
        form_layout = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setText(self.action_data.get('name', ''))
        form_layout.addRow("动作名称:", self.name_input)

        # 根据动作类型初始化不同的参数面板
        init_func = self.init_methods.get(self.action_type)
        if init_func:
            init_func(form_layout)

        layout.addLayout(form_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def _init_move_ui(self, form_layout: QFormLayout):
        """初始化机械臂/身体移动 UI"""
        # 目标选择：机械臂 或 身体
        self.target_combo = QComboBox()
        self.target_combo.addItem("机械臂", "机械臂")
        self.target_combo.addItem("身体", "身体")
        current_target = self.action_data.get('parameters', {}).get('目标', '机械臂')
        self.target_combo.setCurrentText(current_target)
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)

        # 机械臂参数面板
        self.robot_widget = QWidget()
        robot_layout = QFormLayout()

        self.arm_combo = QComboBox()
        self.arm_combo.addItem("左", "左")
        self.arm_combo.addItem("右", "右")
        current_arm = self.action_data.get('parameters', {}).get('臂', '左')
        self.arm_combo.setCurrentText(current_arm)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("关节运动 (move_j)", "move_j")
        self.mode_combo.addItem("直线运动 (move_l)", "move_l")
        current_mode = self.action_data.get('parameters', {}).get('模式', 'move_j')
        self.mode_combo.setCurrentText(current_mode if current_mode in ['move_j', 'move_l'] else 'move_j')

        self.target_pose_input = QLineEdit()
        self.target_pose_input.setText(self.action_data.get('parameters', {}).get('点位', ''))
        self.target_pose_input.setPlaceholderText("例如：[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]")

        robot_layout.addRow("臂:", self.arm_combo)
        robot_layout.addRow("运动模式:", self.mode_combo)
        robot_layout.addRow("点位:", self.target_pose_input)
        self.robot_widget.setLayout(robot_layout)

        # 身体参数面板
        self.body_widget = QWidget()
        body_layout = QFormLayout()

        self.position_input = QSpinBox()
        self.position_input.setRange(0, 500000)
        self.position_input.setValue(self.action_data.get('parameters', {}).get('位置', 0))
        self.position_input.setSuffix(" (脉冲)")

        body_layout.addRow("目标位置:", self.position_input)
        self.body_widget.setLayout(body_layout)

        # 使用堆叠布局根据目标类型显示不同面板
        self.move_param_stack = QStackedLayout()
        self.move_param_stack.addWidget(self.robot_widget)
        self.move_param_stack.addWidget(self.body_widget)
        self.move_param_stack.setCurrentWidget(self.robot_widget)

        form_layout.addRow("目标:", self.target_combo)
        form_layout.addRow("参数:", self.move_param_stack)

        # 初始化显示状态
        self._on_target_changed()

    def _init_manipulate_ui(self, form_layout: QFormLayout):
        """初始化执行类动作 UI"""
        self.executor_combo = QComboBox()
        self.executor_combo.addItem("快换手", "快换手")
        self.executor_combo.addItem("继电器", "继电器")
        self.executor_combo.addItem("夹爪", "夹爪")
        self.executor_combo.addItem("吸液枪", "吸液枪")
        current_executor = self.action_data.get('parameters', {}).get('执行器', '快换手')
        self.executor_combo.setCurrentText(current_executor)
        self.executor_combo.currentIndexChanged.connect(self._on_executor_changed)

        # 快换手/继电器/夹爪 时的参数面板
        self.normal_widget = QWidget()
        normal_layout = QFormLayout()

        self.number_combo = QComboBox()
        self.number_combo.addItem("1", 1)
        self.number_combo.addItem("2", 2)
        current_number = self.action_data.get('parameters', {}).get('编号', 1)
        self.number_combo.setCurrentText(str(current_number))

        self.operation_combo = QComboBox()
        self.operation_combo.addItem("开", "开")
        self.operation_combo.addItem("关", "关")
        current_operation = self.action_data.get('parameters', {}).get('操作', '开')
        self.operation_combo.setCurrentText(current_operation)

        normal_layout.addRow("编号:", self.number_combo)
        normal_layout.addRow("操作:", self.operation_combo)
        self.normal_widget.setLayout(normal_layout)

        # 吸液枪参数面板
        self.pipette_widget = QWidget()
        pipette_layout = QFormLayout()

        self.pipette_operation_combo = QComboBox()
        self.pipette_operation_combo.addItem("吸", "吸")
        self.pipette_operation_combo.addItem("吐", "吐")
        self.pipette_operation_combo.addItem("退枪头", "退枪头")
        current_pipette_op = self.action_data.get('parameters', {}).get('操作', '吸')
        self.pipette_operation_combo.setCurrentText(current_pipette_op)

        self.capacity_input = QSpinBox()
        self.capacity_input.setRange(0, 10000)
        self.capacity_input.setSuffix(" ul")
        self.capacity_input.setValue(self.action_data.get('parameters', {}).get('容量', 500))

        pipette_layout.addRow("操作:", self.pipette_operation_combo)
        pipette_layout.addRow("容量:", self.capacity_input)
        self.pipette_widget.setLayout(pipette_layout)

        # 使用堆叠布局根据执行器类型显示不同面板
        self.param_stack = QStackedLayout()
        self.param_stack.addWidget(self.normal_widget)
        self.param_stack.addWidget(self.pipette_widget)
        self.param_stack.setCurrentWidget(self.normal_widget)

        form_layout.addRow("执行器:", self.executor_combo)
        form_layout.addRow("", self.param_stack)

        # 初始化显示状态
        self._on_executor_changed()

    def _init_inspect_ui(self, form_layout: QFormLayout):
        """初始化检测类动作 UI"""
        self.sensor_input = QLineEdit()
        self.sensor_input.setText(self.action_data.get('parameters', {}).get('Sensor_ID', ''))

        self.threshold_input = QDoubleSpinBox()
        self.threshold_input.setRange(-9999, 9999)
        self.threshold_input.setValue(self.action_data.get('parameters', {}).get('Threshold', 0))

        self.timeout_input = QDoubleSpinBox()
        self.timeout_input.setRange(0.1, 60)
        self.timeout_input.setValue(self.action_data.get('parameters', {}).get('Timeout', 5))
        self.timeout_input.setSuffix(" s")

        form_layout.addRow("传感器 ID:", self.sensor_input)
        form_layout.addRow("判定阈值:", self.threshold_input)
        form_layout.addRow("超时时间:", self.timeout_input)

    def _init_wait_ui(self, form_layout: QFormLayout):
        """初始化 Wait 动作 UI"""
        self.wait_time_input = QDoubleSpinBox()
        self.wait_time_input.setRange(0.1, 3600)
        self.wait_time_input.setDecimals(1)
        self.wait_time_input.setValue(self.action_data.get('parameters', {}).get('wait_seconds', 1.0))
        self.wait_time_input.setSuffix(" s")

        form_layout.addRow("Wait Time:", self.wait_time_input)

    def _init_base_move_ui(self, form_layout: QFormLayout):
        """初始化底盘移动 UI"""
        # 移动方式选择：位置移动 或 距离移动
        self.move_mode_combo = QComboBox()
        self.move_mode_combo.addItem("位置移动", "position")
        self.move_mode_combo.addItem("距离移动", "distance")
        current_mode = self.action_data.get('parameters', {}).get('move_mode', 'position')
        self.move_mode_combo.setCurrentText("位置移动" if current_mode == 'position' else "距离移动")
        self.move_mode_combo.currentIndexChanged.connect(self._on_move_mode_changed)
        
        # 位置移动参数面板
        self.position_widget = QWidget()
        position_layout = QFormLayout()
        
        self.id_input = QSpinBox()
        self.id_input.setRange(0, 100)
        self.id_input.setValue(self.action_data.get('parameters', {}).get('id', 0))
        
        self.cid_input = QSpinBox()
        self.cid_input.setRange(0, 100)
        self.cid_input.setValue(self.action_data.get('parameters', {}).get('cid', 0))
        
        position_layout.addRow("目标位置 ID:", self.id_input)
        position_layout.addRow("目标位置 CID:", self.cid_input)
        self.position_widget.setLayout(position_layout)
        
        # 距离移动参数面板
        self.distance_widget = QWidget()
        distance_layout = QFormLayout()
        
        self.valueY_input = QDoubleSpinBox()
        self.valueY_input.setRange(-10.0, 10.0)
        self.valueY_input.setDecimals(3)
        self.valueY_input.setSuffix(" m")
        self.valueY_input.setValue(self.action_data.get('parameters', {}).get('valueY', 0.0))
        
        distance_layout.addRow("移动距离:", self.valueY_input)
        self.distance_widget.setLayout(distance_layout)
        
        # 使用堆叠布局根据移动方式显示不同面板
        self.move_mode_stack = QStackedLayout()
        self.move_mode_stack.addWidget(self.position_widget)
        self.move_mode_stack.addWidget(self.distance_widget)
        
        form_layout.addRow("移动方式:", self.move_mode_combo)
        form_layout.addRow("", self.move_mode_stack)
        
        # 初始化显示状态
        self._on_move_mode_changed()

    def _init_change_gun_ui(self, form_layout: QFormLayout):
        """初始化换枪动作 UI"""
        self.gun_position_combo = QComboBox()
        self.gun_position_combo.addItem("1", 1)
        self.gun_position_combo.addItem("2", 2)
        current_pos = self.action_data.get('parameters', {}).get('Gun_Position', 1)
        self.gun_position_combo.setCurrentText(str(current_pos))

        self.operation_combo = QComboBox()
        self.operation_combo.addItem("取", "取")
        self.operation_combo.addItem("放", "放")
        current_op = self.action_data.get('parameters', {}).get('Operation', '取')
        self.operation_combo.setCurrentText(current_op)

        form_layout.addRow("枪位:", self.gun_position_combo)
        form_layout.addRow("取/放:", self.operation_combo)

    def _init_vision_capture_ui(self, form_layout: QFormLayout):
        """初始化视觉抓取动作 UI"""
        # 视觉抓取参数已固定：robot1 / bottle / 置信度 0.7 / 速度 15 / 夹爪 150mm
        fixed_label = QLabel(
            "固定配置：Robot1 (左臂) | 工作流：bottle | 置信度：0.7 | "
            "速度：15mm/s | 夹爪：150mm | 调试图片：开"
        )
        fixed_label.setStyleSheet("color: #666; font-size: 12px; padding: 4px;")
        fixed_label.setWordWrap(True)

        form_layout.addRow("", fixed_label)

    def _on_executor_changed(self):
        """根据选择的执行器类型切换参数面板"""
        if hasattr(self, 'executor_combo') and hasattr(self, 'param_stack'):
            executor = self.executor_combo.currentData()
            if executor == '吸液枪':
                self.param_stack.setCurrentWidget(self.pipette_widget)
            else:
                self.param_stack.setCurrentWidget(self.normal_widget)

    def _on_move_mode_changed(self):
        """根据选择的移动方式切换参数面板"""
        if hasattr(self, 'move_mode_combo') and hasattr(self, 'move_mode_stack'):
            move_mode = self.move_mode_combo.currentData()
            if move_mode == 'position':
                self.move_mode_stack.setCurrentWidget(self.position_widget)
            else:
                self.move_mode_stack.setCurrentWidget(self.distance_widget)

    def _on_target_changed(self):
        """根据选择的目标类型切换参数面板"""
        if hasattr(self, 'target_combo') and hasattr(self, 'move_param_stack'):
            target = self.target_combo.currentData()
            if target == '机械臂':
                self.move_param_stack.setCurrentWidget(self.robot_widget)
            else:
                self.move_param_stack.setCurrentWidget(self.body_widget)

    def get_type_display(self) -> str:
        type_map = {
            ActionType.MOVE: "移动",
            ActionType.BASE_MOVE: "底盘移动",
            ActionType.MANIPULATE: "机械臂",
            ActionType.INSPECT: "检测",
            ActionType.WAIT: "Wait",
            ActionType.CHANGE_GUN: "换枪",
            ActionType.VISION_CAPTURE: "视觉抓取"
        }
        return type_map.get(self.action_type, "")

    def validate_and_accept(self):
        name = self.name_input.text().strip()
        if not name:
            self.name_input.setFocus()
            return

        if self.action_type == ActionType.MOVE:
            # 根据目标类型验证不同参数
            if hasattr(self, 'target_combo'):
                target = self.target_combo.currentData()
                if target == '机械臂':
                    target_pose = self.target_pose_input.text().strip()
                    if not target_pose:
                        self.target_pose_input.setFocus()
                        return
                # 身体模式不需要额外验证
            else:
                target_pose = self.target_pose_input.text().strip()
                if not target_pose:
                    self.target_pose_input.setFocus()
                    return

        elif self.action_type == ActionType.BASE_MOVE:
            move_mode = self.move_mode_combo.currentData()
            if move_mode == 'position':
                # 位置移动模式需要验证 id 和 cid
                pass  # id 和 cid 都有默认值，不需要额外验证
            else:
                # 距离移动模式不需要额外验证
                pass

        if self.action_type == ActionType.INSPECT:
            sensor_id = self.sensor_input.text().strip()
            if not sensor_id:
                self.sensor_input.setFocus()
                return

        self.accept()

    def get_action_definition(self) -> ActionDefinition:
        name = self.name_input.text().strip()
        
        # 如果是新建动作（没有 id），生成新的 UUID
        action_id = self.action_data.get('id', '')
        if not action_id:
            from uuid import uuid4
            action_id = str(uuid4())
        
        # 根据动作类型构建参数
        build_method = self.param_build_methods.get(self.action_type)
        if build_method:
            parameters = build_method()
        else:
            parameters = {}
        
        return ActionDefinition(
            id=action_id,
            name=name,
            type=self.action_type,
            parameters=parameters
        )
    
    def _build_move_params(self) -> dict:
        """构建机械臂/身体移动动作参数"""
        target = self.target_combo.currentData()
        if target == '机械臂':
            return {
                '目标': target,
                '臂': self.arm_combo.currentText(),
                '模式': self.mode_combo.currentData(),
                '点位': self.target_pose_input.text().strip()
            }
        else:
            return {
                '目标': target,
                '位置': self.position_input.value()
            }
    
    def _build_manipulate_params(self) -> dict:
        """构建执行类动作参数"""
        executor = self.executor_combo.currentData()
        if executor == '吸液枪':
            return {
                '执行器': executor,
                '操作': self.pipette_operation_combo.currentText(),
                '容量': self.capacity_input.value()
            }
        else:
            return {
                '执行器': executor,
                '编号': self.number_combo.currentData(),
                '操作': self.operation_combo.currentText()
            }
    
    def _build_base_move_params(self) -> dict:
        """构建底盘移动动作参数"""
        move_mode = self.move_mode_combo.currentData()
        if move_mode == 'position':
            return {
                'move_mode': 'position',
                'id': self.id_input.value(),
                'cid': self.cid_input.value()
            }
        else:
            return {
                'move_mode': 'distance',
                'valueY': self.valueY_input.value()
            }
    
    def _build_inspect_params(self) -> dict:
        """构建检测类动作参数"""
        return {
            'Sensor_ID': self.sensor_input.text().strip(),
            'Threshold': self.threshold_input.value(),
            'Timeout': self.timeout_input.value()
        }
    
    def _build_wait_params(self) -> dict:
        """构建 Wait 动作参数"""
        return {
            'wait_seconds': self.wait_time_input.value()
        }
    
    def _build_change_gun_params(self) -> dict:
        """构建换枪动作参数"""
        return {
            'Gun_Position': self.gun_position_combo.currentData(),
            'Operation': self.operation_combo.currentText()
        }
    
    def _build_vision_capture_params(self) -> dict:
        """构建视觉抓取动作参数"""
        return {
            '目标机械臂': 'robot1',
            '工作流': 'bottle',
            '置信度': 0.7,
            '调试图片': True,
            '移动速度': 15,
            '夹爪长度': 150.0
        }