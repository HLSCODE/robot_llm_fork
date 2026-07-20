from PyQt6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                            QDoubleSpinBox, QDialogButtonBox, QVBoxLayout,
                            QHBoxLayout, QLabel, QSpinBox, QWidget, QStackedLayout,
                            QGroupBox, QListWidget, QListWidgetItem, QPushButton,
                            QFileDialog, QCheckBox, QMessageBox)
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
        self.setMinimumSize(500, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header card
        header = QWidget()
        header.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 12px;")
        header_layout = QVBoxLayout(header)
        info_label = QLabel(f"{icon} {skill_name}")
        info_label.setStyleSheet("font-size: 16px; font-weight: 700; color: #1e293b; border: none; background: transparent;")
        header_layout.addWidget(info_label)
        desc_label = QLabel(self._skill_info.get('description', ''))
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #64748b; font-size: 12px; border: none; background: transparent;")
        header_layout.addWidget(desc_label)
        layout.addWidget(header)

        # Steps
        self.step_list = QListWidget()
        self.step_list.setStyleSheet("""
            QListWidget { border: 1px solid #e2e8f0; border-radius: 8px; background: #ffffff; font-size: 12px; }
            QListWidget::item { padding: 6px 10px; }
        """)
        self._populate_step_list()
        layout.addWidget(self.step_list, stretch=1)

        # Time estimate
        time_label = QLabel(f"⏱ 预计执行时间：~{estimated_time:.0f} 秒")
        time_label.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(time_label)

        if estimated_time > 30:
            warning_label = QLabel("⚠ 提示：动作较多，执行时间较长")
            warning_label.setStyleSheet("color: #d97706; font-size: 12px; font-weight: 600;")
            layout.addWidget(warning_label)

        # Buttons
        button_layout = QHBoxLayout()
        cancel_button = QPushButton("取消")
        cancel_button.setMinimumHeight(34)
        cancel_button.setStyleSheet("""
            QPushButton { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; color: #334155; font-weight: 500; padding: 6px 20px; }
            QPushButton:hover { background: #f8fafc; }
        """)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        button_layout.addStretch()

        confirm_button = QPushButton("✅ 确认执行")
        confirm_button.setMinimumHeight(34)
        confirm_button.setStyleSheet("""
            QPushButton { background: #22c55e; color: #fff; font-weight: 700; border: none; border-radius: 8px; padding: 8px 24px; font-size: 13px; }
            QPushButton:hover { background: #16a34a; }
        """)
        confirm_button.clicked.connect(self.accept_and_emit)
        button_layout.addWidget(confirm_button)
        layout.addLayout(button_layout)

    def _populate_step_list(self):
        action_type_names = {
            "TRAJECTORY": "Trajectory",
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
                list_item.setForeground(QColor("#16a34a"))

            self.step_list.addItem(list_item)

    def accept_and_emit(self):
        """确认并发送信号"""
        self.confirmed.emit()
        self.accept()


class ActionConfigDialog(QDialog):
    def __init__(self, action_type: ActionType, action_data: dict = None, parent=None, existing_names: set = None, move_target: str = None):
        super().__init__(parent)
        self.action_type = action_type
        self.action_data = action_data or {}
        self._existing_names = existing_names or set()
        self._move_target = move_target  # 新建时预设的移动目标（"机械臂移动" / "身体移动"）
        # 编辑模式下，排除自身的名称（允许不改名保存）
        if action_data and action_data.get('name'):
            self._existing_names.discard(action_data['name'])
        
        # 动作类型与初始化方法的映射
        self.init_methods = {
            ActionType.MOVE: self._init_move_ui,
            ActionType.MANIPULATE: self._init_manipulate_ui,
            ActionType.INSPECT: self._init_inspect_ui,
            ActionType.WAIT: self._init_wait_ui,
            ActionType.BASE_MOVE: self._init_base_move_ui,
            ActionType.CHANGE_GUN: self._init_change_gun_ui,
            ActionType.VISION_CAPTURE: self._init_vision_capture_ui,
            ActionType.VISION_RELOCALIZE: self._init_vision_relocalize_ui,
            ActionType.TRAJECTORY: self._init_trajectory_ui,
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
            ActionType.VISION_RELOCALIZE: self._build_vision_relocalize_params,
            ActionType.TRAJECTORY: self._build_trajectory_params,
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

    @staticmethod
    def _param_text(params: dict, key: str, default: str = "") -> str:
        value = params.get(key, default)
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _param_float(params: dict, key: str, default: float) -> float:
        value = params.get(key, default)
        if value is None or value == "":
            return float(default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _param_int(params: dict, key: str, default: int) -> int:
        value = params.get(key, default)
        if value is None or value == "":
            return int(default)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _param_bool(params: dict, key: str, default: bool = False) -> bool:
        value = params.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on", "是"}
        return bool(value)

    def _init_move_ui(self, form_layout: QFormLayout):
        """初始化机械臂/身体移动 UI"""
        # 预设目标：新建时从上级选择传入，编辑时从已有参数读取
        if self._move_target == "身体移动":
            preset_target = "身体"
        elif self._move_target == "机械臂移动":
            preset_target = "机械臂"
        else:
            preset_target = self.action_data.get('parameters', {}).get('目标', '机械臂')
        self._preset_target = preset_target  # 保存供 _build_move_params 使用

        # 目标选择：机械臂 或 身体
        self.target_combo = QComboBox()
        self.target_combo.addItem("机械臂", "机械臂")
        self.target_combo.addItem("身体", "身体")
        self.target_combo.setCurrentText(preset_target)
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        # 新建时锁定目标类型，不可切换；编辑时允许切换
        if self._move_target:
            self.target_combo.setEnabled(False)

        # 机械臂参数面板
        self.robot_widget = QWidget()
        robot_layout = QFormLayout()

        self.arm_combo = QComboBox()
        self.arm_combo.addItem("左", "左")
        self.arm_combo.addItem("右", "右")
        current_arm = self.action_data.get('parameters', {}).get('臂', '左')
        self.arm_combo.setCurrentText(current_arm)
        self.arm_combo.currentIndexChanged.connect(self._refresh_vision_station_choices)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("关节运动 (move_j)", "move_j")
        self.mode_combo.addItem("直线运动 (move_l)", "move_l")
        current_mode = self.action_data.get('parameters', {}).get('模式', 'move_j')
        self.mode_combo.setCurrentText(current_mode if current_mode in ['move_j', 'move_l'] else 'move_j')

        self.target_pose_input = QLineEdit()
        self.target_pose_input.setText(self.action_data.get('parameters', {}).get('点位', ''))
        self.target_pose_input.setPlaceholderText("例如：[-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]")

        params = self.action_data.get('parameters', {})
        compensation_config = params.get('补偿', {})
        localization_config = params.get('定位补偿', {})
        if not compensation_config and localization_config.get('enabled'):
            compensation_config = {'mode': 'udp', 'udp': localization_config}

        current_compensation_mode = compensation_config.get('mode', 'none')
        self.localization_reference = (
            compensation_config.get('udp', {}).get('teach_offset')
            or localization_config.get('teach_offset')
        )

        self.compensation_mode_combo = QComboBox()
        self.compensation_mode_combo.addItem("不补偿", "none")
        self.compensation_mode_combo.addItem("UDP 定位补偿", "udp")
        self.compensation_mode_combo.addItem("视觉重定位补偿", "vision")
        index = self.compensation_mode_combo.findData(current_compensation_mode)
        self.compensation_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.compensation_mode_combo.currentIndexChanged.connect(self._on_compensation_mode_changed)

        localization_row = QWidget()
        localization_layout = QHBoxLayout(localization_row)
        localization_layout.setContentsMargins(0, 0, 0, 0)
        localization_layout.setSpacing(4)

        self.localization_status_label = QLabel(self._format_localization_reference(self.localization_reference))
        self.localization_status_label.setStyleSheet("color: #64748b; font-size: 11px;")
        capture_localization_btn = QPushButton("读取当前定位")
        capture_localization_btn.clicked.connect(self._capture_localization_reference)
        localization_layout.addWidget(self.localization_status_label, stretch=1)
        localization_layout.addWidget(capture_localization_btn)

        vision_config = compensation_config.get('vision', compensation_config if current_compensation_mode == 'vision' else {})
        self.vision_station_combo = QComboBox()
        self.vision_station_combo.setEditable(True)
        self._vision_station_current = str(vision_config.get('station_id', ''))
        self._refresh_vision_station_choices()

        robot_layout.addRow("臂:", self.arm_combo)
        robot_layout.addRow("运动模式:", self.mode_combo)
        robot_layout.addRow("点位:", self.target_pose_input)
        robot_layout.addRow("补偿方式:", self.compensation_mode_combo)
        robot_layout.addRow("UDP基准:", localization_row)
        robot_layout.addRow("视觉工位:", self.vision_station_combo)
        self.robot_widget.setLayout(robot_layout)
        self._on_compensation_mode_changed()

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
        self.executor_combo.addItem("右臂转圈注液", "右臂转圈注液")
        self.executor_combo.addItem("加粉装置", "加粉装置")
        self.executor_combo.addItem("智能加粉", "智能加粉")
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
        self.pipette_operation_combo.currentTextChanged.connect(self._on_pipette_operation_changed)

        self.capacity_input = QSpinBox()
        self.capacity_input.setRange(0, 10000)
        self.capacity_input.setSuffix(" ul")
        self.capacity_input.setValue(self._param_int(self.action_data.get('parameters', {}), '容量', 500))

        self.absorb_speed_input = QSpinBox()
        self.absorb_speed_input.setRange(1, 9999)
        self.absorb_speed_input.setSuffix(" ul/s")
        self.absorb_speed_input.setValue(self._param_int(self.action_data.get('parameters', {}), '吸液速度', 1200))

        self.dispense_speed_input = QSpinBox()
        self.dispense_speed_input.setRange(1, 9999)
        self.dispense_speed_input.setSuffix(" ul/s")
        self.dispense_speed_input.setValue(self._param_int(self.action_data.get('parameters', {}), '吐液速度', 800))

        self.dispense_mode_combo = QComboBox()
        self.dispense_mode_combo.addItem("指定容量", "指定容量")
        self.dispense_mode_combo.addItem("全吐", "全吐")
        current_dispense_mode = self.action_data.get('parameters', {}).get('吐液容量模式')
        if current_dispense_mode is None and self.action_data.get('parameters', {}).get('全吐', False):
            current_dispense_mode = "全吐"
        self.dispense_mode_combo.setCurrentText(current_dispense_mode or "指定容量")
        self.dispense_mode_combo.currentTextChanged.connect(self._on_pipette_operation_changed)

        pipette_layout.addRow("操作:", self.pipette_operation_combo)
        pipette_layout.addRow("容量:", self.capacity_input)
        pipette_layout.addRow("吸液速度:", self.absorb_speed_input)
        pipette_layout.addRow("吐液速度:", self.dispense_speed_input)
        pipette_layout.addRow("吐液容量:", self.dispense_mode_combo)
        self.pipette_widget.setLayout(pipette_layout)
        self._on_pipette_operation_changed()

        # 右臂转圈注液参数面板
        self.circle_dispense_widget = QWidget()
        circle_layout = QFormLayout()
        circle_params = self.action_data.get('parameters', {})

        self.circle_pose_input = QLineEdit()
        self.circle_pose_input.setText(self._param_text(circle_params, '位姿', ''))
        self.circle_pose_input.setPlaceholderText("[-0.058,-0.412,-0.154,-2.934,0.428,-2.722]")

        self.circle_radius_input = QDoubleSpinBox()
        self.circle_radius_input.setRange(0.1, 500.0)
        self.circle_radius_input.setDecimals(3)
        self.circle_radius_input.setSuffix(" mm")
        self.circle_radius_input.setValue(self._param_float(circle_params, '半径R', 10.0))

        self.circle_dispense_speed_input = QDoubleSpinBox()
        self.circle_dispense_speed_input.setRange(1.0, 9999.0)
        self.circle_dispense_speed_input.setDecimals(1)
        self.circle_dispense_speed_input.setSuffix(" ul/s")
        self.circle_dispense_speed_input.setValue(self._param_float(circle_params, '吐液速度', 800.0))

        self.circle_volume_input = QDoubleSpinBox()
        self.circle_volume_input.setRange(1.0, 10000.0)
        self.circle_volume_input.setDecimals(1)
        self.circle_volume_input.setSuffix(" ul")
        self.circle_volume_input.setValue(self._param_float(circle_params, '吐液量', 500.0))

        self.circle_count_input = QDoubleSpinBox()
        self.circle_count_input.setRange(0.1, 20.0)
        self.circle_count_input.setDecimals(2)
        self.circle_count_input.setValue(self._param_float(circle_params, '圈数', 1.0))

        self.circle_segments_input = QSpinBox()
        self.circle_segments_input.setRange(8, 360)
        self.circle_segments_input.setValue(self._param_int(circle_params, '分段数', 72))

        self.circle_blend_radius_input = QSpinBox()
        self.circle_blend_radius_input.setRange(0, 100)
        self.circle_blend_radius_input.setValue(self._param_int(circle_params, '过渡半径', 20))

        self.circle_move_velocity_input = QSpinBox()
        self.circle_move_velocity_input.setRange(1, 100)
        self.circle_move_velocity_input.setValue(self._param_int(circle_params, '运动速度', 10))

        self.circle_continuous_checkbox = QCheckBox("连续运动")
        self.circle_continuous_checkbox.setChecked(self._param_bool(circle_params, '连续运动', True))

        self.circle_clockwise_checkbox = QCheckBox("顺时针")
        self.circle_clockwise_checkbox.setChecked(self._param_bool(circle_params, '顺时针', False))

        circle_layout.addRow("圆心位姿:", self.circle_pose_input)
        circle_layout.addRow("半径R:", self.circle_radius_input)
        circle_layout.addRow("吐液速度:", self.circle_dispense_speed_input)
        circle_layout.addRow("吐液量:", self.circle_volume_input)
        circle_layout.addRow("圈数:", self.circle_count_input)
        circle_layout.addRow("每圈分段:", self.circle_segments_input)
        circle_layout.addRow("过渡半径:", self.circle_blend_radius_input)
        circle_layout.addRow("运动速度:", self.circle_move_velocity_input)
        circle_layout.addRow("", self.circle_continuous_checkbox)
        circle_layout.addRow("", self.circle_clockwise_checkbox)
        self.circle_dispense_widget.setLayout(circle_layout)

        # 加粉装置手动动作参数面板
        self.tapping_widget = QWidget()
        tapping_layout = QFormLayout()
        tapping_params = self.action_data.get('parameters', {})

        self.tapping_operation_combo = QComboBox()
        for op in ["使能", "夹爪移动到", "夹爪闭合", "夹爪张开", "针下降", "针上升", "针正转", "针反转", "针停止", "针旋转停止"]:
            self.tapping_operation_combo.addItem(op, op)
        self.tapping_operation_combo.setCurrentText(self._param_text(tapping_params, '操作', '使能'))

        self.tapping_steps_input = QSpinBox()
        self.tapping_steps_input.setRange(-500000, 500000)
        self.tapping_steps_input.setSuffix(" 步")
        self.tapping_steps_input.setValue(self._param_int(tapping_params, '步数', 5000))

        self.tapping_opening_input = QSpinBox()
        self.tapping_opening_input.setRange(0, 100)
        self.tapping_opening_input.setSuffix(" %")
        self.tapping_opening_input.setValue(self._param_int(tapping_params, '开度', 50))

        tapping_layout.addRow("操作:", self.tapping_operation_combo)
        tapping_layout.addRow("步数:", self.tapping_steps_input)
        tapping_layout.addRow("夹爪开度:", self.tapping_opening_input)
        self.tapping_widget.setLayout(tapping_layout)

        # 智能加粉参数面板
        self.powder_widget = QWidget()
        powder_layout = QFormLayout()
        powder_params = self.action_data.get('parameters', {})

        self.powder_target_input = QDoubleSpinBox()
        self.powder_target_input.setRange(0.1, 100000.0)
        self.powder_target_input.setDecimals(1)
        self.powder_target_input.setSuffix(" mg")
        self.powder_target_input.setValue(self._param_float(powder_params, '目标重量mg', 100.0))

        self.powder_tolerance_input = QDoubleSpinBox()
        self.powder_tolerance_input.setRange(0.1, 10000.0)
        self.powder_tolerance_input.setDecimals(1)
        self.powder_tolerance_input.setSuffix(" mg")
        self.powder_tolerance_input.setValue(self._param_float(powder_params, '容差mg', 5.0))

        self.powder_max_rounds_input = QSpinBox()
        self.powder_max_rounds_input.setRange(1, 200)
        self.powder_max_rounds_input.setValue(self._param_int(powder_params, '最大轮次', 20))

        self.powder_settle_input = QDoubleSpinBox()
        self.powder_settle_input.setRange(0.0, 60.0)
        self.powder_settle_input.setDecimals(1)
        self.powder_settle_input.setSuffix(" s")
        self.powder_settle_input.setValue(self._param_float(powder_params, '稳定等待秒数', 2.0))

        self.powder_safe_pos_input = QSpinBox()
        self.powder_safe_pos_input.setRange(-500000, 500000)
        self.powder_safe_pos_input.setSuffix(" 步")
        self.powder_safe_pos_input.setValue(self._param_int(powder_params, '安全位置步数', 0))

        self.powder_dispense_pos_input = QSpinBox()
        self.powder_dispense_pos_input.setRange(-500000, 500000)
        self.powder_dispense_pos_input.setSuffix(" 步")
        self.powder_dispense_pos_input.setValue(self._param_int(powder_params, '加粉位置步数', 50000))

        self.powder_rotation_home_input = QSpinBox()
        self.powder_rotation_home_input.setRange(-500000, 500000)
        self.powder_rotation_home_input.setSuffix(" 步")
        self.powder_rotation_home_input.setValue(self._param_int(powder_params, '旋转原点步数', 0))

        self.powder_large_step_input = QSpinBox()
        self.powder_large_step_input.setRange(1, 500000)
        self.powder_large_step_input.setSuffix(" 步")
        self.powder_large_step_input.setValue(self._param_int(powder_params, '大步步数', 20000))

        self.powder_medium_step_input = QSpinBox()
        self.powder_medium_step_input.setRange(1, 500000)
        self.powder_medium_step_input.setSuffix(" 步")
        self.powder_medium_step_input.setValue(self._param_int(powder_params, '中步步数', 8000))

        self.powder_small_step_input = QSpinBox()
        self.powder_small_step_input.setRange(1, 500000)
        self.powder_small_step_input.setSuffix(" 步")
        self.powder_small_step_input.setValue(self._param_int(powder_params, '小步步数', 2000))

        self.powder_micro_step_input = QSpinBox()
        self.powder_micro_step_input.setRange(1, 500000)
        self.powder_micro_step_input.setSuffix(" 步")
        self.powder_micro_step_input.setValue(self._param_int(powder_params, '微步步数', 500))

        powder_layout.addRow("目标重量:", self.powder_target_input)
        powder_layout.addRow("容差:", self.powder_tolerance_input)
        powder_layout.addRow("最大轮次:", self.powder_max_rounds_input)
        powder_layout.addRow("稳定等待:", self.powder_settle_input)
        powder_layout.addRow("安全位置:", self.powder_safe_pos_input)
        powder_layout.addRow("加粉位置:", self.powder_dispense_pos_input)
        powder_layout.addRow("旋转原点:", self.powder_rotation_home_input)
        powder_layout.addRow("大步:", self.powder_large_step_input)
        powder_layout.addRow("中步:", self.powder_medium_step_input)
        powder_layout.addRow("小步:", self.powder_small_step_input)
        powder_layout.addRow("微步:", self.powder_micro_step_input)
        self.powder_widget.setLayout(powder_layout)

        # 使用堆叠布局根据执行器类型显示不同面板
        self.param_stack = QStackedLayout()
        self.param_stack.addWidget(self.normal_widget)
        self.param_stack.addWidget(self.pipette_widget)
        self.param_stack.addWidget(self.circle_dispense_widget)
        self.param_stack.addWidget(self.tapping_widget)
        self.param_stack.addWidget(self.powder_widget)
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
        self.id_input.setRange(-100, 100)
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

        self.distance_x_input = QDoubleSpinBox()
        self.distance_x_input.setRange(-1000.0, 1000.0)
        self.distance_x_input.setDecimals(3)
        self.distance_x_input.setSuffix(" cm")
        self.distance_x_input.setValue(self.action_data.get('parameters', {}).get('x', 0.0))

        self.distance_y_input = QDoubleSpinBox()
        self.distance_y_input.setRange(-1000.0, 1000.0)
        self.distance_y_input.setDecimals(3)
        self.distance_y_input.setSuffix(" cm")
        self.distance_y_input.setValue(self.action_data.get('parameters', {}).get('y', 0.0))

        self.distance_angle_input = QDoubleSpinBox()
        self.distance_angle_input.setRange(-360.0, 360.0)
        self.distance_angle_input.setDecimals(3)
        self.distance_angle_input.setSuffix(" °")
        self.distance_angle_input.setValue(self.action_data.get('parameters', {}).get('angle', 0.0))

        distance_layout.addRow("X 距离:", self.distance_x_input)
        distance_layout.addRow("Y 距离:", self.distance_y_input)
        distance_layout.addRow("角度:", self.distance_angle_input)
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
        """初始化视觉抓取动作 UI（可从 config.env 配置默认值）"""
        from ..core.config_loader import Config
        cfg = Config.get_instance()

        params = self.action_data.get('parameters', {})

        self.vision_robot_combo = QComboBox()
        self.vision_robot_combo.addItem("左臂 (Robot1)", "robot1")
        self.vision_robot_combo.addItem("右臂 (Robot2)", "robot2")
        self.vision_robot_combo.setCurrentText(
            "左臂 (Robot1)" if params.get('目标机械臂', 'robot1') == 'robot1' else "右臂 (Robot2)"
        )

        self.vision_workflow_combo = QComboBox()
        self.vision_workflow_combo.addItem("瓶子抓取 (bottle)", "bottle")
        self.vision_workflow_combo.addItem("竖直抓取 (vertical)", "vertical")
        default_wf = params.get('工作流', cfg.VISION_DEFAULT_WORKFLOW)
        self.vision_workflow_combo.setCurrentText(
            "瓶子抓取 (bottle)" if default_wf == 'bottle' else "竖直抓取 (vertical)"
        )

        self.vision_confidence_input = QDoubleSpinBox()
        self.vision_confidence_input.setRange(0.1, 1.0)
        self.vision_confidence_input.setSingleStep(0.05)
        self.vision_confidence_input.setDecimals(2)
        self.vision_confidence_input.setValue(
            float(params.get('置信度', cfg.VISION_DEFAULT_CONFIDENCE))
        )

        self.vision_velocity_input = QSpinBox()
        self.vision_velocity_input.setRange(1, 100)
        self.vision_velocity_input.setSuffix(" mm/s")
        self.vision_velocity_input.setValue(
            int(params.get('移动速度', cfg.VISION_DEFAULT_VELOCITY))
        )

        self.vision_gripper_length_input = QDoubleSpinBox()
        self.vision_gripper_length_input.setRange(10.0, 500.0)
        self.vision_gripper_length_input.setSuffix(" mm")
        self.vision_gripper_length_input.setValue(
            float(params.get('夹爪长度', cfg.VISION_DEFAULT_GRIPPER_LENGTH))
        )

        self.vision_debug_checkbox = QCheckBox("保存调试图片")
        self.vision_debug_checkbox.setChecked(
            bool(params.get('调试图片', True))
        )

        form_layout.addRow("目标机械臂:", self.vision_robot_combo)
        form_layout.addRow("工作流:", self.vision_workflow_combo)
        form_layout.addRow("置信度:", self.vision_confidence_input)
        form_layout.addRow("移动速度:", self.vision_velocity_input)
        form_layout.addRow("夹爪长度:", self.vision_gripper_length_input)
        form_layout.addRow("", self.vision_debug_checkbox)

    def _init_vision_relocalize_ui(self, form_layout: QFormLayout):
        """初始化视觉重定位动作 UI。"""
        from ..core.config_loader import Config
        cfg = Config.get_instance()
        params = self.action_data.get('parameters', {})

        self.relocalize_mode_combo = QComboBox()
        self.relocalize_mode_combo.addItem("运行时重定位", "run")
        self.relocalize_mode_combo.addItem("采集/更新示教基准", "teach")
        current_mode = params.get("action_mode", "run")
        index = self.relocalize_mode_combo.findData(current_mode)
        self.relocalize_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        self.relocalize_mode_combo.currentIndexChanged.connect(self._on_relocalize_mode_changed)

        self.relocalize_arm_combo = QComboBox()
        self.relocalize_arm_combo.addItem("左臂", "left")
        self.relocalize_arm_combo.addItem("右臂", "right")
        current_arm = params.get("arm", "left")
        index = self.relocalize_arm_combo.findData(current_arm)
        self.relocalize_arm_combo.setCurrentIndex(index if index >= 0 else 0)
        self.relocalize_arm_combo.currentIndexChanged.connect(self._on_relocalize_arm_changed)

        self.relocalize_station_combo = QComboBox()
        self.relocalize_station_combo.currentIndexChanged.connect(self._on_relocalize_station_selected)
        self.relocalize_station_current = str(params.get("station_id") or params.get("station_name") or "").strip()

        self.relocalize_station_id_input = QLineEdit()
        self.relocalize_station_id_input.setText(params.get("station_id") or params.get("station_name", ""))
        self.relocalize_station_id_input.setPlaceholderText("例如：station_1")

        self.relocalize_station_name_input = QLineEdit()
        self.relocalize_station_name_input.setText(params.get("station_name") or params.get("station_id", ""))
        self.relocalize_station_name_input.setPlaceholderText("例如：一号工位")

        self.relocalize_photo_pose_input = QLineEdit()
        photo_pose = params.get("photo_pose", "")
        if isinstance(photo_pose, list):
            photo_pose = str(photo_pose)
        self.relocalize_photo_pose_input.setText(str(photo_pose or ""))
        self.relocalize_photo_pose_input.setPlaceholderText("留空则采集示教时使用当前位姿；运行时优先使用示教库位姿")

        self.relocalize_camera_input = QLineEdit()
        self.relocalize_camera_input.setText(params.get("camera_name", ""))
        relocalize_cfg = cfg.get_vision_relocalization_config(current_arm)
        self.relocalize_camera_input.setPlaceholderText(relocalize_cfg.get("camera_name", ""))

        marker_params = params.get("marker", {}) if isinstance(params.get("marker"), dict) else {}
        default_marker = relocalize_cfg.get("marker", {})
        marker_width = params.get("marker_width", marker_params.get("width", default_marker.get("width", 0.158)))
        marker_height = params.get("marker_height", marker_params.get("height", default_marker.get("height", 0.158)))

        self.relocalize_marker_width_input = QDoubleSpinBox()
        self.relocalize_marker_width_input.setRange(0.000001, 10000.0)
        self.relocalize_marker_width_input.setDecimals(6)
        self.relocalize_marker_width_input.setValue(float(marker_width or 0.158))

        self.relocalize_marker_height_input = QDoubleSpinBox()
        self.relocalize_marker_height_input.setRange(0.000001, 10000.0)
        self.relocalize_marker_height_input.setDecimals(6)
        self.relocalize_marker_height_input.setValue(float(marker_height or 0.158))

        self.relocalize_move_mode_combo = QComboBox()
        self.relocalize_move_mode_combo.addItem("关节运动 (move_j)", "move_j")
        self.relocalize_move_mode_combo.addItem("直线运动 (move_l)", "move_l")
        current_move_mode = params.get("move_mode", "move_j")
        index = self.relocalize_move_mode_combo.findData(current_move_mode)
        self.relocalize_move_mode_combo.setCurrentIndex(index if index >= 0 else 0)

        form_layout.addRow("动作模式:", self.relocalize_mode_combo)
        form_layout.addRow("机械臂:", self.relocalize_arm_combo)
        self.relocalize_station_combo_label = QLabel("示教工位:")
        self.relocalize_station_name_label = QLabel("工位名称:")
        self.relocalize_photo_pose_label = QLabel("拍照位姿:")
        self.relocalize_camera_label = QLabel("相机名称:")
        form_layout.addRow(self.relocalize_station_combo_label, self.relocalize_station_combo)
        form_layout.addRow(self.relocalize_station_name_label, self.relocalize_station_name_input)
        form_layout.addRow(self.relocalize_photo_pose_label, self.relocalize_photo_pose_input)
        form_layout.addRow(self.relocalize_camera_label, self.relocalize_camera_input)
        self.relocalize_marker_width_label = QLabel("示教marker宽度(同位姿单位):")
        self.relocalize_marker_height_label = QLabel("示教marker高度(同位姿单位):")
        form_layout.addRow(self.relocalize_marker_width_label, self.relocalize_marker_width_input)
        form_layout.addRow(self.relocalize_marker_height_label, self.relocalize_marker_height_input)
        form_layout.addRow("移动模式:", self.relocalize_move_mode_combo)
        self._refresh_relocalize_station_choices()
        self._on_relocalize_mode_changed()

    def _on_relocalize_arm_changed(self):
        if not hasattr(self, 'relocalize_camera_input'):
            return
        from ..core.config_loader import Config

        arm = self.relocalize_arm_combo.currentData()
        camera_name = Config.get_instance().get_vision_relocalization_config(arm).get("camera_name", "")
        self.relocalize_camera_input.setPlaceholderText(camera_name)
        self._refresh_relocalize_station_choices()

    def _on_relocalize_mode_changed(self):
        teach_mode = (
            hasattr(self, 'relocalize_mode_combo')
            and self.relocalize_mode_combo.currentData() == "teach"
        )
        for attr in (
            'relocalize_station_combo_label',
            'relocalize_station_combo',
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setVisible(not teach_mode)
        for attr in (
            'relocalize_station_name_label',
            'relocalize_station_name_input',
            'relocalize_photo_pose_label',
            'relocalize_photo_pose_input',
            'relocalize_camera_label',
            'relocalize_camera_input',
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setVisible(teach_mode)
        for attr in (
            'relocalize_marker_width_label',
            'relocalize_marker_width_input',
            'relocalize_marker_height_label',
            'relocalize_marker_height_input',
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setVisible(teach_mode)
        if not teach_mode:
            self._on_relocalize_station_selected()

    def _refresh_relocalize_station_choices(self):
        if not hasattr(self, 'relocalize_station_combo'):
            return

        current = self.relocalize_station_id_input.text().strip()
        if not current:
            current = getattr(self, 'relocalize_station_current', '')

        self.relocalize_station_combo.blockSignals(True)
        self.relocalize_station_combo.clear()
        self.relocalize_station_combo.addItem("请选择示教工位", "")
        try:
            from ..core.vision_station_storage import VisionStationStorage

            arm = self.relocalize_arm_combo.currentData()
            for station_id, label in VisionStationStorage.list_station_choices(arm):
                self.relocalize_station_combo.addItem(label, station_id)
        except Exception:
            pass

        if current:
            index = self.relocalize_station_combo.findData(current)
            if index >= 0:
                self.relocalize_station_combo.setCurrentIndex(index)
            else:
                self.relocalize_station_combo.setCurrentIndex(0)
        self.relocalize_station_combo.blockSignals(False)
        self._on_relocalize_station_selected()

    def _selected_relocalize_station_id(self) -> str:
        if not hasattr(self, 'relocalize_station_combo'):
            return self.relocalize_station_id_input.text().strip()
        data = self.relocalize_station_combo.currentData()
        return str(data or "").strip()

    def _on_relocalize_station_selected(self):
        if not hasattr(self, 'relocalize_station_combo'):
            return
        if self.relocalize_mode_combo.currentData() == "teach":
            return

        station_id = self._selected_relocalize_station_id()
        self.relocalize_station_id_input.setText(station_id)
        if not station_id:
            self.relocalize_station_name_input.clear()
            self.relocalize_photo_pose_input.clear()
            self.relocalize_camera_input.clear()
            return

        try:
            from ..core.vision_station_storage import VisionStationStorage

            profile = VisionStationStorage.get_profile(station_id, self.relocalize_arm_combo.currentData())
        except Exception:
            profile = None
        if not profile:
            return

        self.relocalize_station_name_input.setText(str(profile.get("station_name") or station_id))
        self.relocalize_photo_pose_input.setText(str(profile.get("photo_pose") or ""))
        self.relocalize_camera_input.setText(str(profile.get("camera_name") or ""))

    def _init_trajectory_ui(self, form_layout: QFormLayout):
        self.trajectory_robot_combo = QComboBox()
        self.trajectory_robot_combo.addItem("R1", "robot1")
        self.trajectory_robot_combo.addItem("R2", "robot2")
        current_robot = self.action_data.get('parameters', {}).get('robot', 'robot1')
        index = self.trajectory_robot_combo.findData(current_robot)
        if index >= 0:
            self.trajectory_robot_combo.setCurrentIndex(index)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(4)

        self.trajectory_path_input = QLineEdit()
        self.trajectory_path_input.setText(self.action_data.get('parameters', {}).get('file_path', ''))
        self.trajectory_path_input.setPlaceholderText("Select recorded trajectory .txt file")

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_trajectory_file)
        path_layout.addWidget(self.trajectory_path_input, stretch=1)
        path_layout.addWidget(browse_btn)

        form_layout.addRow("Robot:", self.trajectory_robot_combo)
        form_layout.addRow("Trajectory:", path_row)

    def _browse_trajectory_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select trajectory file",
            "",
            "Trajectory Files (*.txt);;All Files (*)"
        )
        if filename:
            self.trajectory_path_input.setText(filename)

    def _capture_localization_reference(self):
        try:
            from .udp_receive import get_latest_position

            position = get_latest_position(max_age=2.0, wait_timeout=1.5)
        except Exception as exc:
            QMessageBox.warning(self, "定位补偿", f"读取 UDP 定位失败:\n{exc}")
            return

        if position is None:
            QMessageBox.warning(self, "定位补偿", "未收到有效定位数据，请确认 UDP Tag 已检测到")
            return

        self.localization_reference = {
            "id": position.get("id", -99),
            "x": position.get("x", 0.0),
            "y": position.get("y", 0.0),
            "angle": position.get("angle", 0.0),
            "timestamp": position.get("timestamp", 0.0),
        }
        if hasattr(self, 'compensation_mode_combo'):
            index = self.compensation_mode_combo.findData("udp")
            if index >= 0:
                self.compensation_mode_combo.setCurrentIndex(index)
        self.localization_status_label.setText(self._format_localization_reference(self.localization_reference))

    def _format_localization_reference(self, reference: dict | None) -> str:
        if not reference:
            return "未读取"
        return (
            f"ID={reference.get('id', -99)}  "
            f"X={float(reference.get('x', 0.0)):.3f}cm  "
            f"Y={float(reference.get('y', 0.0)):.3f}cm  "
            f"Angle={float(reference.get('angle', 0.0)):.3f}deg"
        )

    def _on_compensation_mode_changed(self):
        mode = self.compensation_mode_combo.currentData() if hasattr(self, 'compensation_mode_combo') else "none"
        if hasattr(self, 'localization_status_label'):
            self.localization_status_label.parentWidget().setEnabled(mode == "udp")
        if hasattr(self, 'vision_station_combo'):
            self.vision_station_combo.setEnabled(mode == "vision")

    def _refresh_vision_station_choices(self):
        if not hasattr(self, 'vision_station_combo') or not hasattr(self, 'arm_combo'):
            return

        current = self._selected_vision_station_id()
        if not current:
            current = getattr(self, '_vision_station_current', '')

        self.vision_station_combo.blockSignals(True)
        self.vision_station_combo.clear()
        try:
            from ..core.vision_station_storage import VisionStationStorage

            choices = VisionStationStorage.list_station_choices(self.arm_combo.currentText())
        except Exception:
            choices = []

        for station_id, label in choices:
            self.vision_station_combo.addItem(f"{station_id} | {label}", station_id)

        if current:
            index = self.vision_station_combo.findData(current)
            if index >= 0:
                self.vision_station_combo.setCurrentIndex(index)
            else:
                self.vision_station_combo.setEditText(current)
        self.vision_station_combo.blockSignals(False)

    def _selected_vision_station_id(self) -> str:
        if not hasattr(self, 'vision_station_combo'):
            return ""
        data = self.vision_station_combo.currentData()
        if data:
            return str(data).strip()
        text = self.vision_station_combo.currentText().strip()
        if "|" in text:
            text = text.split("|", 1)[0].strip()
        return text

    def _on_executor_changed(self):
        """根据选择的执行器类型切换参数面板"""
        if hasattr(self, 'executor_combo') and hasattr(self, 'param_stack'):
            executor = self.executor_combo.currentData()
            if executor == '吸液枪':
                self.param_stack.setCurrentWidget(self.pipette_widget)
            elif executor == '右臂转圈注液':
                self.param_stack.setCurrentWidget(self.circle_dispense_widget)
            elif executor == '加粉装置':
                self.param_stack.setCurrentWidget(self.tapping_widget)
            elif executor == '智能加粉':
                self.param_stack.setCurrentWidget(self.powder_widget)
            else:
                self.param_stack.setCurrentWidget(self.normal_widget)

    def _on_pipette_operation_changed(self):
        """根据吸液枪操作切换可编辑参数。"""
        if not hasattr(self, 'pipette_operation_combo'):
            return

        operation = self.pipette_operation_combo.currentText()
        is_absorb = operation == '吸'
        is_dispense = operation == '吐'
        is_full_dispense = (
            hasattr(self, 'dispense_mode_combo')
            and self.dispense_mode_combo.currentText() == '全吐'
        )

        if hasattr(self, 'capacity_input'):
            self.capacity_input.setEnabled(is_absorb or (is_dispense and not is_full_dispense))
        if hasattr(self, 'absorb_speed_input'):
            self.absorb_speed_input.setEnabled(is_absorb)
        if hasattr(self, 'dispense_speed_input'):
            self.dispense_speed_input.setEnabled(is_dispense)
        if hasattr(self, 'dispense_mode_combo'):
            self.dispense_mode_combo.setEnabled(is_dispense)

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
            ActionType.TRAJECTORY: "Trajectory",
            ActionType.MOVE: "移动",
            ActionType.BASE_MOVE: "底盘移动",
            ActionType.MANIPULATE: "机械臂",
            ActionType.INSPECT: "检测",
            ActionType.WAIT: "Wait",
            ActionType.CHANGE_GUN: "换枪",
            ActionType.VISION_CAPTURE: "视觉抓取",
            ActionType.VISION_RELOCALIZE: "视觉重定位"
        }
        return type_map.get(self.action_type, "")

    def validate_and_accept(self):
        name = self.name_input.text().strip()
        if not name:
            self.name_input.setFocus()
            return

        # Dedup check
        if name in self._existing_names:
            QMessageBox.warning(self, "名称重复", f'动作名称 "{name}" 已存在，请使用其他名称。')
            self.name_input.setFocus()
            self.name_input.selectAll()
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
                    compensation_mode = (
                        self.compensation_mode_combo.currentData()
                        if hasattr(self, 'compensation_mode_combo')
                        else "none"
                    )
                    if compensation_mode == "udp" and not self.localization_reference:
                        self._capture_localization_reference()
                        if not self.localization_reference:
                            return
                    if compensation_mode == "vision" and not self._selected_vision_station_id():
                        self.vision_station_combo.setFocus()
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

        if self.action_type == ActionType.MANIPULATE and hasattr(self, 'executor_combo'):
            if self.executor_combo.currentData() == '右臂转圈注液':
                pose = self.circle_pose_input.text().strip()
                if not pose:
                    self.circle_pose_input.setFocus()
                    return

        if self.action_type == ActionType.TRAJECTORY:
            file_path = self.trajectory_path_input.text().strip()
            if not file_path:
                self.trajectory_path_input.setFocus()
                return

        if self.action_type == ActionType.VISION_RELOCALIZE:
            if self.relocalize_mode_combo.currentData() == "run":
                if not self._selected_relocalize_station_id():
                    self.relocalize_station_combo.setFocus()
                    return
            elif not self.relocalize_station_name_input.text().strip():
                self.relocalize_station_name_input.setFocus()
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
            compensation_mode = (
                self.compensation_mode_combo.currentData()
                if hasattr(self, 'compensation_mode_combo')
                else "none"
            )
            params = {
                '目标': target,
                '臂': self.arm_combo.currentText(),
                '模式': self.mode_combo.currentData(),
                '点位': self.target_pose_input.text().strip(),
                '补偿': {
                    'mode': compensation_mode,
                }
            }
            if compensation_mode == "udp":
                params['补偿']['udp'] = {
                    'teach_offset': self.localization_reference,
                    'udp_linear_unit': 'cm',
                    'udp_angle_unit': 'deg',
                    'pose_linear_unit': 'm',
                    'pose_angle_unit': 'rad',
                }
                params['定位补偿'] = {
                    'enabled': True,
                    'teach_offset': self.localization_reference,
                    'udp_linear_unit': 'cm',
                    'udp_angle_unit': 'deg',
                    'pose_linear_unit': 'm',
                    'pose_angle_unit': 'rad',
                }
            elif compensation_mode == "vision":
                params['补偿']['vision'] = {
                    'station_id': self._selected_vision_station_id(),
                    'arm': self.arm_combo.currentText(),
                }
            return params
        else:
            return {
                '目标': target,
                '位置': self.position_input.value()
            }
    
    def _build_manipulate_params(self) -> dict:
        """构建执行类动作参数"""
        executor = self.executor_combo.currentData()
        if executor == '吸液枪':
            operation = self.pipette_operation_combo.currentText()
            dispense_mode = self.dispense_mode_combo.currentText()
            return {
                '执行器': executor,
                '操作': operation,
                '容量': self.capacity_input.value(),
                '吸液速度': self.absorb_speed_input.value(),
                '吐液速度': self.dispense_speed_input.value(),
                '吐液容量模式': dispense_mode,
                '全吐': operation == '吐' and dispense_mode == '全吐',
            }
        elif executor == '右臂转圈注液':
            return {
                '执行器': executor,
                '位姿': self.circle_pose_input.text().strip(),
                '半径R': self.circle_radius_input.value(),
                '吐液速度': self.circle_dispense_speed_input.value(),
                '吐液量': self.circle_volume_input.value(),
                '圈数': self.circle_count_input.value(),
                '分段数': self.circle_segments_input.value(),
                '过渡半径': self.circle_blend_radius_input.value(),
                '运动速度': self.circle_move_velocity_input.value(),
                '连续运动': self.circle_continuous_checkbox.isChecked(),
                '顺时针': self.circle_clockwise_checkbox.isChecked(),
            }
        elif executor == '加粉装置':
            operation = self.tapping_operation_combo.currentText()
            params = {
                '执行器': executor,
                '操作': operation,
            }
            if operation == '夹爪移动到':
                params['开度'] = self.tapping_opening_input.value()
            if operation in {'针下降', '针上升', '针正转', '针反转'}:
                params['步数'] = self.tapping_steps_input.value()
            return params
        elif executor == '智能加粉':
            return {
                '执行器': executor,
                '操作': '加粉到目标重量',
                '目标重量mg': self.powder_target_input.value(),
                '容差mg': self.powder_tolerance_input.value(),
                '最大轮次': self.powder_max_rounds_input.value(),
                '稳定等待秒数': self.powder_settle_input.value(),
                '安全位置步数': self.powder_safe_pos_input.value(),
                '加粉位置步数': self.powder_dispense_pos_input.value(),
                '旋转原点步数': self.powder_rotation_home_input.value(),
                '大步步数': self.powder_large_step_input.value(),
                '中步步数': self.powder_medium_step_input.value(),
                '小步步数': self.powder_small_step_input.value(),
                '微步步数': self.powder_micro_step_input.value(),
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
                'x': self.distance_x_input.value(),
                'y': self.distance_y_input.value(),
                'angle': self.distance_angle_input.value()
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
        """构建视觉抓取动作参数（从用户输入收集）"""
        return {
            '目标机械臂': self.vision_robot_combo.currentData(),
            '工作流': self.vision_workflow_combo.currentData(),
            '置信度': self.vision_confidence_input.value(),
            '调试图片': self.vision_debug_checkbox.isChecked(),
            '移动速度': self.vision_velocity_input.value(),
            '夹爪长度': self.vision_gripper_length_input.value(),
        }

    def _build_vision_relocalize_params(self) -> dict:
        """构建视觉重定位动作参数。"""
        action_mode = self.relocalize_mode_combo.currentData()
        params = {
            'action_mode': action_mode,
            'arm': self.relocalize_arm_combo.currentData(),
            'move_mode': self.relocalize_move_mode_combo.currentData(),
        }
        if action_mode == "teach":
            station_name = self.relocalize_station_name_input.text().strip()
            params.update({
                'station_id': station_name,
                'station_name': station_name,
                'photo_pose': self.relocalize_photo_pose_input.text().strip(),
                'camera_name': self.relocalize_camera_input.text().strip(),
            })
            marker_width = self.relocalize_marker_width_input.value()
            marker_height = self.relocalize_marker_height_input.value()
            params.update({
                'marker_width': marker_width,
                'marker_height': marker_height,
                'marker': {
                    'width': marker_width,
                    'height': marker_height,
                },
            })
        else:
            station_id = self._selected_relocalize_station_id()
            params['station_id'] = station_id
            params['station_name'] = self.relocalize_station_name_input.text().strip()
        return params

    def _build_trajectory_params(self) -> dict:
        return {
            'robot': self.trajectory_robot_combo.currentData(),
            'file_path': self.trajectory_path_input.text().strip()
        }
