"""
同步调用异步 LLM 能力的辅助函数。
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Awaitable, TypeVar


T = TypeVar("T")


def run_coro_sync(coro: Awaitable[T]) -> T:
    """在同步代码中运行 coroutine。

    普通线程中直接 `asyncio.run`；如果当前线程已有事件循环，则开一个短线程
    执行，避免 `asyncio.run()` 嵌套报错。这个函数用于兼容旧的同步
    `LLMClient.plan()` 调用点。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if not loop.is_running():
        return loop.run_until_complete(coro)

    future: Future[T] = Future()

    def _runner() -> None:
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as exc:
            future.set_exception(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return future.result()
