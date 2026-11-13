"""JSON-safe action outputs and optional per-workflow-node export settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import re
from typing import Any, Mapping
from collections import Counter
from collections.abc import Iterable

ACTION_OUTPUT_MAX_BYTES = 65536
RUN_OUTPUT_MAX_BYTES = 524288


class OutputDataError(ValueError):
    """An action produced non-JSON or oversized business data."""


def json_output(value: object, *, limit: int = ACTION_OUTPUT_MAX_BYTES) -> dict[str, Any]:
    """Validate and detach JSON data; never stringify SDK/NumPy/image objects."""
    count = 0

    def check(item: object, depth: int) -> None:
        nonlocal count
        count += 1
        if count > 10000 or depth > 16:
            raise OutputDataError("输出数据过深或元素过多")
        if isinstance(item, str) and len(item) > limit:
            raise OutputDataError("输出文本过长，请使用制品引用")
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(isinstance(key, str) for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise OutputDataError("输出仅支持 JSON 类型及有限数值，不支持图片、设备对象或 NaN")

    if type(value) is not dict:
        raise OutputDataError("动作 outputs 必须是 JSON 对象")
    check(value, 0)
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > limit:
        raise OutputDataError(f"输出数据超过 {limit} 字节限制，请使用制品引用")
    return json.loads(encoded)


@dataclass(frozen=True, slots=True)
class NodeOutput:
    name: str
    field: str = ""
    mode: str = "single"
    required: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,63}", self.name
        ):
            raise ValueError("输出名称须为字母或下划线开头的 1～64 位字母、数字、下划线")
        if not isinstance(self.field, str) or (self.field and not self.field.startswith("/")):
            raise ValueError("输出字段须为 JSON Pointer（例如 /height），留空表示全部结果")
        if re.search(r"~(?![01])", self.field) or len(self.field) > 256:
            raise ValueError("输出字段路径无效或过长")
        if self.mode not in ("single", "all", "last") or type(self.required) is not bool:
            raise ValueError("无效的输出收集模式或必需标志")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NodeOutput:
        if not isinstance(value, Mapping) or set(value) - {"name", "field", "mode", "required"}:
            raise ValueError("节点输出设置格式无效")
        return cls(**dict(value))

    def select(self, outputs: dict[str, Any]) -> object:
        if not outputs:
            raise KeyError("节点没有产生业务输出")
        value: Any = outputs
        for token in self.field.split("/")[1:] if self.field else ():
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict):
                value = value[key]
            elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
                value = value[int(key)]
            else:
                raise KeyError(self.field)
        return value


def supports_action_output(action_type: object) -> bool:
    """Capability catalog; extend when a new output-producing action is added."""
    return getattr(action_type, "value", action_type) in {"VISION_CAPTURE", "VISION_RELOCALIZE"}


def validate_output_specs(specs: Iterable[tuple[str, NodeOutput | None]]) -> None:
    owners: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for node_id, output in specs:
        if output is None:
            continue
        if not isinstance(output, NodeOutput):
            raise ValueError("节点输出设置必须是 NodeOutput")
        owner = owners.setdefault(output.name, node_id)
        if owner != node_id:
            raise ValueError(f"输出名称重复: {output.name}，请为不同节点使用不同名称")
        counts[node_id] += 1
        if counts[node_id] > 1 and output.mode == "single":
            raise ValueError(
                f"循环内输出 {output.name} 不能使用单次结果，请选择收集全部或仅最后一次"
            )
