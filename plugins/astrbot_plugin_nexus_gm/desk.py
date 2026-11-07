"""Deterministic chat authorization. No LLM output is accepted as identity/consent.

All mutations delegate to unchanged SafeStore. A process lock serializes selection,
proposal creation and persisted delivery receipts across restarts/workers.
"""
import fcntl,hashlib,json,re,time,unicodedata
from contextlib import contextmanager
from pathlib import Path

class DeskError(ValueError):pass

class NaturalDesk:
 def __init__(self,save,directory,templates,engine=None):
  self.save=save;self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
  self.templates=templates;self.engine=engine
  if {k:v['name'] for k,v in templates.items()}!={k:v['name'] for k,v in self.public_templates().items()}:raise DeskError('公开角色索引与存档模板不一致，已停止认领')
  with save._db() as c:
   c.executescript('''CREATE TABLE IF NOT EXISTS nexus_chat_requests(
    ticket INTEGER PRIMARY KEY AUTOINCREMENT,k TEXT NOT NULL,pid TEXT UNIQUE NOT NULL,
    digest TEXT NOT NULL,kind TEXT NOT NULL,cid TEXT NOT NULL,owner TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS nexus_chat_receipts(
    k TEXT,actor TEXT,mid TEXT,text_hash TEXT,response TEXT,PRIMARY KEY(k,actor,mid));''')
 @contextmanager
 def locked(self):
  with (self.directory/'natural-desk.lock').open('a') as f:
   fcntl.flock(f,fcntl.LOCK_EX)
   try:yield
   finally:fcntl.flock(f,fcntl.LOCK_UN)
 @staticmethod
 def parse(text):
  t=unicodedata.normalize("NFKC",text).strip().rstrip('。！!').strip()
  m=re.fullmatch(r'(?:我想|我要|我想要|申请|我申请)?认领\s*(0?[1-9]|10)\s*号?(?:\s*([^\s，,。！!]{1,12}))?',t)
  if m:return ('claim',m.groups())
  m=re.fullmatch(r'批准\s*(0?[1-9]|10)\s*号(?:入团|认领)(?:[，, ]*申请\s*(\d+))?',t)
  if m:return ('approve',m.groups())
  m=re.fullmatch(r'确认(?:认领(?:\s*(0?[1-9]|10)\s*号)?)?(?:[，, ]*申请\s*(\d+))?',t)
  if m:return ('consent',m.groups())
  m=re.fullmatch(r'取消认领(?:\s*(0?[1-9]|10)\s*号)?',t)
  if m:return ('cancel',m.groups())
  if t in ('我的角色卡','查看我的角色卡','查询角色卡','查看角色卡','我的背包','查看我的背包','查看背包','背包'):return ('read',(t,))
  if t in ('申请入场','我要入场','激活角色','申请激活角色'):return ('activate',())
  if t in ('申请任务结算','申请结算','生成结算草案'):return ('settle',())
  # Flexible recognition resolves only a unique public template reference.
  # These are human message intents, never LLM-produced tool arguments.
  if '认领' in t or re.search(r'(?:我想|我要|请帮我|让我)(?:选择|扮演|玩)|(?:我想玩|我要玩)',t):
   if any(x in t for x in ('不','别','没','取消','他说','她说','有人说','代替','帮他','帮她','替他','替她','是否','能否','怎么','如何','？','?','“','”','「','」')):
    return ('clarify',('请本人明确说明想认领哪个角色；未创建申请或记录同意。',))
   ids={f'{int(x):02d}' for x in re.findall(r'(?<![0-9])(0?[1-9]|10)(?![0-9])',t)}
   words={'一':'01','二':'02','三':'03','四':'04','五':'05','六':'06','七':'07','八':'08','九':'09','十':'10'}
   ids.update(words[x] for x in re.findall(r'([一二三四五六七八九十])号',t))
   # Names come from existing templates; their text cannot set any save field.
   template_names={v['name']:k for k,v in NaturalDesk.public_templates().items()}
   names={cid for name,cid in template_names.items() if name in t}
   if len(ids)>1 or len(names)>1 or (ids and names and ids!=names):
    return ('clarify',('认领对象不唯一或编号与名字冲突，请一次明确一个角色。',))
   refs=ids or names
   if len(refs)!=1:return ('clarify',('请明确角色号或模板名字，例如“我想认领03号扳手”。',))
   cid=next(iter(refs))
   # Saying consent for a non-existent request never creates one implicitly.
   return ('consent',(cid,None)) if '确认' in t else ('claim',(cid,None))
  if re.fullmatch(r'(?:我)?(?:已)?确认(?:了|无误|同意)?(?:[,， ]*(?:请|麻烦)?(?:帮我)?(?:完成登记|提交审核|登记))?',t):return ('consent',(None,None))
  return None
 @staticmethod
 def public_templates():
  return json.loads(Path(__file__).with_name('public_claim_names.json').read_text())
 def handle(self,k,actor,mid,text,admin=False):
  action=self.parse(text)
  if action is None:return None
  if not actor or not mid:raise DeskError('未取得真实账号或消息标识，未执行，请重新发送')
  digest=hashlib.sha256(text.encode()).hexdigest()
  with self.locked():
   with self.save._db() as c:
    row=c.execute('SELECT text_hash,response FROM nexus_chat_receipts WHERE k=? AND actor=? AND mid=?',(k,actor,mid)).fetchone()
   if row:
    if row['text_hash']!=digest:raise DeskError('消息标识冲突，未执行')
    # Returning a receipt performs no mutation, even during pause.
    return row['response']
   response=self.execute(k,actor,action,admin)
   with self.save._db() as c:c.execute('INSERT INTO nexus_chat_receipts VALUES(?,?,?,?,?)',(k,actor,mid,digest,response))
   return response
 def requests(self,k,owner=None,cid=None,ticket=None):
  with self.save._db() as c:
   rows=c.execute('''SELECT r.*,p.status,p.expires,p.revision,p.payload,p.preview,p.required
    FROM nexus_chat_requests r JOIN neon_v020_proposals p ON p.id=r.pid
    WHERE r.k=? AND p.group_key=? AND p.status='pending' AND p.expires>?''',(k,k,time.time())).fetchall()
  current=self.save.read(k)['revision'];epoch=self.save.gate_state(k)['epoch']
  with self.save._db() as c:epochs={r['pid']:c.execute('SELECT epoch FROM neon_safety_proposals WHERE pid=?',(r['pid'],)).fetchone() for r in rows}
  rows=[r for r in rows if r['revision']==current and epochs[r['pid']] is not None and epochs[r['pid']][0]==epoch]
  return [r for r in rows if (owner is None or r['owner']==owner) and (cid is None or r['cid']==cid) and (ticket is None or r['ticket']==int(ticket))]
 def register(self,k,p,kind,cid,owner):
  with self.save._db() as c:
   c.execute('INSERT OR IGNORE INTO nexus_chat_requests(k,pid,digest,kind,cid,owner) VALUES(?,?,?,?,?,?)',(k,p['id'],p['digest'],kind,cid,owner))
   return c.execute('SELECT ticket FROM nexus_chat_requests WHERE pid=?',(p['id'],)).fetchone()[0]
 def card(self,r,cid):
  t=self.templates[cid]
  attrs='、'.join(f'{k} {v}' for k,v in r['attributes'].items())
  skills='、'.join(f'{k} {v}' for k,v in r['skills'].items() if v)
  return f"{cid}号{t['name']}｜{r['job']}\n生命 {r['hp']}/{r['max_hp']}，现金 {r['cash']}，XP {r['xp']}\n属性：{attrs}\n技能：{skills or '无已训练技能'}"
 def execute(self,k,actor,action,admin):
  kind,args=action
  if kind=='clarify':return args[0]
  if kind=='cancel':
   cid=f'{int(args[0]):02d}' if args[0] else None
   rows=self.requests(k,owner=actor,cid=cid)
   if len(rows)!=1:raise DeskError('没有唯一对应的本人有效认领申请，未取消')
   r=rows[0];self.save.consent(k,actor,r['pid'],r['digest'],accept=False)
   return f"已取消你的{r['cid']}号认领申请，未修改角色存档。"
  if kind=='read':
   roles=self.save.read(k)['snapshot']['roles'];own=[(cid,r) for cid,r in roles.items() if r['qq_user_id']==actor]
   if len(own)!=1:raise DeskError('当前账号没有唯一绑定角色；可说“我想认领03号扳手”')
   cid,r=own[0]
   if '背包' not in args[0]:return self.card(r,cid)+f"\n状态：{r['status']}。未知档案不会自动揭晓。"
   # Internal metadata and hidden dossier material never enter public chat.
   items=[x for x in r['equipment'] if isinstance(x,str)]
   return f"{cid}号背包："+'、'.join(items)+f"。现金 {r['cash']}。"
  if kind=='activate':
   return '入场申请尚未执行：需管理员核实安全节点、队伍、场景，以及无战斗、无锁定检定。当前不会自动补造入场证据；角色状态未改变。'
  if kind=='settle':
   if not admin:return '已收到结算请求；请管理员核对真实任务完成及各人参与记录后申请结算。尚未生成或发放奖励。'
   raise DeskError('本入口暂不自动关联跨玩家结算；需管理员核对现有任务结算草案，未发放奖励')
  if kind=='claim':
   cid=f'{int(args[0]):02d}';name=args[1];t=self.templates[cid]
   if name and name!=t['name']:raise DeskError(f"编号与名字不一致：{cid}号是{t['name']}，请明确认领对象")
   current=self.save.read(k);roles=current['snapshot']['roles']
   if cid in roles:raise DeskError('该角色已有归属，不能覆盖；请查询本人角色卡或选择其他角色')
   if any(r['qq_user_id']==actor for r in roles.values()):raise DeskError('本账号已绑定角色，不能重复认领')
   pending=self.requests(k,owner=actor)
   if pending:
    if len(pending)!=1 or pending[0]['cid']!=cid:raise DeskError('本账号已有待审核认领，请先处理原申请；不会创建第二份')
    r=pending[0];p={'id':r['pid'],'digest':r['digest'],'result':json.loads(r['preview'])};ticket=r['ticket']
    if r['revision']!=current['revision']:raise DeskError('原申请因存档变化需要重新核对；请管理员处理旧申请，原同意不能移用')
   else:
    p=self.save.propose(k,actor,current['revision'],dict(kind='initialize',cid=cid,user=actor,nickname=t['name']))
    ticket=self.register(k,p,'claim',cid,actor)
   r=p['result']['roles'][cid]
   return self.card(r,cid)+f"\n能力：{t['ability']}；护甲 {t['arm']}；CAP {t['cap']}，已占 {t['cap_used']}，剩余 {t['cap_remaining']}\n装备：{'、'.join(x for x in r['equipment'] if isinstance(x,str))}\n认领申请{ticket}：角色归当前发送账号；入团后先为待入场状态。本卡仅待审核，尚未写入角色存档。核对无误请本人回复“确认”；管理员随后说“批准{cid}号入团”。30分钟内有效，存档变化需重新申请并确认。也可说“取消认领”。"
  if kind=='consent':
   cid=f'{int(args[0]):02d}' if args[0] else None
   rows=self.requests(k,owner=actor,cid=cid,ticket=args[1])
   if len(rows)!=1:raise DeskError('没有唯一对应的本人有效认领申请。若已过期、重新暂停或存档已变化，请重新发送认领请求并核对新卡；有多份申请时请明确角色与申请编号。未记录同意')
   r=rows[0]
   self.save.consent(k,actor,r['pid'],r['digest'])
   return f"已记录你对申请{r['ticket']}、{r['cid']}号角色卡的本人同意，仍待管理员审核；尚未入团。"
  if kind=='approve':
   if admin is not True:raise DeskError('只有AstrBot管理员可以批准入团')
   cid=f'{int(args[0]):02d}';rows=self.requests(k,cid=cid,ticket=args[1])
   if len(rows)!=1:
    choices='、'.join(f"申请{r['ticket']}" for r in rows)
    raise DeskError('没有唯一对应的有效申请；'+(f'候选为{choices}。请明确说“批准{cid}号入团，申请{rows[0]["ticket"]}”，并核对申请人。' if rows else '请玩家重新查询认领申请。')+'未批准。')
   r=rows[0]
   # Stored mapping is checked against the immutable proposal again before delegation.
   with self.save._db() as c:
    p=self.save.proposal(c,k,r['pid']);payload=json.loads(p['payload'])
    if p['digest']!=r['digest'] or payload!={'kind':'initialize','cid':cid,'user':r['owner'],'nickname':self.templates[cid]['name']}:raise DeskError('申请关联校验失败，未批准')
   self.save.approve(k,actor,r['pid'],r['digest'],is_admin=admin)
   return f"{cid}号{self.templates[cid]['name']}已审核入团，归属申请{r['ticket']}的申请者。当前为待入场，尚未激活；未自动开场或发放任务奖励。"
