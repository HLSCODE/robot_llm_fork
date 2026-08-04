# -*- coding: utf-8 -*-
"""极简天平读数：拍一张摄像头画面，交给视觉大模型读取数字。"""

from __future__ import annotations

import base64
import json
import re
import urllib.request


def capture_image(camera_index: int):
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


def ask_model(
    image_url: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
) -> str:
    if not api_key:
        raise RuntimeError("请先设置环境变量 VVEAI_API_KEY")

    payload = {
        "model": model,
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
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_value(text: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        raise RuntimeError(f"模型返回中没有数字: {text}")
    return float(match.group(0))


def read_balance(
    *,
    camera_index: int,
    api_key: str,
    base_url: str,
    model: str,
    timeout_seconds: float,
) -> float:
    frame = capture_image(camera_index)
    reply = ask_model(
        image_to_data_url(frame),
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
    )
    return parse_value(reply)


if __name__ == "__main__":
    from ..configuration.config_loader import load_application_settings

    settings = load_application_settings()
    value = read_balance(
        camera_index=settings.vision.balance_camera_index,
        api_key=settings.secrets.vveai_api_key,
        base_url=settings.vision.vveai_base_url,
        model=settings.vision.vveai_model,
        timeout_seconds=settings.vision.balance_request_timeout_seconds,
    )
    print(value)
