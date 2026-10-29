"""Call saved tasks without ever retrying an execution automatically.

uv run --env-file .env python examples/network/tcp-task-client.py --task 移液
Use --request-id again after reconnect to retrieve the same execution receipt.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description="TCP saved-task client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--task")
    parser.add_argument("--run-id", help="Query an existing run instead of starting one")
    parser.add_argument(
        "--request-id", default=None, help="Stable unique ID; reuse only for the same task"
    )
    parser.add_argument("--ca", help="Trusted TLS CA certificate (required for remote deployment)")
    args = parser.parse_args()
    request_id = args.request_id or uuid4().hex
    print(f"execution request_id={request_id}", flush=True)
    connection = socket.create_connection((args.host, args.port), timeout=10)
    if args.ca:
        connection = ssl.create_default_context(cafile=args.ca).wrap_socket(
            connection, server_hostname=args.host
        )
    connection.settimeout(3600)
    try:
        with connection.makefile("rwb") as stream:

            def send(command, identity=None, **values):
                stream.write(
                    (
                        json.dumps(
                            {
                                "version": 1,
                                "request_id": identity or uuid4().hex,
                                "command": command,
                                **values,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode()
                )
                stream.flush()
                line = stream.readline()
                if not line:
                    raise ConnectionError("连接已关闭；请重连查询，勿更换请求 ID 自动重跑")
                result = json.loads(line)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                if not result.get("ok"):
                    raise RuntimeError(result.get("error"))
                return result

            token = os.environ.get("SERVER_AUTH_TOKEN") or os.environ.get("WEBSOCKET_AUTH_TOKEN")
            if token:
                send("authenticate", token=token)
            if args.run_id:
                send("run.status", run_id=args.run_id)
            elif args.task:
                send("control.acquire")
                send("task.run", request_id, task=args.task)
                # Workflow execution survives lease expiry and disconnect.
                # A later cancellation requires acquiring control again.
                while True:
                    line = stream.readline()
                    if not line:
                        raise ConnectionError("连接中断，请按 run_id 查询")
                    event = json.loads(line)
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                    if event.get("event") == "run.finished":
                        break
            else:
                send("task.list")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
