"""认领两步走 + 简易版/核心书卡互斥（按行定位，避免引号纠缠）。"""
import io

NL = chr(10)
CF = "core/card_flow.py"
src = io.open(CF, encoding="utf-8").read()
lines = src.split(NL)


def find(prefix, start=0):
    for i in range(start, len(lines)):
        if lines[i].startswith(prefix):
            return i
    raise SystemExit(f"找不到 {prefix!r}")


# ── 1. 待确认状态：插在 def _plain_command( 之前 ────────────────
block = '''# 认领要先确认：模板名 → 待提交，5 分钟没确认就作废
_CLAIM_PENDING = {}
CLAIM_TTL = 300.0
CONFIRM_WORDS = ("确认", "确定", "领取", "就它", "就要这个", "好")
CANCEL_WORDS = ("取消", "算了", "不要", "不领", "先不要")
SWAP_WORDS = ("换一个", "换张", "换个", "别的", "其他")


def pending_claim(group_id, user_id):
    """有没有还没确认的认领？过期就清掉。"""
    import time

    key = (str(group_id), str(user_id))
    item = _CLAIM_PENDING.get(key)
    if not item:
        return None
    name, ts = item
    if time.monotonic() - ts > CLAIM_TTL:
        _CLAIM_PENDING.pop(key, None)
        return None
    return name


def _set_pending(group_id, user_id, name):
    import time

    _CLAIM_PENDING[(str(group_id), str(user_id))] = (name, time.monotonic())


def _drop_pending(group_id, user_id):
    _CLAIM_PENDING.pop((str(group_id), str(user_id)), None)


'''.split(NL)

i = find("def _plain_command(")
lines[i:i] = block

# ── 2. claim() 改成两步：整段替换到 def sheet( ──────────────────
a = find("def claim(")
b = find("def sheet(", a)
claim_block = '''def claim(group_id, user_id, want):
    """第一步：先给看卡，等「确认」再落库。"""
    want = (want or "").strip()
    if not want:
        return "用法：认领 火炬（先看「模板」）"
    tpl = easy.get(want)
    if tpl is None:
        return f"没有「{want}」这张卡。可用：{'、'.join(easy.names())}"
    _set_pending(group_id, user_id, tpl["name"])
    return (
        easy.format_card(tpl)
        + "''' + NL_ESC_PLACEHOLDER + "'''"
    )
'''.split(NL)
# 上面占位标记稍后替换成真正的换行转义
claim_block = [
    'def claim(group_id, user_id, want):',
    '    """第一步：先给看卡，等「确认」再落库。"""',
    '    want = (want or "").strip()',
    '    if not want:',
    '        return "用法：认领 火炬（先看「模板」）"',
    '    tpl = easy.get(want)',
    '    if tpl is None:',
    '        return f"没有「{want}」这张卡。可用：{\'、\'.join(easy.names())}"',
    '    _set_pending(group_id, user_id, tpl["name"])',
    '    tail = "要这张的话回「确认」；不满意就「换一个」，或者「取消」。"',
    '    return easy.format_card(tpl) + SEP + tail',
    '',
    '',
    'def confirm_claim(group_id, user_id):',
    '    """第二步：「确认」才真正落库。"""',
    '    name = pending_claim(group_id, user_id)',
    '    if not name:',
    '        return None',
    '    tpl = easy.get(name)',
    '    if tpl is None:',
    '        _drop_pending(group_id, user_id)',
    '        return f"「{name}」这张模板找不到了，重新说「模板」看看。"',
    '    _drop_pending(group_id, user_id)',
    '    card = easy_roster.save(CONN, group_id, user_id, tpl, name=None)',
    '    return easy.format_card(card) + SEP + "想改名就说「把名字改成 XX」；「档案」随时看卡。"',
    '',
    '',
    'def cancel_claim(group_id, user_id):',
    '    if pending_claim(group_id, user_id) is None:',
    '        return None',
    '    _drop_pending(group_id, user_id)',
    '    return "好，不领了。想要就说「模板」再看一遍。"',
    '',
    '',
    'def swap_claim(group_id, user_id):',
    '    if pending_claim(group_id, user_id) is None:',
    '        return None',
    '    _drop_pending(group_id, user_id)',
    '    return templates_text()',
    '',
    '',
]
lines[a:b] = claim_block

# ── 3. _plain_command 里挂确认/取消/换一个 ─────────────────────
i = find("def _plain_command(")
anchor = i + 6  # if not body: / return None 之后
for j in range(i, i + 14):
    if lines[j].strip() == "return None":
        anchor = j + 1
        break
insert = [
    '    if body in CONFIRM_WORDS:',
    '        hit = confirm_claim(group_id, user_id)',
    '        if hit is not None:',
    '            return hit',
    '    if body in CANCEL_WORDS:',
    '        hit = cancel_claim(group_id, user_id)',
    '        if hit is not None:',
    '            return hit',
    '    if body in SWAP_WORDS:',
    '        hit = swap_claim(group_id, user_id)',
    '        if hit is not None:',
    '            return hit',
]
lines[anchor:anchor] = insert

# ── 4. META 放行确认词 ─────────────────────────────────────────
i = find('    r"^提升",')
lines.insert(i + 1, '    r"^(?:确认|确定|领取|就它|换一个|换张|取消|算了|不要)",')

out = NL.join(lines)
out = out.replace("SEP", repr(NL + NL))
io.open(CF, "w", encoding="utf-8", newline=NL).write(out)
compile(out, CF, "exec")
print("card_flow ok")
