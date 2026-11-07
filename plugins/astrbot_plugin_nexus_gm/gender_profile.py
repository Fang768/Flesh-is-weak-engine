"""Explicit self-character gender is cosmetic; never infer anatomy or consent."""
import re
GENDERS=('女性','男性','非二元')
def gender_change(text):
 if re.search(r'吗|是否|是不是|能不能|可不可以|如果|假如|听说|他说|她说|替他|替她|代替|不要|不想|不是|别改|[?？]',text):return None
 matches=re.findall(r'(?:性别\s*(?:设为|设置为|改为|改成|调整为|是|为)|(?:变成|改成|改为|设为|设置为))\s*(女性|男性|非二元)',text)
 if not matches:return None
 if len(set(matches))!=1:return None
 return matches[0]
