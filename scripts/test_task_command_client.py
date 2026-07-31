#!/usr/bin/env python3
"""命令行测试客户端：发送固定任务 command_type 与执行控制命令。

示例：
    python scripts/test_task_command_client.py 730-peiye --aspirate-volume 200
    python scripts/test_task_command_client.py 730-zhuye --station-id 2 --height-level middle --method circular --flow-rate 800 --volume 500
    python scripts/test_task_command_client.py pause
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any
from uuid import uuid4

import websockets


TASK_COMMANDS = (
    "730-1-2",
    "730-peiye",
    "730-2-3",
    "730-zhuye",
    "730-3-1",
)
CONTROL_COMMANDS = ("pause", "resume", "stop")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="向 robot_llm WebSocket 服务发送固定任务测试命令",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "operation",
        choices=(*TASK_COMMANDS, *CONTROL_COMMANDS),
        help="固定任务 command_type，或执行控制命令",
    )
    parser.add_argument("--url", default="ws://127.0.0.1:8765", help="WebSocket 服务地址")
    parser.add_argument("--request-id", help="可选的客户端请求标识；默认自动生成")
    parser.add_argument("--aspirate-volume", type=float, help="aspirate_volume_ml")
    parser.add_argument("--station-id", type=int, choices=(1, 2, 3, 4), help="station_id")
    parser.add_argument(
        "--height-level",
        choices=("upper", "middle", "lower"),
        help="height_level",
    )
    parser.add_argument("--method", choices=("vertical", "circular"), help="method")
    parser.add_argument("--flow-rate", type=float, help="flow_rate_ml_min")
    parser.add_argument("--volume", type=float, help="volume_ml")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="发送后不等待任务完成事件",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600,
        help="等待任务完成事件的最长秒数；仅固定任务生效",
    )
    return parser.parse_args()


def build_request(args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    """只发送用户显式给出的字段，让服务端决定任务模板默认值。"""
    if args.operation in CONTROL_COMMANDS:
        return {"action": args.operation}, None

    request_id = args.request_id or str(uuid4())
    request: dict[str, Any] = {
        "command_type": args.operation,
        "request_id": request_id,
    }
    optional_fields = {
        "aspirate_volume_ml": args.aspirate_volume,
        "station_id": args.station_id,
        "height_level": args.height_level,
        "method": args.method,
        "flow_rate_ml_min": args.flow_rate,
        "volume_ml": args.volume,
    }
    request.update({key: value for key, value in optional_fields.items() if value is not None})
    return request, request_id


async def send_and_watch(args: argparse.Namespace) -> int:
    request, request_id = build_request(args)
    print("连接:", args.url)
    print("发送:", json.dumps(request, ensure_ascii=False))

    try:
        async with websockets.connect(args.url) as websocket:
            await websocket.send(json.dumps(request, ensure_ascii=False))
            if args.no_wait:
                return 0

            # 控制接口是单次响应；固定任务需要等待带相同 request_id 的最终事件。
            terminal_events = {"paused", "resumed", "stopped", "error"}
            async with asyncio.timeout(args.timeout):
                async for raw_message in websocket:
                    event = json.loads(raw_message)
                    print("收到:", json.dumps(event, ensure_ascii=False))

                    event_name = event.get("event")
                    if request_id is None:
                        if event_name in terminal_events:
                            return 0 if event_name != "error" else 1
                        continue

                    # 同一连接也可能收到其他客户端的广播，仅以 request_id 判断本次任务。
                    if event.get("request_id") != request_id:
                        continue
                    if event_name == "command_completed":
                        return 0
                    if event_name in {"command_failed", "command_rejected"}:
                        return 1
    except TimeoutError:
        print(f"等待超时：{args.timeout} 秒", file=sys.stderr)
        return 1
    except (OSError, websockets.WebSocketException, json.JSONDecodeError) as exc:
        print(f"客户端错误: {exc}", file=sys.stderr)
        return 1

    return 0


def main() -> int:
    return asyncio.run(send_and_watch(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
