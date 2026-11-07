"""认领两步走 + 简易版/核心书卡互斥（按行定位）。"""
import io

NL = chr(10)
CF = "core/card_flow.py"
src = io.open(CF, encoding="utf-8").read()
lines = src.split(NL)


def find(prefix, start=0):
    for i in range(start, len(lines)):
        if lines[i].startswith(prefix):
            return i
    raise SystemExit("找不到 " + prefix)


PENDING = '''# 认领要先确认：模板名 → 待提交，5 分钟没确认就作废
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
'''

i = find("def _plain_command(")
lines[i:i] = PENDING.split(NL) + ["", ""]

RESET = repr(NL + NL)  # 源码里直接写成 '\\n\\n'

claim_src = '''def claim(group_id, user_id, want):
    """第一步：先给看卡，等「确认」再落库。"""
    want = (want or "").strip()
    if not want:
        return "用法：认领 火炬（先看「模板」）"
    tpl = easy.get(want)
    if tpl is None:
        return "没有「" + want + "」这张卡。可用：" + "、".join(easy.names())
    _set_pending(group_id, user_id, tpl["name"])
    return easy.format_card(tpl) + SEP + "要这张的话回「确认」；不满意就「换一个」，或者「取消」。"


def confirm_claim(group_id, user_id):
    """第二步：「确认」才真正落库。"""
    name = pending_claim(group_id, user_id)
    if not name:
        return None
    tpl = easy.get(name)
    if tpl is None:
        _drop_pending(group_id, user_id)
        return "「" + name + "」这张模板找不到了，重新说「模板」看看。"
    _drop_pending(group_id, user_id)
    card = easy_roster.save(CONN, group_id, user_id, tpl, name=None)
    return easy.format_card(card) + SEP + "想改名就说「把名字改成 XX」；「档案」随时看卡。"


def cancel_claim(group_id, user_id):
    if pending_claim(group_id, user_id) is None:
        return None
    _drop_pending(group_id, user_id)
    return "好，不领了。想要就说「模板」再看一遍。"


def swap_claim(group_id, user_id):
    if pending_claim(group_id, user_id) is None:
        return None
    _drop_pending(group_id, user_id)
    return templates_text()


'''.replace("SEP", RESET)

a = find("def claim(")
b = find("def sheet(", a)
lines[a:b] = claim_src.split(NL)

i = find("def _plain_command(")
anchor = None
for j in range(i, i + 20):
    if lines[j].strip() == "return None":
        anchor = j + 1
        break
if anchor is None:
    raise SystemExit("_plain_command 开头没找到")

insert = '''    if body in CONFIRM_WORDS:
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
'''.split(NL)
lines[anchor:anchor] = insert

i = find('    r"^提升",')
lines.insert(i + 1, '    r"^(?:确认|确定|领取|就它|换一个|换张|取消|算了|不要)",')

out = NL.join(lines)
io.open(CF, "w", encoding="utf-8", newline=NL).write(out)
compile(out, CF, "exec")
print("card_flow ok")
