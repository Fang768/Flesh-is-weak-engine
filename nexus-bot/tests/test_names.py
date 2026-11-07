"""中英术语表：中文/英文/片段都要能解析到同一项技能。"""

from __future__ import annotations

from cyberpunk import names


def test_all_skills_loaded():
    assert len(names.skills()) == 95
    assert len({s["cat"] for s in names.skills()}) == 9


def test_stats_zh():
    assert names.stats_en_to_zh()["REF"] == "反应"
    assert names.stats_en_to_zh()["BODY"] == "体格"


def test_en_to_zh():
    assert names.to_zh("Handgun") == "手枪"
    assert names.to_zh("Brawling/Hand to Hand") == "徒手搏斗"
    assert names.to_zh("handgun") == "手枪"


def test_zh_passthrough():
    assert names.to_zh("手枪") == "手枪"
    assert names.to_en("手枪") == "Handgun"


def test_resolve_english_and_chinese():
    assert names.resolve_skill("Handgun") == "手枪"
    assert names.resolve_skill("手枪") == "手枪"
    assert names.resolve_skill("潜行") == "潜行"


def test_resolve_ambiguous_fragment_rejected():
    # 「驾驶」对应多个 Pilot/Driving，不能瞎猜
    assert names.resolve_skill("驾驶") is None


def test_resolve_unknown_rejected():
    assert names.resolve_skill("开飞机") is None
    assert names.resolve_skill("") is None


def test_stat_of_skill():
    assert names.stat_of("手枪") == "REF"
    assert names.stat_of("潜行") == "DEX"
    assert names.stat_of("医疗技术") == "TECH"


def test_display():
    assert names.display("Handgun") == "手枪（Handgun）"
    assert names.display("手枪") == "手枪（Handgun）"


def test_markdown_table_uses_real_newlines():
    table = names.markdown_table()
    for cat_zh in ("格斗", "远程", "觉察", "操控", "体能", "交际", "技艺", "表演", "学识"):
        assert cat_zh in table
    assert "\\n" not in table
    assert table.count("\n") > 100


def test_normalize_template_skill():
    assert names.normalize_template_skill("Aw areness") == "觉察"
    assert names.normalize_template_skill("Automatic We apons") == "冲锋枪"
    assert names.normalize_template_skill("A rc h e ry or Handgun (choose)") == "弓术"
    assert names.normalize_template_skill("Karate") == "武术"
    assert names.normalize_template_skill("Subjock") == "驾驶（潜水器）"
    assert names.normalize_template_skill("Handgun") == "手枪"
    # 认不出来就原样返回，不抛异常
    assert names.normalize_template_skill("Mystery Skill") == "Mystery Skill"


def test_normalize_covers_every_template_skill():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "rules" / "cp_v3" / "data" / "templates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = {s["skill"] for tpl in data for s in tpl.get("skills", [])}
    assert len(raw) == 39
    zh = {names.normalize_template_skill(r) for r in raw}
    # 归一后必须全部能在术语表里查到关联属性
    unknown = sorted(x for x in zh if names.stat_of(x) is None)
    assert unknown == []


def test_glossary_terms():
    assert names.term("DV") == "难度值"
    assert names.term("不存在") == "不存在"


def test_cool_is_zhending():
    """2077 简中把 COOL 译作「镇定」；「酷」仍可作别名输入。"""
    assert names.stats_en_to_zh()["COOL"] == "镇定"
    assert names.stat_code("镇定") == "COOL"
    assert names.stat_code("酷") == "COOL"
    assert names.stat_code("身体") == "BODY"
