"""Narrow enrollment authorization; no LLM tool and no generic auto-approve switch.
All preview, consent, policy checks, commit and delivery receipts use the same DB.
"""
import copy,hashlib,json,re,secrets,time,unicodedata
from pathlib import Path
from .store import SaveError,canonical,utcnow,validate_snapshot

from .body_privacy import BODY_FIELDS,ANATOMY,validate_body,body_risk

FIELDS=BODY_FIELDS|{'gender','appearance','personality','speech','hobbies','story','display_name'}
# The automatic lane accepts known cosmetic phrases; unknown text requires a human.
WORDS={
 'appearance':'橙色护目镜|黑色护目镜|红色护目镜|护目镜|橙色|黑色|白色|红色|蓝色|银色|绿色|紫色|粉色|金色|灰色|短发|长发|卷发|直发|马尾|寸头|头发|发型|发色|工装|夹克|风衣|卫衣|衬衫|帽子|手套|靴子|运动鞋|眼镜|穿着|穿|戴着|戴|留着|留|喜欢|风格|朴素|整洁|随性|复古|朋克',
 'personality':'毒舌|内向|外向|谨慎|乐观|幽默|沉默|直率|温和|耐心|认真|话多|慢热|好奇|热心|冷静|害羞|随和|固执|活泼|平时|有点|比较|性格|但|也|很',
 'speech':'话少|轻声细语|语速慢|语速快|慢条斯理|喜欢开玩笑|喜欢冷笑话|口头禅|说话|习惯|说|行吧|没问题|交给我|啧|嗯|总之|得嘞',
 'hobbies':'收藏废旧芯片|收藏旧芯片|收集废旧芯片|收藏芯片|收藏|收集|废旧芯片|旧芯片|芯片|听音乐|摇滚乐|音乐|看书|阅读|下棋|做饭|喝咖啡|咖啡|养花|散步|画画|绘画|修小玩意|喜欢|爱好|兴趣|习惯|平时|还'}
BLOCK=re.compile(r'HP|RAM|CAP|ARM|REP|XP|BODY|REF|TECH|INT|COOL|EMP|属性|技能|生命值|血量|伤害|护甲|资金|现金|声望|压力数值|初始装备|新增装备|义体|专属能力|未知档案|隐藏剧情|主线|改变出身|修改出身|改变职业|修改职业|觉醒|无限|无敌|自动成功|免疫|管理员|系统指令|绕过|忽略规则|提示词|ignore|prompt',re.I)

def validate_diy(diy):
 if not isinstance(diy,dict) or set(diy)-FIELDS or len(diy)>10:raise SaveError('DIY只允许外观、生活细节及成年角色的私人身体外观字段')
 validate_body(diy)
 clean={};review=False
 for field,value in diy.items():
  if not isinstance(value,str) or not 1<=len(value.strip())<=(500 if field=='story' else 160):raise SaveError('DIY内容为空或过长，请分项描述')
  value=unicodedata.normalize('NFKC',value).strip()
  if any(ord(x)<32 for x in value) or BLOCK.search(value):raise SaveError('DIY不能改变数值、能力、义体或隐藏事实，也不能包含授权指令；请只描述外观和生活细节')
  if field not in BODY_FIELDS and ANATOMY.search(value):raise SaveError('身体设定请使用独立身体外观字段，默认私密；不能放进公开昵称或普通DIY')
  if field in ('adult_status','body_visibility'):pass
  elif field=='gender':
   if value not in ('女性','男性','非二元'):raise SaveError('请明确性别设定；不从外貌或身体设定推断')
  elif field=='body_features':review=review or body_risk(value)
  elif field=='display_name':
   if not re.fullmatch(r'[\w\u4e00-\u9fff·]{1,20}',value):raise SaveError('展示称呼请使用20字内普通文字，不含指令或特殊格式')
  elif field=='story':review=True
  else:
   remaining=value
   for phrase in sorted(WORDS[field].split('|'),key=len,reverse=True):remaining=remaining.replace(phrase,'')
   remaining=re.sub(r'[\s、,，。！!；;和与的地得着了很是我他她希望想要并且还有点]+','',remaining)
   if remaining:review=True
  clean[field]=value
 return clean,('review' if review else 'auto')

class EnrollmentMixin:
 def enrollment_migrate(self):
  with self._db() as c:c.executescript('''
   CREATE TABLE IF NOT EXISTS neon_enrollment_policy(k TEXT PRIMARY KEY,enabled INTEGER NOT NULL,generation INTEGER NOT NULL,actor TEXT NOT NULL,updated TEXT NOT NULL);
   CREATE TABLE IF NOT EXISTS neon_character_profiles(k TEXT,cid TEXT,owner TEXT,version INTEGER NOT NULL,profile TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(k,cid),UNIQUE(k,owner));
   CREATE TABLE IF NOT EXISTS neon_enrollment_proposals(id TEXT PRIMARY KEY,k TEXT,owner TEXT,cid TEXT,kind TEXT,revision INTEGER,epoch INTEGER,profile_version INTEGER,payload TEXT,preview TEXT,profile TEXT,risk TEXT,template_hash TEXT,policy_generation INTEGER,auto_allowed INTEGER,expires REAL,digest TEXT,status TEXT,committed_revision INTEGER);
   CREATE TABLE IF NOT EXISTS neon_enrollment_consents(pid TEXT,actor TEXT,digest TEXT,mid TEXT,created TEXT,PRIMARY KEY(pid,actor));
   CREATE TABLE IF NOT EXISTS neon_enrollment_messages(k TEXT,actor TEXT,mid TEXT,input_hash TEXT,source_hash TEXT,result TEXT,PRIMARY KEY(k,actor,mid));
   CREATE TABLE IF NOT EXISTS neon_enrollment_audit(id INTEGER PRIMARY KEY,k TEXT,pid TEXT,actor TEXT,event TEXT,detail TEXT,created TEXT);
   ''')
 def enrollment_policy(self,key,c=None):
  if c is None:
   with self._db() as db:return self.enrollment_policy(key,db)
  r=c.execute('SELECT enabled,generation FROM neon_enrollment_policy WHERE k=?',(self._key(key),)).fetchone()
  return dict(r) if r else dict(enabled=0,generation=0)
 def set_enrollment_auto(self,key,actor,enabled,is_admin=False,mid='',source_text=''):
  if is_admin is not True or type(enabled) is not bool:raise SaveError('只有真实AstrBot管理员可以开关角色自动审核')
  hashed=self._enroll_input(key,actor,mid,dict(action='policy',enabled=enabled,source=source_text)) if mid else None
  with self._db() as c:
   c.execute('BEGIN IMMEDIATE');self._row(c,key)
   if mid:
    cached=self._enroll_cached(c,key,actor,mid,hashed)
    if cached:return cached
   if enabled:self.assert_running(c,key)
   old=self.enrollment_policy(key,c);generation=old['generation']
   if old['enabled']!=int(enabled):
    generation+=1
    c.execute('INSERT OR REPLACE INTO neon_enrollment_policy VALUES(?,?,?,?,?)',(key,int(enabled),generation,actor,utcnow()))
    self._enroll_log(c,key,'',actor,'policy',dict(enabled=enabled,generation=generation))
   result=dict(status='policy',cid='',enabled=bool(enabled),generation=generation)
   return self._enroll_receipt(c,key,actor,mid,hashed,result,source_text) if mid else result
 def _enroll_log(self,c,key,pid,actor,event,detail):
  c.execute('INSERT INTO neon_enrollment_audit(k,pid,actor,event,detail,created) VALUES(?,?,?,?,?,?)',(key,pid,actor,event,canonical(detail),utcnow()))
 def _enroll_templates(self):
  # Fixed approved package fingerprint: runtime edits cannot redefine auto-eligible stats.
  from .safe import TEMPLATES
  expected=Path(__file__).with_name('enrollment_templates.sha256').read_text().strip()
  fingerprint=hashlib.sha256(canonical(TEMPLATES).encode()).hexdigest()
  if fingerprint!=expected or set(TEMPLATES)!={f'{x:02d}' for x in range(1,11)}:raise SaveError('预设模板校验失败，停止自动审核并请管理员核对')
  for t in TEMPLATES.values():
   if t['cap']!=10+t['attributes']['BODY']+t['attributes']['TECH'] or t['hp']!=20+4*t['attributes']['BODY'] or sum(x['capacity'] for x in t['cyberware'])!=t['cap_used'] or t['cap']-t['cap_used']!=t['cap_remaining'] or t['cap_remaining']<0:raise SaveError('模板与V3.2基础公式不一致')
  return TEMPLATES,fingerprint
 def character_profile(self,key,cid):
  with self._db() as c:
   role=json.loads(self._row(c,key)['snapshot'])['roles'].get(cid)
   p=c.execute('SELECT * FROM neon_character_profiles WHERE k=? AND cid=?',(key,cid)).fetchone()
   if not role:raise SaveError('角色尚未登记')
   if p and p['owner']!=role['qq_user_id']:raise SaveError('DIY归属异常，请管理员核对')
   return dict(version=p['version'],details=json.loads(p['profile'])) if p else dict(version=0,details={})
 def _enroll_input(self,key,actor,mid,data):
  self._key(key)
  if not isinstance(actor,str) or not actor or actor.strip()!=actor or actor in ('None','null') or any(ord(x)<32 for x in actor) or len(actor)>128 or not isinstance(mid,str) or not 1<=len(mid)<=1000:raise SaveError('缺少真实平台账号或消息标识')
  return hashlib.sha256(canonical(data).encode()).hexdigest()
 def _enroll_cached(self,c,key,actor,mid,hashed):
  r=c.execute('SELECT * FROM neon_enrollment_messages WHERE k=? AND actor=? AND mid=?',(key,actor,mid)).fetchone()
  if r and r['input_hash']!=hashed:raise SaveError('消息标识与正文冲突，未执行')
  return json.loads(r['result']) if r else None
 def _enroll_receipt(self,c,key,actor,mid,hashed,result,source_text=''):
  if result.get('status') in ('preview','cancelled') and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='neon_enrollment_dialogs'").fetchone():
   c.execute('DELETE FROM neon_enrollment_dialogs WHERE k=? AND actor=?',(key,actor))
  c.execute('INSERT INTO neon_enrollment_messages VALUES(?,?,?,?,?,?)',(key,actor,mid,hashed,hashlib.sha256(source_text.encode()).hexdigest(),canonical(result)));return result
 def enrollment_replay(self,key,actor,mid,source_text):
  with self._db() as c:
   r=c.execute('SELECT * FROM neon_enrollment_messages WHERE k=? AND actor=? AND mid=?',(key,actor,mid)).fetchone()
   if r and r['source_hash']!=hashlib.sha256(source_text.encode()).hexdigest():raise SaveError('消息标识与正文冲突，未执行')
   return json.loads(r['result']) if r else None
 def enrollment_pending(self,key,cid=None,kind=None):
  with self._db() as c:rows=c.execute("SELECT rowid AS ticket,* FROM neon_enrollment_proposals WHERE k=? AND status='pending' AND expires>?",(key,time.time())).fetchall()
  return [dict(r) for r in rows if (cid is None or r['cid']==cid) and (kind is None or r['kind']==kind)]
 def _enroll_digest(self,p):
  fields=('id','k','owner','cid','kind','revision','epoch','profile_version','payload','preview','profile','risk','template_hash','policy_generation','auto_allowed','expires')
  return hashlib.sha256(canonical({k:p[k] for k in fields}).encode()).hexdigest()
 def _enroll_preview(self,p):
  snapshot=json.loads(p['preview']);cid=p['cid']
  return dict(status='preview',id=p['id'],digest=p['digest'],cid=cid,kind=p['kind'],role=snapshot['roles'][cid],diy=json.loads(p['profile']),risk=p['risk'],auto=bool(p['auto_allowed']),expires=p['expires'])
 def request_enrollment(self,key,actor,mid,cid,diy=None,kind='claim',source_text=''):
  hashed=self._enroll_input(key,actor,mid,dict(action='request',cid=cid,diy=diy,kind=kind,source=source_text))
  if kind not in ('claim','profile'):raise SaveError('自动审核只支持角色认领和个人DIY，不支持奖励或其他变更')
  incoming=dict(diy or {})
  declaration=self.data_dir/'body_adult_campaigns.json'
  campaign_adult=False
  if declaration.exists():
   settings=json.loads(declaration.read_text())
   campaign_adult=isinstance(settings,dict) and isinstance(settings.get('groups'),list) and key in settings['groups'] and settings.get('confirmed') is True
  if campaign_adult and 'body_features' in incoming:incoming.setdefault('adult_status','成年')
  if incoming.get('adult_status')=='成年' and not campaign_adult and not re.search(r'(?:角色(?:已经|是|为)?(?:已成年|成年人|成年)|成年(?:的)?角色)',source_text):raise SaveError('需要本人明确说明角色成年，不能由模型推定')
  if set(incoming)&BODY_FIELDS and (re.search(r'未成年|非成年|不是成年|并非成年|还没成年|未满18|不满18|(?<!\d)(?:1[0-7]|[0-9])岁',source_text)):raise SaveError('未成年或年龄矛盾，不能保存身体外观')
  # Partial edits are validated after merging the existing owner-bound profile.
  clean,risk=validate_diy(incoming) if 'body_features' not in incoming or incoming.get('adult_status')=='成年' else (incoming,'review')
  if kind=='profile' and not clean:raise SaveError('请说明要修改的DIY细节，未创建申请')
  templates,fingerprint=self._enroll_templates()
  if cid not in templates:raise SaveError('请明确01至10号的一张角色卡')
  with self._db() as c:
   c.execute('BEGIN IMMEDIATE');cached=self._enroll_cached(c,key,actor,mid,hashed)
   if cached:return cached
   self.assert_running(c,key);row=self._row(c,key);s=json.loads(row['snapshot']);validate_snapshot(s);roles=s['roles']
   if s['world']['scene_status']=='paused':raise SaveError('场景暂停中，不能认领或修改DIY')
   if kind=='claim':
    if cid in roles:raise SaveError('该角色已有归属，不能覆盖。'+self._enroll_choices(roles,templates))
    if any(r['qq_user_id']==actor for r in roles.values()):raise SaveError('本账号已持有角色，一个账号在本团只能持有一个角色')
    if sum(r['status']!='retired' for r in roles.values())>=10:raise SaveError('本团十个席位已满，请联系管理员')
    payload=dict(kind='initialize',cid=cid,user=actor,nickname=templates[cid]['name'])
   else:
    if cid not in roles or roles[cid]['qq_user_id']!=actor:raise SaveError('只能修改本人绑定角色的DIY，不能替其他角色决定')
    payload=dict(kind='profile',cid=cid,user=actor)
   oldprofile=c.execute('SELECT * FROM neon_character_profiles WHERE k=? AND cid=?',(key,cid)).fetchone()
   if oldprofile and oldprofile['owner']!=actor:raise SaveError('DIY归属异常，停止申请')
   profile=json.loads(oldprofile['profile']) if oldprofile else {};version=oldprofile['version'] if oldprofile else 0
   profile.update(clean)
   if 'body_features' in clean and 'body_visibility' not in clean:profile['body_visibility']='私密'
   profile,risk=validate_diy(profile)
   if kind=='profile' and profile==json.loads(oldprofile['profile'] if oldprofile else '{}'):raise SaveError('DIY没有变化，未创建申请')
   if kind=='claim':self.apply(c,key,s,payload)
   validate_snapshot(s)
   policy=self.enrollment_policy(key,c);epoch=self.gate_state(key,c)['epoch']
   previous=c.execute("SELECT * FROM neon_enrollment_proposals WHERE k=? AND owner=? AND status='pending' ORDER BY rowid DESC LIMIT 1",(key,actor)).fetchone()
   if previous and previous['revision']==row['revision'] and previous['epoch']==epoch and previous['kind']==kind and previous['cid']==cid and previous['profile']==canonical(profile) and previous['template_hash']==fingerprint and previous['expires']>time.time() and previous['policy_generation']==policy['generation']:
    return self._enroll_receipt(c,key,actor,mid,hashed,self._enroll_preview(previous),source_text)
   c.execute("UPDATE neon_enrollment_proposals SET status='superseded' WHERE k=? AND owner=? AND status='pending'",(key,actor))
   # A new explicit application supersedes this owner's old-format initialization
   # drafts too; no old consent can approve a different, newly displayed card.
   for legacy in c.execute("SELECT id,payload FROM neon_v020_proposals WHERE group_key=? AND status='pending'",(key,)).fetchall():
    data=json.loads(legacy['payload'])
    if data.get('kind')=='initialize' and data.get('user')==actor:c.execute("UPDATE neon_v020_proposals SET status='superseded' WHERE id=?",(legacy['id'],))
   p=dict(id=secrets.token_hex(12),k=key,owner=actor,cid=cid,kind=kind,revision=row['revision'],epoch=epoch,profile_version=version,payload=canonical(payload),preview=canonical(s),profile=canonical(profile),risk=risk,template_hash=fingerprint,policy_generation=policy['generation'],auto_allowed=int(bool(policy['enabled']) and risk=='auto'),expires=time.time()+1800,status='pending',committed_revision=None)
   p['digest']=self._enroll_digest(p)
   fields=list(p);c.execute('INSERT INTO neon_enrollment_proposals('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',[p[x] for x in fields])
   self._enroll_log(c,key,p['id'],actor,'request',dict(digest=p['digest'],payload=payload,profile=profile,risk=risk))
   return self._enroll_receipt(c,key,actor,mid,hashed,self._enroll_preview(p),source_text)
 def _enroll_choices(self,roles,templates):
  free=[f'{cid}号{t["name"]}' for cid,t in templates.items() if cid not in roles]
  return '可选择：'+'、'.join(free) if free else '暂无空闲角色，请联系管理员'
 def enrollment_current(self,key,actor):
  with self._db() as c:
   p=c.execute("SELECT * FROM neon_enrollment_proposals WHERE k=? AND owner=? AND status='pending' ORDER BY rowid DESC LIMIT 1",(key,actor)).fetchone()
   return dict(p) if p else None
 def _enroll_check(self,c,key,pid,digest):
  p=c.execute('SELECT * FROM neon_enrollment_proposals WHERE id=? AND k=?',(pid,key)).fetchone()
  if not p or p['digest']!=digest or self._enroll_digest(p)!=digest:raise SaveError('申请内容校验失败，请重新查询自己的申请')
  return p
 def _enroll_valid(self,c,p):
  if p['status']!='pending' or p['expires']<=time.time():raise SaveError('申请已处理或过期，请重新申请并确认新预览')
  self.assert_running(c,p['k'])
  if self.gate_state(p['k'],c)['epoch']!=p['epoch']:raise SaveError('暂停状态已变化，旧申请失效，请重新申请')
  if json.loads(self._row(c,p['k'])['snapshot'])['world']['scene_status']=='paused':raise SaveError('场景暂停中，不能提交')
  if self._row(c,p['k'])['revision']!=p['revision']:raise SaveError('存档已经变化，请重新申请并核对新预览；旧同意不沿用')
  _,fingerprint=self._enroll_templates()
  if fingerprint!=p['template_hash']:raise SaveError('模板已经变化，请管理员核对')
  old=c.execute('SELECT * FROM neon_character_profiles WHERE k=? AND cid=?',(p['k'],p['cid'])).fetchone()
  if (old['version'] if old else 0)!=p['profile_version'] or old and old['owner']!=p['owner']:raise SaveError('DIY版本或归属已变化，请重新申请')
  _,risk=validate_diy(json.loads(p['profile']))
  if risk!=p['risk']:raise SaveError('DIY审核规则已变化，请重新申请')
 def confirm_enrollment(self,key,actor,mid,pid,digest,source_text=''):
  hashed=self._enroll_input(key,actor,mid,dict(action='confirm',pid=pid,digest=digest,source=source_text))
  with self._db() as c:
   c.execute('BEGIN IMMEDIATE');cached=self._enroll_cached(c,key,actor,mid,hashed)
   if cached:return cached
   p=self._enroll_check(c,key,pid,digest)
   if p['owner']!=actor:raise SaveError('必须由申请者本人确认，其他账号及AI文字不能代替')
   if p['status']=='committed':return self._enroll_receipt(c,key,actor,mid,hashed,self._enroll_committed(p),source_text)
   self._enroll_valid(c,p)
   c.execute('INSERT OR IGNORE INTO neon_enrollment_consents VALUES(?,?,?,?,?)',(pid,actor,digest,mid,utcnow()))
   self._enroll_log(c,key,pid,actor,'consent',dict(digest=digest,message=mid))
   policy=self.enrollment_policy(key,c)
   if p['auto_allowed'] and p['risk']=='auto' and policy['enabled'] and policy['generation']==p['policy_generation']:
    result=self._enroll_commit(c,p,'system:character_enrollment','auto')
   else:result=dict(status='awaiting_admin',cid=p['cid'],kind=p['kind'],id=pid,digest=digest,risk=p['risk'])
   return self._enroll_receipt(c,key,actor,mid,hashed,result,source_text)
 def approve_enrollment(self,key,actor,mid,pid,digest,is_admin=False,source_text=''):
  if is_admin is not True:raise SaveError('只有AstrBot管理员可以人工批准')
  hashed=self._enroll_input(key,actor,mid,dict(action='approve',pid=pid,digest=digest,source=source_text))
  with self._db() as c:
   c.execute('BEGIN IMMEDIATE');cached=self._enroll_cached(c,key,actor,mid,hashed)
   if cached:return cached
   p=self._enroll_check(c,key,pid,digest)
   if p['status']=='committed':return self._enroll_receipt(c,key,actor,mid,hashed,self._enroll_committed(p),source_text)
   self._enroll_valid(c,p);result=self._enroll_commit(c,p,actor,'manual')
   return self._enroll_receipt(c,key,actor,mid,hashed,result,source_text)
 def _enroll_committed(self,p):return dict(status='committed',cid=p['cid'],kind=p['kind'],revision=p['committed_revision'],id=p['id'],digest=p['digest'])
 def _enroll_commit(self,c,p,actor,mode):
  self._enroll_valid(c,p)
  if mode=='auto':
   policy=self.enrollment_policy(p['k'],c)
   if not p['auto_allowed'] or p['risk']!='auto' or not policy['enabled'] or policy['generation']!=p['policy_generation']:raise SaveError('角色自动审核条件已失效')
  elif mode!='manual':raise SaveError('审核模式无效')
  if not c.execute('SELECT 1 FROM neon_enrollment_consents WHERE pid=? AND actor=? AND digest=?',(p['id'],p['owner'],p['digest'])).fetchone():raise SaveError('尚缺申请者本人对准确预览的同意')
  row=self._row(c,p['k']);old=json.loads(row['snapshot']);s=copy.deepcopy(old);payload=json.loads(p['payload']);profile=json.loads(p['profile'])
  if p['kind']=='claim':
   if payload!=dict(kind='initialize',cid=p['cid'],user=p['owner'],nickname=self._enroll_templates()[0][p['cid']]['name']):raise SaveError('认领参数异常，停止提交')
   self.apply(c,p['k'],s,payload)
   s['roles'][p['cid']]['joined_at']=json.loads(p['preview'])['roles'][p['cid']]['joined_at']
   if s['roles'][p['cid']]['status']!='pending':raise SaveError('认领不能自动进入场景')
  else:
   if payload!=dict(kind='profile',cid=p['cid'],user=p['owner']) or s['roles'].get(p['cid'],{}).get('qq_user_id')!=p['owner']:raise SaveError('只能修改本人DIY')
  validate_snapshot(s)
  if canonical(s)!=p['preview']:raise SaveError('角色卡预览发生变化，拒绝提交')
  oldprofile=c.execute('SELECT profile FROM neon_character_profiles WHERE k=? AND cid=?',(p['k'],p['cid'])).fetchone()
  self.log(c,p['k'],actor,'preimage',canonical(dict(revision=row['revision'],snapshot=old,profile=json.loads(oldprofile['profile']) if oldprofile else {})))
  # Cosmetic edits have their own signed version. They must not invalidate an
  # active GM action window whose numeric game snapshot is entirely unchanged.
  rev=row['revision']+(1 if p['kind']=='claim' else 0);now=utcnow()
  c.execute('INSERT OR REPLACE INTO neon_character_profiles VALUES(?,?,?,?,?,?)',(p['k'],p['cid'],p['owner'],p['profile_version']+1,p['profile'],now))
  if p['kind']=='claim':c.execute('UPDATE saves SET snapshot=?,revision=?,updated_at=?,updated_by=? WHERE group_key=?',(canonical(s),rev,now,actor,p['k']))
  c.execute('INSERT INTO audit(group_key,revision,actor,action,reason,before_json,after_json,timestamp) VALUES(?,?,?,?,?,?,?,?)',(p['k'],rev,actor,'enrollment_'+p['kind'],p['id']+':'+p['digest'],canonical(old),canonical(s),now))
  c.execute("UPDATE neon_enrollment_proposals SET status='committed',committed_revision=? WHERE id=?",(rev,p['id']))
  self._enroll_log(c,p['k'],p['id'],actor,'commit',dict(mode=mode,revision=rev,profile_version=p['profile_version']+1,before_profile=json.loads(oldprofile['profile']) if oldprofile else {},profile=profile,digest=p['digest']))
  return dict(status='committed',cid=p['cid'],kind=p['kind'],revision=rev,id=p['id'],digest=p['digest'])
 def cancel_enrollment(self,key,actor,mid,source_text=''):
  hashed=self._enroll_input(key,actor,mid,dict(action='cancel',source=source_text))
  with self._db() as c:
   c.execute('BEGIN IMMEDIATE');cached=self._enroll_cached(c,key,actor,mid,hashed)
   if cached:return cached
   self.assert_running(c,key)
   p=c.execute("SELECT * FROM neon_enrollment_proposals WHERE k=? AND owner=? AND status='pending' ORDER BY rowid DESC LIMIT 1",(key,actor)).fetchone()
   if not p:raise SaveError('没有自己的待处理申请')
   c.execute("UPDATE neon_enrollment_proposals SET status='cancelled' WHERE id=?",(p['id'],));self._enroll_log(c,key,p['id'],actor,'cancel',{})
   return self._enroll_receipt(c,key,actor,mid,hashed,dict(status='cancelled',cid=p['cid']),source_text)
