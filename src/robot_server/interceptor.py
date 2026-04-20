"""系统提示注入 + 脚本块检测。

注入目标
---------
WS /ws/chat
    首条含 "messages" 的出站帧 → 在 messages[] 头部插入系统提示

WS /ws/duplex/* 和 /ws/half_duplex/*
    首条 type=="prepare" 的出站帧 → 覆盖 system_prompt 字段

检测目标（网关 → 客户端）
--------------------------
WS /ws/chat 入站帧
    {"text": "..."} 或 {"content": "..."} → 扫描并替换 [[SCRIPT_START]]...[[SCRIPT_END]]

WS /ws/duplex/* 和 /ws/half_duplex/* 入站帧（type=="result"）
    跨帧累积说话轮次文本，轮次结束后检测并执行
"""

import json
import re
from typing import Optional

from .prompts import SCRIPT_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# 脚本块正则
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(
    r"\[\[SCRIPT_START\]\]\s*(.*?)\s*\[\[SCRIPT_END\]\]",
    re.DOTALL,
)
_MD_FENCE_RE = re.compile(r"^```[^\n]*\n?(.*?)\n?```$", re.DOTALL)


# ---------------------------------------------------------------------------
# 注入工具
# ---------------------------------------------------------------------------

def inject_into_messages(body: dict) -> dict:
    """在 body["messages"] 头部插入 SCRIPT_SYSTEM_PROMPT 系统消息。"""
    messages: list = body.get("messages", [])
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            existing = msg.get("content", "")
            messages[i] = {
                "role": "system",
                "content": SCRIPT_SYSTEM_PROMPT + "\n\n" + existing,
            }
            return body
    body["messages"] = [{"role": "system", "content": SCRIPT_SYSTEM_PROMPT}] + messages
    return body


def inject_into_prepare(msg: dict, mode: str = "duplex") -> dict:
    """将系统提示覆写到 prepare 消息的 system_prompt 字段。"""
    field = "system_prompt"
    msg[field] = SCRIPT_SYSTEM_PROMPT.strip()
    return msg


# ---------------------------------------------------------------------------
# 出站注入器（有状态，每个连接一个实例，仅注入一次）
# ---------------------------------------------------------------------------

class OutgoingInjector:
    """有状态注入器，每个连接注入一次系统提示。"""

    def __init__(self, mode: str):
        """
        mode: "chat" | "duplex" | "half_duplex"
        """
        self._mode = mode
        self._done = False

    def process(self, text: str) -> str:
        if self._done:
            return text
        try:
            data = json.loads(text)
            if self._mode == "chat" and "messages" in data:
                data = inject_into_messages(data)
                self._done = True
                return json.dumps(data, ensure_ascii=False)
            if self._mode in ("duplex", "half_duplex") and data.get("type") == "prepare":
                data = inject_into_prepare(data, self._mode)
                self._done = True
                return json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
        return text


# ---------------------------------------------------------------------------
# 脚本块提取
# ---------------------------------------------------------------------------

def extract_scripts(text: str) -> list[dict]:
    """从文本中提取所有 [[SCRIPT_START]]...[[SCRIPT_END]] 块。

    返回列表，每项包含:
        full_match  (str)
        language    (str)
        description (str)
        code        (str)
    """
    results = []
    for m in _SCRIPT_RE.finditer(text):
        raw = m.group(1).strip()
        fence = _MD_FENCE_RE.match(raw)
        if fence:
            raw = fence.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            brace = raw.find("{")
            if brace != -1:
                try:
                    data = json.loads(raw[brace:])
                except json.JSONDecodeError:
                    continue
            else:
                continue
        results.append({
            "full_match": m.group(0),
            "language": data.get("language", ""),
            "description": data.get("Instruction", data.get("description", "")),
            "code": data.get("Instruction", data.get("code", "")),
        })
    return results


class ScriptStreamFilter:
    """逐块过滤 [[SCRIPT_START]]...[[SCRIPT_END]] 内容（跨帧安全）。"""

    _START = "[[SCRIPT_START]]"
    _END = "[[SCRIPT_END]]"

    def __init__(self) -> None:
        self._buf = ""
        self._in_script = False

    def process(self, chunk: str) -> str:
        self._buf += chunk
        output = ""
        while self._buf:
            if self._in_script:
                idx = self._buf.find(self._END)
                if idx == -1:
                    keep = self._partial_prefix_len(self._buf, self._END)
                    self._buf = self._buf[-keep:] if keep else ""
                    break
                else:
                    self._buf = self._buf[idx + len(self._END):]
                    self._in_script = False
            else:
                idx = self._buf.find(self._START)
                if idx == -1:
                    keep = self._partial_prefix_len(self._buf, self._START)
                    output += self._buf[:-keep] if keep else self._buf
                    self._buf = self._buf[-keep:] if keep else ""
                    break
                else:
                    output += self._buf[:idx]
                    self._buf = self._buf[idx + len(self._START):]
                    self._in_script = True
        return output

    @staticmethod
    def _partial_prefix_len(text: str, marker: str) -> int:
        for i in range(min(len(marker) - 1, len(text)), 0, -1):
            if marker.startswith(text[-i:]):
                return i
        return 0


def strip_script_blocks(text: str) -> str:
    return _SCRIPT_RE.sub("", text).strip()


def replace_script_block(text: str, full_match: str, replacement: str) -> str:
    return text.replace(full_match, replacement, 1)


# ---------------------------------------------------------------------------
# 双工轮次累积器
# ---------------------------------------------------------------------------

class TurnAccumulator:
    """跨帧累积一次完整说话轮次的文本。"""

    def __init__(self):
        self._buf = ""
        self._speaking = False

    def process_result(self, msg: dict) -> Optional[str]:
        is_listen: bool = msg.get("is_listen", False)
        end_of_turn: bool = msg.get("end_of_turn", False)
        chunk: str = msg.get("text", "") or ""

        if is_listen:
            if self._speaking and self._buf:
                complete = self._buf
                self._buf = ""
                self._speaking = False
                return complete
            self._speaking = False
            return None

        self._speaking = True
        self._buf += chunk

        if end_of_turn:
            complete = self._buf
            self._buf = ""
            self._speaking = False
            return complete

        return None

    def reset(self):
        self._buf = ""
        self._speaking = False
