"""义体中文条目的测试。"""

from __future__ import annotations

from cyberpunk import cyber


def test_all_entries_have_chinese():
    entries = cyber.all_entries()
    assert len(entries) >= 100
    missing = [e["en"] for e in entries if not e["zh"] or e["zh"] == e["en"]]
    assert missing == []


def test_search_by_chinese_category_word():
    hits = cyber.search("接驳器", limit=10)
    assert hits
    assert all("接驳器" in h["zh"] for h in hits)


def test_search_by_english_name():
    hit = cyber.get("Smartgun")
    assert hit is not None
    assert hit["zh"] == "智能枪链接"


def test_search_returns_notes():
    hits = cyber.search("痛觉", limit=3)
    assert hits and hits[0]["note"]


def test_format_entry_single_line():
    entry = cyber.get("Pain Editor")
    line = cyber.format_entry(entry)
    assert line.startswith("🦾")
    assert "\n" not in line


def test_markdown_table_builds():
    table = cyber.markdown_table()
    assert table.startswith("| 中文名 |")
    assert table.count("\n") >= len(cyber.all_entries())
