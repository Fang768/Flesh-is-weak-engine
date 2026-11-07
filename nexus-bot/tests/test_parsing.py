"""命令参数解析的单元测试（不启动 NoneBot）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyberpunk import (  # noqa: E402
    ScriptedRoller,
    parse_check_args,
    parse_notation,
    roll_verbose,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2d6", (2, 6, 0)),
        ("1d10", (1, 10, 0)),
        ("3d6-2", (3, 6, -2)),
        (" 2D6+1 ", (2, 6, 1)),
        ("1d100", (1, 100, 0)),
    ],
)
def test_parse_notation_ok(text, expected):
    assert parse_notation(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "21d6", "2d7", "d6", "2d6+"])
def test_parse_notation_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_notation(text)


def test_roll_verbose_shows_each_die():
    text = roll_verbose(ScriptedRoller([3, 5]), 2, 6, 1)
    assert "3 + 5" in text and "**9**" in text


def test_parse_check_args_with_modifiers():
    skill, dv, mods = parse_check_args("手枪 dv=14 mod=-3")
    assert skill == "手枪"
    assert dv == 14
    assert mods == [-3]


def test_parse_check_args_defaults_to_everyday():
    skill, dv, mods = parse_check_args("潜行")
    assert skill == "潜行"
    assert dv == 14
    assert mods == []


def test_parse_check_args_strips_keywords_from_skill_name():
    skill, dv, _ = parse_check_args("电子/安防技术 dv=22")
    assert skill == "电子/安防技术"
    assert dv == 22
