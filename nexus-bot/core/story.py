"""剧本：把 JSON 读成可查询的场景图。

结构（见 `story/demo.json`）：

    {id, title, premise, acts:[{id,title,goal,scenes:[{id,name,text,npcs,clues,gates,exits,ending}]}],
     npcs:{key:{name,voice}}, endings:{key:text}}

设计约定：
    * `campaigns.scene` 存的是**场景 id**（如 `act1.dock`），显示时用 :func:`scene_name` 转中文名
    * 场景推进只能走 `gates[*].next` 或 `exits`，不允许 LLM 随便编场景
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

STORY_DIR = Path(__file__).resolve().parent.parent / "story"
DEFAULT_ID = "demo"


class StoryError(ValueError):
    pass


@lru_cache(maxsize=8)
def load(story_id: str = DEFAULT_ID) -> dict:
    path = STORY_DIR / f"{story_id}.json"
    if not path.exists():
        raise StoryError(f"没有剧本 {story_id}（{path}）")
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    # 扁平化场景表，方便按 id 取
    scenes: dict[str, dict] = {}
    for act in data.get("acts", []):
        for scene in act.get("scenes", []):
            scene = dict(scene)
            scene.setdefault("act", act["id"])
            scene.setdefault("act_title", act.get("title", ""))
            scene["gates"] = list(scene.get("gates") or [])
            scene["clues"] = list(scene.get("clues") or [])
            scenes[scene["id"]] = scene
    data["scenes"] = scenes
    data.setdefault("npcs", {})
    data.setdefault("endings", {})
    data.setdefault("acts", [])
    return data


def list_stories() -> list[str]:
    return sorted(p.stem for p in STORY_DIR.glob("*.json"))


def first_scene(story_id: str = DEFAULT_ID) -> str:
    data = load(story_id)
    for act in data.get("acts", []):
        for scene in act.get("scenes", []):
            return scene["id"]
    raise StoryError("剧本里没有任何场景")


def scene(story_id: str, scene_id: str) -> dict | None:
    return load(story_id).get("scenes", {}).get(scene_id)


def scene_name(story_id: str, scene_id: str) -> str:
    sc = scene(story_id, scene_id)
    return sc["name"] if sc else (scene_id or "未知地点")


def gate_for(story_id: str, scene_id: str, skill: str) -> dict | None:
    """在场景的闸门里找匹配技能的一道（技能名完全相等才算）。"""
    sc = scene(story_id, scene_id)
    if not sc:
        return None
    for gate in sc.get("gates") or []:
        if gate.get("skill") == skill:
            return gate
    return None


def best_gate(story_id: str, scene_id: str, skills: list[str]) -> dict | None:
    """给一串候选技能，返回第一道能匹配的闸门（用于模型没给准技能时兜底）。"""
    for skill in skills:
        gate = gate_for(story_id, scene_id, skill)
        if gate:
            return gate
    return None


def next_scene(gate: dict, success: bool) -> str | None:
    return gate.get("next") if success else gate.get("fail")


def ending_text(story_id: str, key: str) -> str:
    return load(story_id).get("endings", {}).get(key, "")


def npc_line(story_id: str, key: str) -> str:
    npc = load(story_id).get("npcs", {}).get(key)
    return f"{npc['name']}（{npc['voice']}）" if npc else key


def match_scenario(text: str) -> str | None:
    """把玩家起的名字对到剧本：先比 id，再比标题，最后比标题片段。"""
    body = (text or "").strip()
    if not body:
        return None
    for sid in list_stories():
        if body == sid or body.lower() == sid.lower():
            return sid
    for sid in list_stories():
        try:
            title = load(sid).get("title", "")
        except StoryError:
            continue
        if title and (body == title or title in body or body in title):
            return sid
    return None
