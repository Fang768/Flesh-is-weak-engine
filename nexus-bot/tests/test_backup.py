"""存档导出/恢复的测试：roundtrip、坏文件、覆盖语义。"""

from __future__ import annotations

import json

import pytest

from core import backup, db, roster, easy_roster


@pytest.fixture()
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def _card(**kw):
    card = roster.build(template="独狼", name="测试员", concept="跑团测试")
    for k, v in kw.items():
        card[k] = v
    return card


def _seed(conn, gid="qq:g1"):
    card = _card()
    roster.save(conn, gid, "u1", card)
    from cyberpunk import easy as easy_rules

    easy_roster.save(conn, gid, "u2", easy_rules.templates()[0])
    conn.execute(
        "INSERT INTO campaigns (id, title, status, scene, scenario, created_at, updated_at)"
        " VALUES (7, '测试主线', 'active', 'act1.dock', 'demo', 'now', 'now')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO group_sessions (group_id, campaign_id, mode, updated_at)"
        " VALUES (?, 7, 'active', 'now')",
        (gid,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO clues_found (group_id, campaign_id, clue_id, found_by, created_at)"
        " VALUES (?, 7, 'brake', 'u1', 'now')",
        (gid,),
    )
    conn.commit()
    return card


def test_export_shape(conn, tmp_path):
    _seed(conn)
    data = backup.export_group(conn, "qq:g1")
    assert data["schema"] == backup.SCHEMA
    assert data["campaign"]["title"] == "测试主线"
    assert len(data["characters"]) == 1
    assert len(data["easy_cards"]) == 1
    assert len(data["clues_found"]) == 1
    assert "主线《测试主线》" in backup.counts(data)


def test_save_and_load_roundtrip(conn, tmp_path):
    _seed(conn)
    path = backup.save(conn, "qq:g1", out_dir=tmp_path)
    assert path.exists()
    data = backup.load(path)
    assert data["group_id"] == "qq:g1"
    assert backup.list_backups(tmp_path)[0].name == path.name


def test_restore_into_clean_db(conn, tmp_path):
    _seed(conn)
    path = backup.save(conn, "qq:g1", out_dir=tmp_path)
    data = backup.load(path)

    other = db.connect(":memory:")
    backup.restore(other, data, group_id="qq:g2")
    got = roster.get(other, "qq:g2", "u1")
    assert got is not None and got["name"] == "测试员"
    row = other.execute(
        "SELECT mode, campaign_id FROM group_sessions WHERE group_id = 'qq:g2'"
    ).fetchone()
    assert (row["mode"], row["campaign_id"]) == ("active", 7)
    clues = other.execute(
        "SELECT clue_id FROM clues_found WHERE group_id = 'qq:g2'"
    ).fetchall()
    assert [r["clue_id"] for r in clues] == ["brake"]
    other.close()


def test_restore_replaces_existing(conn, tmp_path):
    _seed(conn)
    path = backup.save(conn, "qq:g1", out_dir=tmp_path)
    data = backup.load(path)

    # 备份之后又改了卡：恢复应把它覆盖回去
    changed = roster.get(conn, "qq:g1", "u1")
    changed["name"] = "被改过的名字"
    roster.save(conn, "qq:g1", "u1", changed)
    assert roster.get(conn, "qq:g1", "u1")["name"] == "被改过的名字"

    backup.restore(conn, data, group_id="qq:g1")
    assert roster.get(conn, "qq:g1", "u1")["name"] == "测试员"


def test_load_rejects_bad_files(conn, tmp_path):
    with pytest.raises(backup.BackupError):
        backup.load(tmp_path / "不存在.json")

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(backup.BackupError):
        backup.load(bad)

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema": 0}), encoding="utf-8")
    with pytest.raises(backup.BackupError):
        backup.load(old)


def test_restore_requires_group(conn):
    with pytest.raises(backup.BackupError):
        backup.restore(conn, {"schema": backup.SCHEMA})


def test_list_backups_empty(tmp_path):
    assert backup.list_backups(tmp_path) == []
