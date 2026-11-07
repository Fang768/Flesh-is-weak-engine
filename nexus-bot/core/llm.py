"""DeepSeek 调用层：只负责「把自然语言变成结构化 JSON」，不负责裁定。

三条硬规矩：

* 只返回 dict，模型说废话就抛 ``LLMError``
* 不在这里做规则判断——数值一律交给 ``cyberpunk/`` 和 ``core/roster`` 校验
* 每次调用都记账（``LAST_USAGE``），方便看 token 花在哪
"""

from __future__ import annotations

import json
import os as _os

_os.environ.setdefault("NO_PROXY", "*")
_os.environ.setdefault("no_proxy", "*")
import os
from pathlib import Path

import httpx

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
ROOT = Path(__file__).resolve().parents[1]  # nexus-bot/

LAST_USAGE: dict[str, int] = {}


class LLMError(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise LLMError("没找到 DEEPSEEK_API_KEY（.env 或环境变量）")


async def complete_text(
    system: str,
    user: str,
    *,
    timeout: float = 45.0,
    max_tokens: int = 400,
    temperature: float = 0.8,
) -> str:
    """要一段人话（NPC 台词、旁白），不要 JSON。"""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(API_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"连不上 DeepSeek：{exc}") from exc

    if resp.status_code != 200:
        raise LLMError(f"DeepSeek 返回 {resp.status_code}：{resp.text[:180]}")

    data = resp.json()
    usage = data.get("usage") or {}
    LAST_USAGE.clear()
    LAST_USAGE.update(
        {
            "prompt": int(usage.get("prompt_tokens", 0)),
            "completion": int(usage.get("completion_tokens", 0)),
        }
    )
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError("DeepSeek 返回结构异常") from exc
    return (content or "").strip()


async def complete_json(
    system: str,
    user: str,
    *,
    timeout: float = 45.0,
    max_tokens: int = 900,
    temperature: float = 0.3,
) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(API_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"连不上 DeepSeek：{exc}") from exc

    if resp.status_code != 200:
        raise LLMError(f"DeepSeek 返回 {resp.status_code}：{resp.text[:180]}")

    data = resp.json()
    usage = data.get("usage") or {}
    LAST_USAGE.clear()
    LAST_USAGE.update(
        {
            "prompt": int(usage.get("prompt_tokens", 0)),
            "completion": int(usage.get("completion_tokens", 0)),
        }
    )

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError("DeepSeek 返回结构异常") from exc

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型没吐 JSON：{content[:160]}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("模型返回的不是 JSON 对象")
    return parsed
