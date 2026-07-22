# -*- coding: utf-8 -*-
"""极简天平读数：拍一张摄像头画面，交给视觉大模型读取数字。"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request

DEFAULT_PROVIDER = os.getenv("BALANCE_READER_PROVIDER", "qwen").strip().lower()

PROVIDERS = {
    "doubao": {
        "api_key": os.getenv(
            "VVEAI_API_KEY",
            "sk-NZx2AwzcJlwOnq9p307c548dF43a43Dd9bAf16F38f8cD026",
        ),
        "base_url": os.getenv("VVEAI_BASE_URL", "https://api.vveai.com/v1"),
        "model": os.getenv("VVEAI_MODEL", "doubao-seed-1-8-251228"),
    },
    "qwen": {
        "api_key": os.getenv(
            "QWEN_API_KEY",
            "sk-ws-H.EMEMLHX.aoox.MEQCIC3Ty11AA2ISPT5oIDYqgQIGDF6YRK2LqZ0RbbXKx2XqAiBtUrLKWiql72qZ5_JhCw2PZjWKryHaEfXCrXyzkupEHg",
        ),
        "base_url": os.getenv(
            "QWEN_BASE_URL",
            "https://llm-jsczn3ka5kx5ibia.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        ),
        "model": os.getenv("QWEN_MODEL", "qwen3.6-flash"),
    },
}


def get_provider_config(provider: str | None = None) -> dict:
    name = (provider or DEFAULT_PROVIDER).strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"未知 provider: {provider}; 可选: {', '.join(PROVIDERS)}")
    cfg = PROVIDERS[name].copy()
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    if not cfg["api_key"]:
        raise RuntimeError(f"{name} provider 缺少 API key")
    return cfg


def capture_image(camera_index: int = 12):
    import cv2

    cap = cv2.VideoCapture(camera_index)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"无法读取摄像头 index={camera_index}")
        return frame
    finally:
        cap.release()


def image_to_data_url(frame) -> str:
    import cv2

    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("图片编码失败")
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def ask_model(image_url: str, provider: str | None = None) -> str:
    cfg = get_provider_config(provider)

    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "读取图中天平显示的数值，只返回数字。"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 16,
    }

    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_value(text: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        raise RuntimeError(f"模型返回中没有数字: {text}")
    return float(match.group(0))


def read_balance(camera_index: int = 12, provider: str | None = None) -> float:
    frame = capture_image(camera_index)
    reply = ask_model(image_to_data_url(frame), provider=provider)
    return parse_value(reply)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="极简天平读数：摄像头拍照后交给视觉大模型读取数字")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER, help="模型供应商")
    parser.add_argument("--camera-index", type=int, default=12, help="OpenCV 摄像头 index")
    args = parser.parse_args()

    value = read_balance(camera_index=args.camera_index, provider=args.provider)
    print(value)
