"""简易版卡与核心书卡互斥：存一张就删另一张。"""
import io

NL = chr(10)


def patch_save(path, def_line, insert_lines, marker):
    src = io.open(path, encoding="utf-8").read()
    if marker in src:
        print(path, "already patched")
        return
    lines = src.split(NL)
    idx = next(i for i, l in enumerate(lines) if l.startswith(def_line))
    # 跳过可能的多行签名，找到以 ":" 结尾且不在括号里的那行
    j = idx
    depth = 0
    while j < len(lines):
        depth += lines[j].count("(") - lines[j].count(")")
        if depth <= 0 and lines[j].rstrip().endswith(":"):
            break
        j += 1
    # 跳过紧跟的 docstring
    k = j + 1
    if k < len(lines) and lines[k].lstrip().startswith('"""'):
        if lines[k].count('"""') >= 2:
            k += 1
        else:
            k += 1
            while k < len(lines) and '"""' not in lines[k]:
                k += 1
            k += 1
    lines[k:k] = insert_lines
    out = NL.join(lines)
    io.open(path, "w", encoding="utf-8", newline=NL).write(out)
    compile(out, path, "exec")
    print(path, "ok")


patch_save(
    "core/easy_roster.py",
    "def save(conn: sqlite3.Connection, group_id: str, owner_qq: str,",
    [
        "    # 一个玩家只留一张卡：领了简易版，核心书那张就不算数了",
        "    conn.execute(",
        '        "DELETE FROM characters WHERE group_id = ? AND owner_qq = ?",',
        "        (str(group_id), str(owner_qq)),",
        "    )",
    ],
    "领了简易版，核心书那张就不算数了",
)

patch_save(
    "core/roster.py",
    "def save(conn, group_id: str, owner_qq: str, character: Character) -> None:",
    [
        "    # 一个玩家只留一张卡：建了核心书卡，简易版那张就不算数了",
        "    try:",
        "        conn.execute(",
        '            "DELETE FROM easy_cards WHERE group_id = ? AND owner_qq = ?",',
        "            (str(group_id), str(owner_qq)),",
        "        )",
        "    except sqlite3.OperationalError:",
        "        pass",
    ],
    "建了核心书卡，简易版那张就不算数了",
)
