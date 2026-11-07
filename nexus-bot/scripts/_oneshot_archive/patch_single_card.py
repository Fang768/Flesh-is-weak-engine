"""简易版卡与核心书卡互斥：存一张就删另一张，避免「档案」显示旧卡。"""
import io

NL = chr(10)


def patch(path, anchor, guard_marker, snippet):
    src = io.open(path, encoding="utf-8").read()
    if guard_marker in src:
        print(path, "already patched")
        return
    if anchor not in src:
        raise SystemExit(path + " 找不到锚点")
    src = src.replace(anchor, snippet + anchor, 1)
    io.open(path, "w", encoding="utf-8", newline=NL).write(src)
    compile(src, path, "exec")
    print(path, "ok")


# easy_roster.save 开头：删掉核心书的卡
patch(
    "core/easy_roster.py",
    'def save(conn: sqlite3.Connection, group_id: str, owner_qq: str,',
    "_drop_reason",
    '''def _drop_red_card(conn, group_id: str, owner_qq: str) -> None:
    """同一个玩家只留一张卡：领了简易版，就撤掉核心书那张。"""
    conn.execute(
        "DELETE FROM characters WHERE group_id = ? AND owner_qq = ?",
        (str(group_id), str(owner_qq)),
    )
    conn.commit()


''',
)

# roster.save 开头：删掉简易版的卡
patch(
    "core/roster.py",
    "def save(conn, group_id: str, owner_qq: str, character: Character) -> None:",
    "_drop_easy_card",
    '''def _drop_easy_card(conn, group_id: str, owner_qq: str) -> None:
    """同一个玩家只留一张卡：建了核心书卡，就撤掉简易版那张。"""
    try:
        conn.execute(
            "DELETE FROM easy_cards WHERE group_id = ? AND owner_qq = ?",
            (str(group_id), str(owner_qq)),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


''',
)
