"""认领两步走 + 只保留一张卡（简易版与核心书互斥）。"""
import io

CF = "core/card_flow.py"
src = io.open(CF, encoding="utf-8").read()
NL = chr(10)

# ── 1. 待确认状态 ───────────────────────────────────────────────
pending_block = '''
# 认领要先确认：模板名 → 待提交，5 分钟没确认就作废
_CLAIM_PENDING: dict[tuple[str, str], tuple[str, float]] = {}
CLAIM_TTL = 300.0
CONFIRM_WORDS = ("确认", "确定", "领取", "就它", "就要这个", "好")
CANCEL_WORDS = ("取消", "算了", "不要", "不领", "先不要")
SWAP_WORDS = ("换一个", "换张", "换个", "别的", "其他")


def _pending_key(group_id: str, user_id: str) -> tuple[str, str]:
    return (str(group_id), str(user_id))


def pending_claim(group_id: str, user_id: str) -> str | None:
    """有没有还没确认的认领？过期就清掉。"""
    import time

    item = _CLAIM_PENDING.get(_pending_key(group_id, user_id))
    if not item:
        return None
    name, ts = item
    if time.monotonic() - ts > CLAIM_TTL:
        _CLAIM_PENDING.pop(_pending_key(group_id, user_id), None)
        return None
    return name


def _set_pending(group_id: str, user_id: str, name: str) -> None:
    import time

    _CLAIM_PENDING[_pending_key(group_id, user_id)] = (name, time.monotonic())


def _drop_pending(group_id: str, user_id: str) -> None:
    _CLAIM_PENDING.pop(_pending_key(group_id, user_id), None)


'''.replace(chr(10), NL)

src = src.replace(NL + NL + "def _plain_command(", pending_block + NL + "def _plain_command(", 1)

# ── 2. claim 改成两步 ──────────────────────────────────────────
old_claim = '''def claim(group_id: str, user_id: str, want: str) -> str:
    want = (want or "").strip()
    if not want:
        return "用法：认领 火炬（先看「模板」）"
    tpl = easy.get(want)
    if tpl is None:
        return f"没有「{want}」这张卡。可用：{'、'.join(easy.names())}"
    card = easy_roster.save(CONN, group_id, user_id, tpl, name=None)
    return easy.format_card(card) + NL + NL + "想改名就说「把名字改成 XX」。"'''
old_claim = old_claim.replace("NL", NL_S := "\\n")  # 保持与源码一致的字面量

new_claim = '''def claim(group_id: str, user_id: str, want: str) -> str:
    """第一步：先给看卡，等「确认」再落库。"""
    want = (want or "").strip()
    if not want:
        return "用法：认领 火炬（先看「模板」）"
    tpl = easy.get(want)
    if tpl is None:
        return f"没有「{want}」这张卡。可用：{'、'.join(easy.names())}"
    _set_pending(group_id, user_id, tpl["name"])
    preview = easy.render(tpl) if hasattr(easy, "render") else easy.format_card(tpl)
    return (
        preview
        + NL + NL
        + "要这张的话回「确认」；不满意就「换一个」，或者「取消」。"
    )


def confirm_claim(group_id: str, user_id: str) -> str | None:
    """第二步：「确认」才真正落库。"""
    name = pending_claim(group_id, user_id)
    if not name:
        return None
    tpl = easy.get(name)
    if tpl is None:
        _drop_pending(group_id, user_id)
        return f"「{name}」这张模板找不到了，重新说「模板」看看。"
    _drop_pending(group_id, user_id)
    card = easy_roster.save(CONN, group_id, user_id, tpl, name=None)
    return easy.format_card(card) + NL + NL + "想改名就说「把名字改成 XX」；「档案」随时看卡。"


def cancel_claim(group_id: str, user_id: str) -> str | None:
    if pending_claim(group_id, user_id) is None:
        return None
    _drop_pending(group_id, user_id)
    return "好，不领了。想要就说「模板」再看一遍。"


def swap_claim(group_id: str, user_id: str) -> str | None:
    if pending_claim(group_id, user_id) is None:
        return None
    _drop_pending(group_id, user_id)
    return templates_text()'''
new_claim = new_claim.replace("NL", NL)

if old_claim in src:
    src = src.replace(old_claim, new_claim, 1)
    print("claim() 已改为两步")
else:
    raise SystemExit("claim() 原文没匹配上")

# ── 3. _plain_command 里挂上确认/取消/换一个 ───────────────────
anchor = '''    body = text.strip()
    if body.startswith("/"):
        body = body[1:].strip()
    if not body:
        return None
'''
add = anchor + '''
    if body in CONFIRM_WORDS:
        hit = confirm_claim(group_id, user_id)
        if hit is not None:
            return hit
    if body in CANCEL_WORDS:
        hit = cancel_claim(group_id, user_id)
        if hit is not None:
            return hit
    if body in SWAP_WORDS:
        hit = swap_claim(group_id, user_id)
        if hit is not None:
            return hit
'''
if anchor not in src:
    raise SystemExit("_plain_command 开头没匹配上")
src = src.replace(anchor, add, 1)

# ── 4. META_PATTERNS 放行确认词 ───────────────────────────────
src = src.replace(
    '    r"^提升",',
    '    r"^提升",' + NL + '    r"^(?:确认|确定|领取|就它|换一个|换张|取消|算了|不要)",',
    1,
)

io.open(CF, "w", encoding="utf-8", newline=NL).write(src)
compile(src, CF, "exec")
print("card_flow 补丁完成")
