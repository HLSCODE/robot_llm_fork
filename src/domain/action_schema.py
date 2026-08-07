"""Canonical action parameter schema shared by UI, APIs, and planners."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, TypedDict

from .models import ActionType


class ActionFieldSchema(TypedDict, total=False):
    type: str
    options: list[Any]
    default: Any
    min: float
    max: float
    unit: str
    label: str
    required: bool
    readonly: bool
    placeholder: str


class ActionVariantSchema(TypedDict):
    description: str
    fields: dict[str, ActionFieldSchema]


class ActionTypeSchema(TypedDict, total=False):
    label: str
    description: str
    fields: dict[str, ActionFieldSchema]
    variants: dict[str, ActionVariantSchema]
    variant_key: str
    note: str


class ActionParameterIssueCode(str, Enum):
    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    UNKNOWN_VARIANT = "unknown_variant"
    INVALID_TYPE = "invalid_type"
    INVALID_OPTION = "invalid_option"
    OUT_OF_RANGE = "out_of_range"


@dataclass(frozen=True, slots=True)
class ActionParameterIssue:
    code: ActionParameterIssueCode
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ActionParameterValidation:
    """Normalized parameters and every schema violation found."""

    parameters: dict[str, Any]
    issues: tuple[ActionParameterIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def message(self) -> str:
        return "；".join(issue.message for issue in self.issues)


_MISSING = object()


def _field(
    field_type: str,
    label: str,
    *,
    options: list[Any] | None = None,
    default: Any = _MISSING,
    minimum: float | None = None,
    maximum: float | None = None,
    unit: str = "",
    required: bool = False,
    readonly: bool = False,
    placeholder: str = "",
) -> ActionFieldSchema:
    schema: ActionFieldSchema = {
        "type": field_type,
        "label": label,
    }
    if options is not None:
        schema["options"] = options
    if default is not _MISSING:
        schema["default"] = default
    if minimum is not None:
        schema["min"] = minimum
    if maximum is not None:
        schema["max"] = maximum
    if unit:
        schema["unit"] = unit
    if required:
        schema["required"] = True
    if readonly:
        schema["readonly"] = True
    if placeholder:
        schema["placeholder"] = placeholder
    return schema


def _variant(
    description: str,
    fields: dict[str, ActionFieldSchema],
) -> ActionVariantSchema:
    return {
        "description": description,
        "fields": fields,
    }


_ACTION_SCHEMAS: dict[str, ActionTypeSchema] = {
    ActionType.MOVE.value: {
        "label": "移动类",
        "description": "机械臂移动 / 升降平台移动",
        "variant_key": "目标",
        "variants": {
            "机械臂": _variant(
                "控制机械臂移动到指定点位",
                {
                    "目标": _field(
                        "select",
                        "目标",
                        options=["机械臂"],
                        default="机械臂",
                    ),
                    "臂": _field(
                        "select",
                        "臂",
                        options=["左", "右"],
                        default="左",
                    ),
                    "模式": _field(
                        "select",
                        "运动模式",
                        options=[
                            {
                                "value": "move_j",
                                "label": "关节运动 (move_j)",
                            },
                            {
                                "value": "move_l",
                                "label": "直线运动 (move_l)",
                            },
                        ],
                        default="move_j",
                    ),
                    "点位": _field(
                        "pose",
                        "点位",
                        required=True,
                        placeholder=("例如: [-0.048, -0.269, -0.101, 3.109, -0.094, -1.592]"),
                    ),
                    "补偿": _field("object", "补偿配置"),
                },
            ),
            "机械臂相对": _variant(
                "在基座坐标系中执行有界笛卡尔相对移动",
                {
                    "目标": _field(
                        "select",
                        "目标",
                        options=["机械臂相对"],
                        default="机械臂相对",
                    ),
                    "臂": _field(
                        "select",
                        "臂",
                        options=["左", "右"],
                        required=True,
                    ),
                    "坐标系": _field(
                        "select",
                        "坐标系",
                        options=["base"],
                        default="base",
                    ),
                    "模式": _field(
                        "select",
                        "运动模式",
                        options=[{"value": "move_l", "label": "直线运动 (move_l)"}],
                        default="move_l",
                    ),
                    "x_mm": _field(
                        "number", "X 偏移", default=0.0, minimum=-100, maximum=100, unit="mm"
                    ),
                    "y_mm": _field(
                        "number", "Y 偏移", default=0.0, minimum=-100, maximum=100, unit="mm"
                    ),
                    "z_mm": _field(
                        "number", "Z 偏移", default=0.0, minimum=-100, maximum=100, unit="mm"
                    ),
                },
            ),
            "身体": _variant(
                "控制升降平台移动到指定位置",
                {
                    "目标": _field(
                        "select",
                        "目标",
                        options=["身体"],
                        default="身体",
                    ),
                    "位置": _field(
                        "number",
                        "目标位置",
                        default=0,
                        minimum=0,
                        maximum=500000,
                        unit="脉冲",
                    ),
                },
            ),
        },
    },
    ActionType.BASE_MOVE.value: {
        "label": "底盘移动",
        "description": "按位置或相对距离移动底盘",
        "variant_key": "move_mode",
        "variants": {
            "position": _variant(
                "移动到底盘地图位置",
                {
                    "move_mode": _field(
                        "select",
                        "移动方式",
                        options=["position"],
                        default="position",
                    ),
                    "id": _field(
                        "number",
                        "目标位置 ID",
                        default=0,
                        minimum=-100,
                        maximum=100,
                    ),
                    "cid": _field(
                        "number",
                        "目标位置 CID",
                        default=0,
                        minimum=0,
                        maximum=100,
                    ),
                },
            ),
            "distance": _variant(
                "按相对距离和角度移动",
                {
                    "move_mode": _field(
                        "select",
                        "移动方式",
                        options=["distance"],
                        default="distance",
                    ),
                    "x": _field(
                        "number",
                        "X 距离",
                        default=0.0,
                        minimum=-1000,
                        maximum=1000,
                        unit="cm",
                    ),
                    "y": _field(
                        "number",
                        "Y 距离",
                        default=0.0,
                        minimum=-1000,
                        maximum=1000,
                        unit="cm",
                    ),
                    "angle": _field(
                        "number",
                        "角度",
                        default=0.0,
                        minimum=-360,
                        maximum=360,
                        unit="deg",
                    ),
                },
            ),
        },
    },
    ActionType.MANIPULATE.value: {
        "label": "执行类",
        "description": "快换手、继电器、夹爪、吸液枪、颈部等执行器操作",
        "variant_key": "执行器",
        "variants": {
            "快换手": _variant(
                "控制快换手开关",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["快换手"],
                        default="快换手",
                    ),
                    "编号": _field(
                        "select",
                        "编号",
                        options=[1, 2],
                        default=1,
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=["开", "关"],
                        default="开",
                    ),
                },
            ),
            "继电器": _variant(
                "控制继电器开关",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["继电器"],
                        default="继电器",
                    ),
                    "编号": _field(
                        "select",
                        "编号",
                        options=[1, 2],
                        default=1,
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=["开", "关"],
                        default="开",
                    ),
                },
            ),
            "夹爪": _variant(
                "控制夹爪开关",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["夹爪"],
                        default="夹爪",
                    ),
                    "编号": _field(
                        "select",
                        "编号",
                        options=[1, 2],
                        default=1,
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=["开", "关"],
                        default="开",
                    ),
                },
            ),
            "吸液枪": _variant(
                "控制吸液枪吸液/吐液",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["吸液枪"],
                        default="吸液枪",
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=["吸", "吐", "退枪头"],
                        default="吸",
                    ),
                    "容量": _field(
                        "number",
                        "容量",
                        default=500,
                        minimum=1,
                        maximum=10000,
                        unit="ul",
                    ),
                    "吸液速度": _field(
                        "number",
                        "吸液速度",
                        default=1200,
                        minimum=1,
                        maximum=9999,
                        unit="ul/s",
                    ),
                    "吐液速度": _field(
                        "number",
                        "吐液速度",
                        default=800,
                        minimum=1,
                        maximum=9999,
                        unit="ul/s",
                    ),
                    "吐液容量模式": _field(
                        "select",
                        "吐液容量",
                        options=["指定容量", "全吐"],
                        default="指定容量",
                    ),
                    "全吐": _field(
                        "boolean",
                        "是否全吐",
                        default=False,
                    ),
                },
            ),
            "颈部": _variant(
                "控制颈部水平和垂直舵机",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["颈部"],
                        default="颈部",
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=["水平移动", "垂直移动", "双轴移动", "复位"],
                        default="复位",
                    ),
                    "水平PWM": _field(
                        "number",
                        "水平PWM",
                        default=1600,
                        minimum=500,
                        maximum=2500,
                    ),
                    "垂直PWM": _field(
                        "number",
                        "垂直PWM",
                        default=1600,
                        minimum=500,
                        maximum=2500,
                    ),
                    "时长ms": _field(
                        "number",
                        "运动时长",
                        default=1000,
                        minimum=0,
                        maximum=9999,
                        unit="ms",
                    ),
                },
            ),
            "右臂转圈注液": _variant(
                "Robot2 以给定位姿的 x/y 为圆心画圆，同时控制吸液枪吐液",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["右臂转圈注液"],
                        default="右臂转圈注液",
                    ),
                    "位姿": _field(
                        "text",
                        "圆心位姿",
                        required=True,
                        placeholder=("例如: [-0.058,-0.412,-0.154,-2.934,0.428,-2.722]"),
                    ),
                    "半径R": _field(
                        "number",
                        "半径R",
                        default=10,
                        minimum=0.1,
                        maximum=500,
                        unit="mm",
                    ),
                    "吐液速度": _field(
                        "number",
                        "吐液速度",
                        default=800,
                        minimum=1,
                        maximum=9999,
                        unit="ul/s",
                    ),
                    "吐液量": _field(
                        "number",
                        "吐液量",
                        default=500,
                        minimum=1,
                        maximum=10000,
                        unit="ul",
                    ),
                    "圈数": _field(
                        "number",
                        "圈数",
                        default=1,
                        minimum=0.1,
                        maximum=20,
                    ),
                    "分段数": _field(
                        "number",
                        "每圈分段",
                        default=72,
                        minimum=8,
                        maximum=360,
                    ),
                    "过渡半径": _field(
                        "number",
                        "过渡半径",
                        default=20,
                        minimum=0,
                        maximum=100,
                    ),
                    "运动速度": _field(
                        "number",
                        "运动速度",
                        default=10,
                        minimum=1,
                        maximum=100,
                    ),
                    "连续运动": _field(
                        "boolean",
                        "连续运动",
                        default=True,
                    ),
                    "顺时针": _field(
                        "boolean",
                        "顺时针",
                        default=False,
                    ),
                },
            ),
            "加粉装置": _variant(
                "手动控制加粉装置夹爪、升降和旋转",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["加粉装置"],
                        default="加粉装置",
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=[
                            "使能",
                            "夹爪移动到",
                            "夹爪闭合",
                            "夹爪张开",
                            "针下降",
                            "针上升",
                            "针正转",
                            "针反转",
                            "针停止",
                            "针旋转停止",
                        ],
                        default="使能",
                    ),
                    "步数": _field(
                        "number",
                        "步数",
                        default=5000,
                        minimum=-500000,
                        maximum=500000,
                        unit="步",
                    ),
                    "开度": _field(
                        "number",
                        "夹爪开度",
                        default=50,
                        minimum=0,
                        maximum=100,
                        unit="%",
                    ),
                },
            ),
            "智能加粉": _variant(
                "读取天平并闭环控制加粉装置，直到达到目标加粉量",
                {
                    "执行器": _field(
                        "select",
                        "执行器",
                        options=["智能加粉"],
                        default="智能加粉",
                    ),
                    "操作": _field(
                        "select",
                        "操作",
                        options=["加粉到目标重量"],
                        default="加粉到目标重量",
                    ),
                    "目标重量mg": _field(
                        "number",
                        "目标重量",
                        default=100,
                        minimum=0.1,
                        maximum=100000,
                        unit="mg",
                    ),
                    "容差mg": _field(
                        "number",
                        "容差",
                        default=5,
                        minimum=0.1,
                        maximum=10000,
                        unit="mg",
                    ),
                    "最大轮次": _field(
                        "number",
                        "最大轮次",
                        default=20,
                        minimum=1,
                        maximum=200,
                    ),
                    "稳定等待秒数": _field(
                        "number",
                        "稳定等待",
                        default=2,
                        minimum=0,
                        maximum=60,
                        unit="s",
                    ),
                    "安全位置步数": _field(
                        "number",
                        "安全位置",
                        default=0,
                        minimum=-500000,
                        maximum=500000,
                        unit="步",
                    ),
                    "加粉位置步数": _field(
                        "number",
                        "加粉位置",
                        default=50000,
                        minimum=-500000,
                        maximum=500000,
                        unit="步",
                    ),
                    "旋转原点步数": _field(
                        "number",
                        "旋转原点",
                        default=0,
                        minimum=-500000,
                        maximum=500000,
                        unit="步",
                    ),
                    "大步步数": _field(
                        "number",
                        "大步",
                        default=20000,
                        minimum=1,
                        maximum=500000,
                        unit="步",
                    ),
                    "中步步数": _field(
                        "number",
                        "中步",
                        default=8000,
                        minimum=1,
                        maximum=500000,
                        unit="步",
                    ),
                    "小步步数": _field(
                        "number",
                        "小步",
                        default=2000,
                        minimum=1,
                        maximum=500000,
                        unit="步",
                    ),
                    "微步步数": _field(
                        "number",
                        "微步",
                        default=500,
                        minimum=1,
                        maximum=500000,
                        unit="步",
                    ),
                    "大步阈值mg": _field(
                        "number",
                        "大步阈值",
                        default=25,
                        minimum=0.1,
                        maximum=100000,
                        unit="mg",
                    ),
                    "中步阈值mg": _field(
                        "number",
                        "中步阈值",
                        default=10,
                        minimum=0.1,
                        maximum=100000,
                        unit="mg",
                    ),
                    "小步阈值mg": _field(
                        "number",
                        "小步阈值",
                        default=3,
                        minimum=0.1,
                        maximum=100000,
                        unit="mg",
                    ),
                },
            ),
        },
    },
    ActionType.INSPECT.value: {
        "label": "检测类",
        "description": "传感器读取与阈值判定",
        "fields": {
            "Sensor_ID": _field(
                "text",
                "传感器 ID",
                required=True,
            ),
            "Threshold": _field(
                "number",
                "判定阈值",
                default=0,
                minimum=-9999,
                maximum=9999,
            ),
            "Timeout": _field(
                "number",
                "超时时间",
                default=5,
                minimum=0.1,
                maximum=60,
                unit="s",
            ),
        },
    },
    ActionType.WAIT.value: {
        "label": "等待类",
        "description": "等待指定秒数",
        "fields": {
            "wait_seconds": _field(
                "number",
                "等待时间",
                default=1.0,
                minimum=0.1,
                maximum=3600,
                unit="s",
            ),
        },
    },
    ActionType.CHANGE_GUN.value: {
        "label": "换枪类",
        "description": "取/放工具头",
        "fields": {
            "Gun_Position": _field(
                "select",
                "枪位",
                options=[1, 2],
                default=1,
            ),
            "Operation": _field(
                "select",
                "取/放",
                options=["取", "放"],
                default="取",
            ),
        },
    },
    ActionType.VISION_CAPTURE.value: {
        "label": "视觉类",
        "description": "视觉识别 + 自动抓取（参数已固定）",
        "fields": {
            "目标机械臂": _field(
                "text",
                "目标机械臂",
                default="robot1",
                readonly=True,
            ),
            "工作流": _field(
                "text",
                "工作流",
                default="bottle",
                readonly=True,
            ),
            "置信度": _field(
                "number",
                "置信度",
                default=0.7,
                minimum=0,
                maximum=1,
                readonly=True,
            ),
            "调试图片": _field(
                "boolean",
                "调试图片",
                default=True,
                readonly=True,
            ),
            "移动速度": _field(
                "number",
                "移动速度",
                default=15,
                minimum=1,
                maximum=100,
                unit="mm/s",
                readonly=True,
            ),
            "夹爪长度": _field(
                "number",
                "夹爪长度",
                default=150.0,
                minimum=0,
                unit="mm",
                readonly=True,
            ),
        },
        "note": "视觉抓取参数已固定，前端仅需填写动作名称即可",
    },
    ActionType.VISION_RELOCALIZE.value: {
        "label": "视觉重定位",
        "description": "移动到拍照位，识别 Tag，并更新本次任务的工位定位状态",
        "fields": {
            "action_mode": _field(
                "select",
                "动作模式",
                options=["run", "teach"],
                default="run",
            ),
            "arm": _field(
                "select",
                "机械臂",
                options=["left", "right"],
                default="left",
            ),
            "station_id": _field("text", "工位 ID"),
            "station_name": _field(
                "text",
                "工位名称",
                required=True,
            ),
            "photo_pose": _field("text", "示教拍照位姿"),
            "camera_name": _field("text", "示教相机名称"),
            "marker_width": _field(
                "number",
                "示教 marker 宽度",
                default=0.158,
                minimum=0.000001,
            ),
            "marker_height": _field(
                "number",
                "示教 marker 高度",
                default=0.158,
                minimum=0.000001,
            ),
            "move_mode": _field(
                "select",
                "移动模式",
                options=["move_j", "move_l"],
                default="move_j",
            ),
        },
        "note": (
            "工位名称是唯一用户输入；photo_pose、camera_name、marker "
            "宽高只在 action_mode=teach 时填写"
        ),
    },
    ActionType.TRAJECTORY.value: {
        "label": "轨迹类",
        "description": "执行已录制的机械臂轨迹文件",
        "fields": {
            "robot": _field(
                "select",
                "机械臂",
                options=["robot1", "robot2"],
                default="robot1",
            ),
            "file_path": _field(
                "text",
                "轨迹文件",
                required=True,
            ),
        },
    },
}


def get_action_schema() -> dict[str, ActionTypeSchema]:
    """Return a caller-owned copy of the canonical presentation schema."""
    return deepcopy(_ACTION_SCHEMAS)


def get_action_fields(
    action_type: ActionType,
    parameters: dict[str, Any],
) -> tuple[dict[str, ActionFieldSchema] | None, ActionParameterIssue | None]:
    schema = _ACTION_SCHEMAS[action_type.value]
    variants = schema.get("variants")
    if variants is None:
        return deepcopy(schema.get("fields", {})), None

    variant_key = schema["variant_key"]
    variant_name = parameters.get(variant_key)
    if variant_name is None:
        variant_name = _infer_variant(variants, parameters)
    if not isinstance(variant_name, str) or variant_name not in variants:
        return None, ActionParameterIssue(
            code=ActionParameterIssueCode.UNKNOWN_VARIANT,
            field=variant_key,
            message=(f"{action_type.value} 参数 {variant_key} 必须是 {', '.join(variants)} 之一"),
        )
    return deepcopy(variants[variant_name]["fields"]), None


def validate_action_parameters(
    action_type: ActionType,
    parameters: object,
    *,
    apply_defaults: bool = True,
    reject_unknown: bool = True,
) -> ActionParameterValidation:
    """Validate and normalize one action parameter mapping."""
    if not isinstance(parameters, dict):
        return ActionParameterValidation(
            parameters={},
            issues=(
                ActionParameterIssue(
                    code=ActionParameterIssueCode.INVALID_TYPE,
                    field="",
                    message="动作参数必须是字典",
                ),
            ),
        )

    fields, variant_issue = get_action_fields(action_type, parameters)
    if variant_issue is not None or fields is None:
        return ActionParameterValidation(
            parameters={},
            issues=(variant_issue,) if variant_issue else (),
        )

    normalized: dict[str, Any] = {}
    issues: list[ActionParameterIssue] = []
    for field_name, field_schema in fields.items():
        if field_name in parameters:
            value = parameters[field_name]
        elif apply_defaults and "default" in field_schema:
            value = deepcopy(field_schema["default"])
        elif field_schema.get("required", False):
            issues.append(
                ActionParameterIssue(
                    code=ActionParameterIssueCode.MISSING_FIELD,
                    field=field_name,
                    message=f"缺少必填动作参数: {field_name}",
                )
            )
            continue
        else:
            continue

        issue = _validate_field_value(field_name, value, field_schema)
        if issue is not None:
            issues.append(issue)
            continue
        normalized[field_name] = deepcopy(value)

    if reject_unknown:
        for field_name in parameters.keys() - fields.keys():
            issues.append(
                ActionParameterIssue(
                    code=ActionParameterIssueCode.UNKNOWN_FIELD,
                    field=field_name,
                    message=f"未知动作参数: {field_name}",
                )
            )

    return ActionParameterValidation(
        parameters=normalized,
        issues=tuple(issues),
    )


def _infer_variant(
    variants: dict[str, ActionVariantSchema],
    parameters: dict[str, Any],
) -> str | None:
    supplied_fields = set(parameters)
    candidates = [
        variant_name
        for variant_name, variant in variants.items()
        if supplied_fields <= set(variant["fields"])
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not supplied_fields:
        return next(iter(variants), None)
    return None


def _validate_field_value(
    field_name: str,
    value: Any,
    schema: ActionFieldSchema,
) -> ActionParameterIssue | None:
    field_type = schema["type"]
    if schema.get("required", False) and isinstance(value, str) and not value.strip():
        return ActionParameterIssue(
            code=ActionParameterIssueCode.MISSING_FIELD,
            field=field_name,
            message=f"必填动作参数 {field_name} 不能为空",
        )
    if not _matches_field_type(value, field_type):
        return ActionParameterIssue(
            code=ActionParameterIssueCode.INVALID_TYPE,
            field=field_name,
            message=f"动作参数 {field_name} 类型必须是 {field_type}",
        )

    if field_type == "number":
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            return ActionParameterIssue(
                code=ActionParameterIssueCode.INVALID_TYPE,
                field=field_name,
                message=f"动作参数 {field_name} 必须是有限数值",
            )
        if "min" in schema and numeric_value < schema["min"]:
            return ActionParameterIssue(
                code=ActionParameterIssueCode.OUT_OF_RANGE,
                field=field_name,
                message=f"动作参数 {field_name} 不能小于 {schema['min']}",
            )
        if "max" in schema and numeric_value > schema["max"]:
            return ActionParameterIssue(
                code=ActionParameterIssueCode.OUT_OF_RANGE,
                field=field_name,
                message=f"动作参数 {field_name} 不能大于 {schema['max']}",
            )

    if field_type == "select":
        options = [_option_value(option) for option in schema["options"]]
        if not any(_same_option(value, option) for option in options):
            return ActionParameterIssue(
                code=ActionParameterIssueCode.INVALID_OPTION,
                field=field_name,
                message=f"动作参数 {field_name} 不在允许选项中",
            )
    return None


def _matches_field_type(value: Any, field_type: str) -> bool:
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "text":
        return isinstance(value, str)
    if field_type == "select":
        return isinstance(value, (str, int, float)) and not isinstance(
            value,
            bool,
        )
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "object":
        return isinstance(value, dict)
    if field_type == "pose":
        return (
            isinstance(value, list)
            and len(value) == 6
            and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value
            )
        )
    return False


def _option_value(option: Any) -> Any:
    if isinstance(option, dict):
        return option.get("value")
    return option


def _same_option(value: Any, option: Any) -> bool:
    if type(value) is type(option):
        return value == option
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isinstance(option, (int, float))
        and not isinstance(option, bool)
    ):
        return float(value) == float(option)
    return False
