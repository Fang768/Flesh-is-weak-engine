"""Non-erotic adult anatomy is cosmetic data, private unless its owner publishes it."""
import re
from .store import SaveError
BODY_FIELDS={'body_features','adult_status','body_visibility'}
ANATOMY=re.compile(r'阳具|阴茎|生殖器|外阴|阴道|睾丸|乳房|身体外观|身体特征')
EXPLICIT=re.compile(r'性交|做爱|口交|肛交|手淫|自慰|勃起|射精|性兴奋|性快感|色情|淫荡|插入|性奴|催情|强奸|未成年|幼女|幼男|萝莉|正太')
FUNCTION=re.compile(r'增强|增益|加成|提升|提高|额外|功能|武器|攻击|战斗|电击|发射|植入|义体|防弹|数值|敏捷|力量|魅力|耐力|生育能力|自动|变形|伸缩|更持久|超人|抗性')
def validate_body(diy):
 if not set(diy)&BODY_FIELDS:return
 if 'adult_status' in diy and diy['adult_status']!='成年':raise SaveError('身体DIY仅适用于明确成年的角色')
 if 'body_visibility' in diy and diy['body_visibility'] not in ('私密','公开'):raise SaveError('身体外观公开范围无效')
 if 'body_features' in diy:
  if diy.get('adult_status')!='成年':raise SaveError('请先明确说明角色已成年；未保存身体设定')
  text=diy['body_features']
  if not isinstance(text,str):raise SaveError('身体外观必须是普通文字')
  if EXPLICIT.search(text):raise SaveError('只支持成年角色的非色情外观，不处理露骨性行为或色情描述')
  mechanical=re.sub(r'不影响数值|不改变数值|无功能|没有功能|不提供功能|仅影响外观','',text)
  if FUNCTION.search(mechanical):raise SaveError('身体DIY只能改变外观；有功能的义体或数值变化须走原规则流程')
def public_diy(profile):
 """Never pass private anatomy or age attestation to public cards/model contexts."""
 return {k:v for k,v in profile.items() if k not in BODY_FIELDS or k=='body_features' and profile.get('body_visibility')=='公开'}
def body_risk(text):
 remainder=text
 for word in sorted(('成年角色','生殖器','阳具','阴茎','外阴','阴道','睾丸','乳房','身体外观','身体特征','非色情','仅影响外观','不影响数值','有','无','没有','外观','自然','普通','皮肤','肤色','棕色','深色','浅色','黑色','粉色','疤痕','设为','设置','希望','想要','想','的','是','我','他','她','身体'),key=len,reverse=True):remainder=remainder.replace(word,'')
 return bool(re.sub(r'[\s，,。；;：:、]+','',remainder))
