import json
from pathlib import Path
from typing import List
from uuid import uuid4

from .models import ActionDefinition, SequenceItem

# 项目根目录：src/core/storage.py -> parent 为 core，再 parent 为 src，再 parent 为仓库根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class StorageManager:
    """动作库与任务序列的持久化；路径相对项目根，可在 Windows/Linux 下使用。"""

    ACTIONS_FILE = _PROJECT_ROOT / "data" / "actions_library.json"
    TASKS_DIR = _PROJECT_ROOT / "data" / "tasks"

    @classmethod
    def ensure_directories(cls):
        cls.ACTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        cls.TASKS_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def save_actions(cls, actions: List[ActionDefinition]):
        cls.ensure_directories()
        data = [action.to_dict() for action in actions]
        with open(cls.ACTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load_actions(cls) -> List[ActionDefinition]:
        if not cls.ACTIONS_FILE.is_file():
            return []
        with open(cls.ACTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 自动补全缺失的 id（旧数据迁移）
        need_save = any(not item.get("id") for item in data)
        if need_save:
            for item in data:
                if not item.get("id"):
                    item["id"] = str(uuid4())
            with open(cls.ACTIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return [ActionDefinition.from_dict(item) for item in data]

    @classmethod
    def save_sequence(cls, items: List[SequenceItem], filename: str):
        cls.ensure_directories()
        # 仅使用文件名，避免路径穿越
        name = Path(filename).name
        filepath = cls.TASKS_DIR / name
        if filepath.suffix != ".task":
            filepath = filepath.with_suffix(".task")
        data = [item.to_dict() for item in items]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def load_sequence(cls, filename: str) -> List[SequenceItem]:
        name = Path(filename).name
        filepath = cls.TASKS_DIR / name
        if filepath.suffix != ".task":
            filepath = filepath.with_suffix(".task")
        if not filepath.is_file():
            return []
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [SequenceItem.from_dict(item) for item in data]

    @classmethod
    def list_tasks(cls) -> List[str]:
        if not cls.TASKS_DIR.is_dir():
            return []
        return [p.name for p in cls.TASKS_DIR.glob("*.task")]
