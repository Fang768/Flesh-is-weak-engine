"""Owner-bound body DIY parser. Does not use a model for adulthood or visibility."""
import re
from .privacy_bridge import ANATOMY

def body_intent(text):
 return bool((ANATOMY.search(text) or '身体设定' in text) and re.search(r'想|希望|设定|设置|修改|改成|公开|私密|隐藏|DIY|：|:',text))

def parse_body(text):
 if re.search(r'未成年|非成年|不是成年|并非成年|还没成年|未满18|不满18|(?<!\d)(?:1[0-7]|[0-9])岁',text):raise ValueError('仅支持明确成年的角色；未创建申请')
 d={}
 if re.search(r'(?:角色(?:已经|是|为)?(?:已成年|成年人|成年)|成年(?:的)?角色)',text):d['adult_status']='成年'
 if re.search(r'不公开|不要公开|改为私密|设为私密|保持私密|隐藏.*身体|身体.*隐藏',text):d['body_visibility']='私密'
 elif re.search(r'(?:公开|公布).{0,8}(?:身体|生殖器)|(?:身体|生殖器).{0,8}(?:设为公开|允许公开)',text):d['body_visibility']='公开'
 features=[]
 for clause in re.split(r'[，,。；;！!]',text):
  if not ANATOMY.search(clause):continue
  if re.fullmatch(r'.{0,12}(?:公开|公布|私密|隐藏).{0,10}(?:身体外观|身体特征|身体设定|生殖器外观)|(?:身体外观|身体特征|生殖器外观).{0,5}(?:公开|私密|隐藏)',clause):continue
  if not re.search(r'阳具|阴茎|生殖器|外阴|阴道|睾丸|乳房',clause):continue
  value=re.sub(r'^.*?(?:身体外观|身体特征|生殖器外观)\s*[：:]','',clause.strip())
  features.append(value)
 if features:d['body_features']='；'.join(features)
 if not d:raise ValueError('请说明具体的非色情身体外观，或说明是否公开；未创建申请')
 return d
