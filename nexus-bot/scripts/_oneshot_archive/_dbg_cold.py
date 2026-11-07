"""手动复现 cold 场景第三步，看 gate_flow 到底返回什么。"""
import sys

sys.path.insert(0, ".")

from scripts import simulate as S  # noqa: E402

S.boot()
from core import card_flow, gate_flow  # noqa: E402

GID = "qq:test"
print("1:", gate_flow.handle(group_id=GID, user_id="u1", text="开团",
                            is_addressed=True, is_operator=True))
print("2:", gate_flow.handle(group_id=GID, user_id="u1", text="取消",
                            is_addressed=True, is_operator=True))
print("3:", gate_flow.handle(group_id=GID, user_id="u1", text="开团",
                            is_addressed=True, is_operator=True))
w = gate_flow.ATTENTION.get(GID)
print("stage:", getattr(w, "stage", None), "init:", getattr(w, "initiator", None))
