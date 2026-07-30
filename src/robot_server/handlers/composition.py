from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from ...core.action_schema import get_action_schema
from ...core.models import (
    ActionDefinition,
    ActionType,
    SequenceEntry,
    SequenceItem,
)
from ..protocol import WebSocketRequest
from .base import WebSocketHandlerHost

logger = logging.getLogger(__name__)


class CompositionWebSocketHandler:
    def __init__(self, server: WebSocketHandlerHost) -> None:
        self._server = server

    async def _handle_list_actions(self, websocket, data: WebSocketRequest) -> None:
        """
        返回动作库（按类型分组）
        响应: {"event": "actions_list", "actions": {...按类型分组...}}
        """
        all_actions = self._server._services.composition.list_actions()

        # 按类型分组（与 GUI 左侧 Tab 对应）
        grouped = {action_type.value: [] for action_type in ActionType}
        for a in all_actions:
            grouped[a.type.value].append(a.to_dict())

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "actions_list",
                    "actions": grouped,
                    "total": len(all_actions),
                }
            )
        )

    async def _handle_get_action_schema(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """
        返回所有动作类型的参数结构定义，前端可据此动态生成表单
        请求: {"action": "get_action_schema"}
        响应: {"event": "action_schema", "types": {...}}
        """
        schema = get_action_schema()

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "action_schema",
                    "types": schema,
                }
            )
        )

    async def _handle_create_action(self, websocket, data: WebSocketRequest) -> None:
        """
        新建动作
        请求: {"action": "create_action", "name": "移动到A点", "type": "MOVE_TO_POINT", "parameters": {...}}
        """
        name = data.get("name", "").strip()
        if not name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "动作名称不能为空"}
                )
            )
            return

        action_type_str = data.get("type", "")
        try:
            action_type = ActionType(action_type_str)
        except ValueError:
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "message": f"无效的动作类型: {action_type_str}，"
                        f"可选: {[t.value for t in ActionType]}",
                    }
                )
            )
            return

        parameters = data.get("parameters", {})

        # 创建动作定义
        action_def = ActionDefinition(
            id=str(uuid4()),
            name=name,
            type=action_type,
            parameters=parameters,
        )

        try:
            action_def = await asyncio.to_thread(
                self._server._services.composition.create_action,
                action_def,
                origin=self._server._composition_origin(websocket),
            )
        except (TypeError, ValueError) as exc:
            await websocket.send(
                self._server._json_msg({"event": "error", "message": str(exc)})
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "action_created",
                    "action": action_def.to_dict(),
                }
            )
        )
        logger.info("新建动作: %s (%s)", name, action_type_str)

    async def _handle_delete_action(self, websocket, data: WebSocketRequest) -> None:
        """
        删除动作
        请求: {"action": "delete_action", "id": "..."}
        """
        action_id = data.get("id", "")
        if not action_id:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "动作 id 不能为空"}
                )
            )
            return

        try:
            await asyncio.to_thread(
                self._server._services.composition.delete_action,
                action_id,
                origin=self._server._composition_origin(websocket),
            )
        except KeyError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"未找到 id 为 '{action_id}' 的动作"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "action_deleted",
                    "id": action_id,
                }
            )
        )
        logger.info("删除动作: %s", action_id)

    async def _handle_update_action(self, websocket, data: WebSocketRequest) -> None:
        """
        更新动作
        请求: {"action": "update_action", "id": "...", "name": "...", "type": "...", "parameters": {...}}
        """
        action_id = data.get("id", "")
        if not action_id:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "动作 id 不能为空"}
                )
            )
            return

        try:
            target = self._server._services.composition.get_action(action_id)
        except KeyError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"未找到 id 为 '{action_id}' 的动作"}
                )
            )
            return

        # 更新字段（只更新提供的字段）
        if "name" in data:
            target.name = data["name"]
        if "type" in data:
            try:
                target.type = ActionType(data["type"])
            except ValueError:
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"无效的动作类型: {data['type']}"}
                    )
                )
                return
        if "parameters" in data:
            target.parameters = data["parameters"]

        try:
            target = await asyncio.to_thread(
                self._server._services.composition.update_action,
                action_id,
                target,
                origin=self._server._composition_origin(websocket),
            )
        except (TypeError, ValueError) as exc:
            await websocket.send(
                self._server._json_msg({"event": "error", "message": str(exc)})
            )
            return
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "action_updated",
                    "action": target.to_dict(),
                }
            )
        )
        logger.info("更新动作: %s", action_id)

    async def _handle_get_sequence(self, websocket, data: WebSocketRequest) -> None:
        """获取当前编排的序列"""
        entries = self._server._services.composition.sequence_entries()
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "sequence",
                    "sequence": [entry.to_dict() for entry in entries],
                }
            )
        )

    async def _handle_add_to_sequence(self, websocket, data: WebSocketRequest) -> None:
        """
        添加动作到序列
        请求: {"action": "add_to_sequence", "items": [
            {"name": "...", "type": "MOVE_TO_POINT", "parameters": {...}},
            ...
        ]}
        也支持传入动作库中的 id: {"action": "add_to_sequence", "action_ids": ["id1", "id2"]}
        """
        action_ids = data.get("action_ids", [])
        items = data.get("items", [])
        if not action_ids and not items:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "请提供 items 或 action_ids"}
                )
            )
            return

        additions: list[SequenceEntry] = []
        if action_ids:
            all_actions = self._server._services.composition.list_actions()
            action_map = {a.id: a for a in all_actions}
            for aid in action_ids:
                if aid in action_map:
                    additions.append(SequenceItem.from_definition(action_map[aid]))
                else:
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "error", "message": f"动作库中不存在 id: {aid}"}
                        )
                    )
                    return

        if items:
            try:
                additions.extend(self._server._parse_sequence(items))
            except (KeyError, TypeError, ValueError) as exc:
                await websocket.send(
                    self._server._json_msg(
                        {
                            "event": "error",
                            "message": f"动作解析失败: {exc}",
                        }
                    )
                )
                return

        sequence = self._server._services.composition.append_sequence(
            additions,
            origin=self._server._composition_origin(websocket),
        )

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "sequence_updated",
                    "sequence": [entry.to_dict() for entry in sequence],
                }
            )
        )

    async def _handle_remove_from_sequence(
        self, websocket, data: WebSocketRequest
    ) -> None:
        """
        删除序列中的某项
        请求: {"action": "remove_from_sequence", "index": 0}
        """
        index = data.get("index")
        try:
            removed = self._server._services.composition.remove_sequence_entry(
                index,
                origin=self._server._composition_origin(websocket),
            )
        except (IndexError, TypeError):
            sequence_length = len(self._server._services.composition.sequence_entries())
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "message": f"无效的索引: {index}，序列长度: {sequence_length}",
                    }
                )
            )
            return

        sequence = self._server._services.composition.sequence_entries()
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "sequence_updated",
                    "removed": removed.to_dict(),
                    "sequence": [entry.to_dict() for entry in sequence],
                }
            )
        )

    async def _handle_move_in_sequence(self, websocket, data: WebSocketRequest) -> None:
        """
        移动序列项位置
        请求: {"action": "move_in_sequence", "from": 0, "to": 1}
        """
        from_idx = data.get("from")
        to_idx = data.get("to")

        if from_idx is None or to_idx is None:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "需要提供 from 和 to 索引"}
                )
            )
            return

        try:
            sequence = self._server._services.composition.move_sequence_entry(
                from_idx,
                to_idx,
                origin=self._server._composition_origin(websocket),
            )
        except (IndexError, TypeError):
            sequence_length = len(self._server._services.composition.sequence_entries())
            await websocket.send(
                self._server._json_msg(
                    {
                        "event": "error",
                        "message": f"索引越界，序列长度: {sequence_length}",
                    }
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "sequence_updated",
                    "sequence": [entry.to_dict() for entry in sequence],
                }
            )
        )

    async def _handle_clear_sequence(self, websocket, data: WebSocketRequest) -> None:
        """清空序列"""
        self._server._services.composition.clear_sequence(
            origin=self._server._composition_origin(websocket),
        )
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "sequence_updated",
                    "sequence": [],
                }
            )
        )

    async def _handle_list_tasks(self, websocket, data: WebSocketRequest) -> None:
        """返回所有已保存的任务文件名"""
        summaries = await asyncio.to_thread(
            self._server._services.composition.list_tasks
        )
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "tasks_list",
                    "tasks": [summary.name for summary in summaries],
                    "summaries": [
                        {
                            "name": summary.name,
                            "steps": summary.step_count,
                        }
                        for summary in summaries
                    ],
                }
            )
        )

    async def _handle_save_task(self, websocket, data: WebSocketRequest) -> None:
        """
        保存当前序列为任务文件
        请求: {"action": "save_task", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        try:
            stored_name = await asyncio.to_thread(
                self._server._services.composition.save_current_task,
                task_name,
                origin=self._server._composition_origin(websocket),
            )
        except ValueError as exc:
            await websocket.send(
                self._server._json_msg({"event": "error", "message": str(exc)})
            )
            return
        steps = len(self._server._services.composition.sequence_entries())
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_saved",
                    "name": stored_name,
                    "steps": steps,
                }
            )
        )
        logger.info("任务已保存: %s", stored_name)

    async def _handle_load_task(self, websocket, data: WebSocketRequest) -> None:
        """
        加载任务到当前序列（不执行）
        请求: {"action": "load_task", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        try:
            entries = await asyncio.to_thread(
                self._server._services.composition.load_task_into_sequence,
                task_name,
                origin=self._server._composition_origin(websocket),
            )
        except (FileNotFoundError, ValueError):
            entries = ()
        if not entries:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_loaded",
                    "name": task_name,
                    "sequence": [entry.to_dict() for entry in entries],
                }
            )
        )
        logger.info("任务已加载: %s", task_name)

    async def _handle_delete_task(self, websocket, data: WebSocketRequest) -> None:
        """
        删除任务文件
        请求: {"action": "delete_task", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        try:
            deleted_name = await asyncio.to_thread(
                self._server._services.composition.delete_task,
                task_name,
                origin=self._server._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_deleted",
                    "name": deleted_name,
                }
            )
        )
        logger.info("任务已删除: %s", deleted_name)

    async def _handle_get_task_detail(self, websocket, data: WebSocketRequest) -> None:
        """
        读取任务文件内容，但不影响当前序列
        请求: {"action": "get_task_detail", "name": "xxx.task"}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        try:
            entries = await asyncio.to_thread(
                self._server._services.composition.load_task,
                task_name,
            )
        except FileNotFoundError:
            entries = ()
        if not entries:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务 '{task_name}' 不存在或为空"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_detail",
                    "name": Path(task_name).with_suffix(".task").name,
                    "sequence": [entry.to_dict() for entry in entries],
                }
            )
        )

    async def _handle_rename_task(self, websocket, data: WebSocketRequest) -> None:
        """
        重命名任务文件
        请求: {"action": "rename_task", "name": "old.task", "new_name": "new.task"}
        """
        task_name = data.get("name", "").strip()
        new_name = data.get("new_name", "").strip()

        if not task_name or not new_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "name 和 new_name 不能为空"}
                )
            )
            return

        try:
            old_name, stored_new_name = await asyncio.to_thread(
                self._server._services.composition.rename_task,
                task_name,
                new_name,
                origin=self._server._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
                )
            )
            return
        except FileExistsError as exc:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务文件 '{exc.args[0]}' 已存在"}
                )
            )
            return
        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_renamed",
                    "name": old_name,
                    "new_name": stored_new_name,
                }
            )
        )

    async def _handle_add_to_task(self, websocket, data: WebSocketRequest) -> None:
        """
        直接向任务文件追加/插入动作
        请求:
          {"action": "add_to_task", "name": "x.task", "items": [...], "index": 0}
          {"action": "add_to_task", "name": "x.task", "action_ids": ["..."], "index": 0}
        """
        task_name = data.get("name", "").strip()
        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        insert_items: list[SequenceEntry] = []
        action_ids = data.get("action_ids", [])
        if action_ids:
            all_actions = self._server._services.composition.list_actions()
            action_map = {a.id: a for a in all_actions}
            for aid in action_ids:
                if aid not in action_map:
                    await websocket.send(
                        self._server._json_msg(
                            {"event": "error", "message": f"动作库中不存在 id: {aid}"}
                        )
                    )
                    return
                insert_items.append(SequenceItem.from_definition(action_map[aid]))

        items = data.get("items", [])
        if items:
            try:
                insert_items.extend(self._server._parse_sequence(items))
            except (KeyError, TypeError, ValueError) as exc:
                await websocket.send(
                    self._server._json_msg(
                        {"event": "error", "message": f"动作解析失败: {exc}"}
                    )
                )
                return

        if not insert_items:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "请提供 items 或 action_ids"}
                )
            )
            return

        index = data.get("index")
        try:
            sequence = await asyncio.to_thread(
                self._server._services.composition.insert_task_entries,
                task_name,
                insert_items,
                index=index,
                origin=self._server._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
                )
            )
            return
        except (IndexError, TypeError):
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"无效的插入位置: {index}"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_updated",
                    "name": Path(task_name).with_suffix(".task").name,
                    "sequence": [entry.to_dict() for entry in sequence],
                }
            )
        )

    async def _handle_remove_from_task(self, websocket, data: WebSocketRequest) -> None:
        """
        直接删除任务文件中的某一步
        请求: {"action": "remove_from_task", "name": "x.task", "index": 0}
        """
        task_name = data.get("name", "").strip()
        index = data.get("index")

        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        try:
            removed, sequence = await asyncio.to_thread(
                self._server._services.composition.remove_task_entry,
                task_name,
                index,
                origin=self._server._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
                )
            )
            return
        except (IndexError, TypeError):
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"无效的索引: {index}"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_updated",
                    "name": Path(task_name).with_suffix(".task").name,
                    "removed": removed.to_dict(),
                    "sequence": [entry.to_dict() for entry in sequence],
                }
            )
        )

    async def _handle_move_in_task(self, websocket, data: WebSocketRequest) -> None:
        """
        直接调整任务文件内部顺序
        请求: {"action": "move_in_task", "name": "x.task", "from": 0, "to": 1}
        """
        task_name = data.get("name", "").strip()
        from_idx = data.get("from")
        to_idx = data.get("to")

        if not task_name:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "任务名称不能为空"}
                )
            )
            return

        try:
            sequence = await asyncio.to_thread(
                self._server._services.composition.move_task_entry,
                task_name,
                from_idx,
                to_idx,
                origin=self._server._composition_origin(websocket),
            )
        except FileNotFoundError:
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": f"任务文件 '{task_name}' 不存在"}
                )
            )
            return
        except (IndexError, TypeError):
            await websocket.send(
                self._server._json_msg(
                    {"event": "error", "message": "from/to 索引无效或越界"}
                )
            )
            return

        await websocket.send(
            self._server._json_msg(
                {
                    "event": "task_updated",
                    "name": Path(task_name).with_suffix(".task").name,
                    "sequence": [entry.to_dict() for entry in sequence],
                }
            )
        )
