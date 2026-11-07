"""Editable cosmetic references, deliberately separate from signed game templates."""
import json
from pathlib import Path
DEFAULTS=json.loads(Path(__file__).with_name('appearance_defaults.json').read_text())
LABELS={'age':'年龄参考','build':'体态','hair':'发色与发型','eyes':'瞳色','face':'面部印象','clothing':'日常穿着'}
assert set(DEFAULTS)=={f'{i:02d}' for i in range(1,11)}
assert all(set(v)==set(LABELS) and all(isinstance(x,str) and x for x in v.values()) for v in DEFAULTS.values())

def appearance_reference(cid,profile=None):
 profile=profile or {}
 if profile.get('appearance'):
  return dict(source='player_diy',appearance=profile['appearance'],unspecified='未说明的细节不替玩家补定；可另问外貌参考')
 return dict(source='optional_cosmetic_reference',details=dict(DEFAULTS[cid]),note='作者补充的可调整外貌参考，不是V3.2硬规则、隐藏事实或数值；未写入个人档案')

def appearance_text(cid,profile=None):
 ref=appearance_reference(cid,profile)
 if ref['source']=='player_diy':return '外观以本人DIY为准：'+ref['appearance']+'（未说明的部分暂不补定）'
 return '基础外貌参考（可调整，非固定规则）：'+'；'.join(LABELS[k]+'：'+v for k,v in ref['details'].items())
