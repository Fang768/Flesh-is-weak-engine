"""Voluntary rolling admission: one real player starts, later players join safely."""
import json,time,sqlite3,importlib,hashlib,datetime
from .opening_intent import opening_intent
from .opening_lock import serialized
from pathlib import Path
INTRO='歌舞伎的霓虹映在雨后的路面上。米拉·克罗收起通讯器，朝你点了点头：“有枚芯片留在一名死去快递员的储物柜里。我想找人把它取回来。先谈谈，接不接由你决定。”\n她给你留出了开口的空隙。你想先问她什么，或者做些什么？'
class SimpleOpen:
 def __init__(self,directory,save,dynamic):
  self.path=str(directory/'simple_open.sqlite3');self.directory=directory;self.save=save;self.dynamic=dynamic
  with self.db() as c:
   c.execute('CREATE TABLE IF NOT EXISTS openings(k TEXT PRIMARY KEY,v TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS opening_messages(k TEXT,actor TEXT,mid TEXT,input_hash TEXT,response TEXT,PRIMARY KEY(k,actor,mid))')
 def db(self):
  c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;return c
 def read(self,k):
  with self.db() as c:r=c.execute('select v from openings where k=?',(k,)).fetchone()
  return json.loads(r[0]) if r else None
 def write(self,k,s):
  with self.db() as c:c.execute('insert or replace into openings values(?,?)',(k,json.dumps(s,ensure_ascii=False)))
 def authority(self,k,actor='',mid='',is_admin=False):
  if is_admin is True:return dict(actor=actor,source='real_admin_event',mid=mid)
  p=self.directory/'simple_open_authorization.json'
  if p.exists():
   d=json.loads(p.read_text())
   if d.get('enabled') is True and isinstance(d.get('groups'),list) and k in d.get('groups',[]) and d.get('scope')=='voluntary_noncombat_open_and_pending_join':return dict(actor='operator:codex:explicit_user_rolling_start',source='explicit_user_delegation',authorization=d)
  old=self.read(k)
  if old and old.get('version')==2 and old.get('authority',{}).get('source')=='real_admin_event':return old['authority']
  raise ValueError('本群尚未获授权开场，请管理员先说开始跑团。')
 def state(self,k,authority):
  s=self.read(k)
  if s and s.get('version')!=2:raise ValueError('旧的全员开场安排需先撤销；不沿用他人同意。')
  if not s:s=dict(version=2,authority=authority,status='waiting',joins={},queue={})
  return s
 def own(self,k,actor):
  matches=[(cid,r) for cid,r in self.save.read(k)['snapshot']['roles'].items() if r['qq_user_id']==actor]
  if len(matches)!=1:raise ValueError('请先认领自己的角色，不能替别人到场。')
  cid,role=matches[0]
  if role['status'] not in ('pending','active'):raise ValueError(cid+'号目前暂离或已退团，先按原流程回归，不自动改状态。')
  if role['hp']<=0:raise ValueError(cid+'号目前倒地，先处理伤势；入场不会自动治疗。')
  return cid,role
 def inspect(self,k,actor,mid='',is_admin=False):
  errors=[]
  if not actor or actor in ('None','True','False'):errors.append('没有取得真实QQ账号，请用本人账号真正@机器人。')
  if k not in json.loads(self.dynamic.allow.read_text()):errors.append('本群还没有开放主持入口；请先为本群配置主持授权。')
  try:
   game=self.save.read(k)
   store=importlib.import_module(self.save.__class__.__module__.rsplit('.',1)[0]+'.store');store.validate_snapshot(game['snapshot'])
  except Exception:return dict(errors=['本群没有可读取的有效存档，请先创建或核对存档；不会用新开场覆盖它。'],phase='blocked')
  world=game['snapshot']['world'];gate=self.save.gate_state(k)
  if gate['paused'] or world['scene_status']=='paused':errors.append('目前处于暂停。先由相关玩家同意并经原流程恢复；开场命令不能解除暂停。')
  if world.get('act')!=1:errors.append('存档已不在第一幕，不能用开场指令回滚剧情；请继续当前幕或核对正确群。')
  if world.get('pending_actions'):errors.append('还有未处理的行动或检定，请先完成或核对它们，再开场。')
  engine=self.dynamic.get();session=engine.read(k)
  with engine.db() as c:
   if c.execute("select 1 from requests where k=? and phase='in_flight'",(k,)).fetchone():errors.append('有执行中断的检定记录，先核对骰点证据；不能靠重开绕过。')
  own=[(cid,r) for cid,r in game['snapshot']['roles'].items() if r['qq_user_id']==actor]
  phase='not_started'
  if session:
   phase='running'
   if world['scene_status']!='active':errors.append('存档与主持记录的场景状态不一致，请核对已有记录，不要另开覆盖。')
   if not session.get('started'):errors.append('当前场景已有未完成的就绪记录，请核对已出场成员；不需要等未入场的人。')
  elif world['scene_status']=='active':
   old=self.read(k);receipt=old.get('joins',{}).get(actor) if old else None
   with self.save._db() as c:proof=c.execute('select status,digest,revision from neon_v020_proposals where id=? and group_key=?',((receipt or {}).get('pid',''),k)).fetchone()
   if receipt and proof and proof['status']=='committed' and proof['digest']==receipt['digest'] and proof['revision']+1==game['revision']:phase='recoverable'
   else:errors.append('存档里已有场景，但缺少匹配的主持启动回执。请恢复对应主持记录，不能重新覆盖。')
  elif world['scene_status']!='not_started':errors.append('当前场景状态不允许新开第一幕，请先核对已保存的场景。')
  if phase=='not_started':
   if not own:
    if not game['snapshot']['roles']:errors.append('还没有已登记角色。至少一位玩家先认领并确认自己的角色。')
    elif is_admin is not True:errors.append('你还没有绑定角色。请先认领，或让已有角色的玩家先开始。')
   elif len(own)!=1:errors.append('本账号的角色绑定不唯一，先核对归属，不重新登记。')
   elif own[0][1]['status'] not in ('pending','active'):errors.append(own[0][0]+'号目前暂离或已退团，先完成原有回归流程。')
   elif own[0][1]['hp']<=0:errors.append(own[0][0]+'号目前倒地，先按规则处理，开场不会自动治疗。')
   other_active=[cid for cid,r in game['snapshot']['roles'].items() if r['status']=='active' and r['qq_user_id']!=actor]
   if other_active:errors.append('存档中已有出场角色'+ '、'.join(other_active)+'号，但没有匹配的场景记录；先核对原进度，不擅自覆盖。')
  try:self.authority(k,actor,mid,is_admin is True)
  except ValueError:errors.append('本群未授权你发起开场。请由真实AstrBot管理员说“现在开团”，或先明确配置本群自主开场授权。')
  return dict(errors=errors,phase=phase,game=game,session=session,own=own)
 def running_reply(self,state):
  own=state.get('own',[])
  if len(own)!=1:return '第一幕已经在进行，不需要重开。你尚未绑定本群角色，先认领；已有角色的玩家可以继续。'
  cid,role=own[0]
  if role['status']=='pending':return '第一幕已经在进行，但你还没入场。说“我来了”，就会在安全窗口加入，不重开剧情。'
  if role['status'] in ('away','retired'):return cid+'号目前暂离或已退团；先按原流程回归，不用重新开团。'
  if role['hp']<=0:return cid+'号目前倒地，先处理伤势，不能靠重新开场回血。'
  if cid not in state['session']['roster']:return '场景已开始，但你的角色还没接入当前主持记录。请在安全节点核对动态成员，不要重新开场。'
  return '第一幕已经在进行，直接接着行动即可；不会重复开场。'
 def status_reply(self,k,actor,is_admin=False):
  state=self.inspect(k,actor,is_admin=is_admin)
  if state['errors']:return '不用等固定时间，也不要求人齐。现在卡在：'+ '；'.join(state['errors'])
  if state['phase']=='running':return self.running_reply(state)
  if state['phase']=='recoverable':return '开场已写入存档，只差恢复对应的主持记录。本人再说“现在开团”即可接回原进度，不会重复入场。'
  if not state.get('own'):return '没有固定开场时间。你可以授权开场，但至少需要一名真实玩家参与；让已有角色的玩家说“我来了”，就从他开始。'
  return '开场条件已具备，不用等全员。现在说“现在开团”或“开始第一幕”就会真正开场，其他人可以稍后加入。'
 @serialized
 def start(self,k,actor,mid,is_admin):
  checked=self.inspect(k,actor,mid,is_admin)
  if checked['errors']:raise ValueError('现在还不能开场：'+'；'.join(checked['errors']))
  if checked['phase']=='running':
   if len(checked.get('own',[]))==1 and checked['own'][0][1]['status']=='pending':return self.arrive(k,actor,mid)
   return self.running_reply(checked)
  auth=self.authority(k,actor,mid,is_admin is True)
  if not mid or self.save.gate_state(k)['paused']:raise ValueError('暂停中或消息身份缺失，保持原处。')
  s=self.state(k,auth)
  # Capture real admin authorization when there is no committed opening to recover.
  if is_admin is True and checked['phase']!='recoverable':s['authority']=auth
  self.write(k,s)
  try:self.own(k,actor)
  except ValueError:
   if not is_admin:raise
   return '已开放开场。谁准备好，谁说“我来了”先进入；其他人可以稍后加入，不等人齐。'
  return self.arrive(k,actor,mid)
 @serialized
 def arrive(self,k,actor,mid):
  auth=self.authority(k);s=self.state(k,auth)
  if not mid or self.save.gate_state(k)['paused']:raise ValueError('暂停中或消息身份缺失，保持原处。')
  cid,role=self.own(k,actor);e=self.dynamic.get();before=e.read(k)
  if before and cid in before['roster']:
   if before['roster'][cid]['user']!=actor:raise ValueError('角色归属变化，先核对。')
   if before['roster'][cid]['away']:raise ValueError('暂离回归须从安全节点核对，不自动覆盖。')
   if actor in s['queue']:s['queue'].pop(actor,None);self.write(k,s)
   return '你已在场，直接接着行动即可，不会重复入场。'
  # Keep the exact player request if the current window is busy; never interrupt it.
  if before:
   with e.db() as c:busy=before['events'] or before['proposal'] or c.execute("select 1 from requests where k=? and phase='in_flight'",(k,)).fetchone()
   if busy:
    s['queue'][actor]=dict(mid=mid,cid=cid,expires=time.time()+1800,epoch=self.save.gate_state(k)['epoch']);self.write(k,s)
    return '收到，你到了。当前这轮先让同伴处理完，我会在下一个安全窗口接你入场，不打断他们的行动。'
  game=self.save.read(k);first=before is None;receipt=s['joins'].get(actor)
  if first and game['snapshot']['world']['scene_status'] not in ('not_started','active'):raise ValueError('当前场景不能安全入场。')
  if first and game['snapshot']['world']['scene_status']=='active' and not receipt:raise ValueError('已有场景但缺少开场记录，不覆盖原剧情。')
  p=None
  if receipt:
   with self.save._db() as c:row=c.execute('select * from neon_v020_proposals where group_key=? and id=?',(k,receipt['pid'])).fetchone()
   if row and row['digest']==receipt['digest'] and row['actor']==s['authority']['actor'] and (row['status']=='committed' or row['status']=='pending' and row['expires']>time.time() and row['revision']==game['revision'] and receipt['epoch']==self.save.gate_state(k)['epoch']):p=dict(id=row['id'],digest=row['digest'],committed=row['status']=='committed')
  if before and before['revision']!=game['revision'] and not (p and p['committed'] and receipt and row['revision']==before['revision'] and game['revision']==before['revision']+1):raise ValueError('存档版本已变化，先核对原窗口，未覆盖剧情。')
  if not p:
   ops=[]
   if first:
    if any(r['status']=='active' and r['qq_user_id']!=actor for r in game['snapshot']['roles'].values()):raise ValueError('已有其他出场角色，请先核对场景，不擅自替其开场。')
    ops=[dict(kind='world',field=f,value=v,reason='获授权的自愿开场，仅实际到场者入场，不发钱或代接任务') for f,v in dict(scene_status='active',scene='第一幕公开接洽：米拉·克罗在沃森歌舞伎介绍芯片委托',location='沃森·歌舞伎',recap='首位玩家自愿开始第一幕；其他人尚未到场，可在安全节点加入。').items()]
   if role['status']=='pending':ops.append(dict(kind='status',cid=cid,value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='第一幕公开接洽的安全入口；不改写队友已有剧情',team='第一幕参与者')))
   if not ops:raise ValueError('角色已出场但缺少对应剧情记录，请核对，不重复激活。')
   p=self.save.propose(k,s['authority']['actor'],game['revision'],dict(kind='batch',operations=ops));p['committed']=False
   s['joins'][actor]=dict(pid=p['id'],digest=p['digest'],mid=mid,cid=cid,epoch=self.save.gate_state(k)['epoch']);self.write(k,s)
  # Source authority is a real administrator event or the user's explicit, limited
  # delegation. The joining player's actual message supplies their own consent only.
  if not p['committed']:self.save.consent(k,actor,p['id'],p['digest'])
  if before:
   with e.db() as c:
    c.execute('BEGIN IMMEDIATE');row=c.execute('select v from sessions where k=?',(k,)).fetchone();state=json.loads(row[0]);e.running(k)
    if state['events'] or state['proposal'] or c.execute("select 1 from requests where k=? and phase='in_flight'",(k,)).fetchone():
     s['queue'][actor]=dict(mid=mid,cid=cid,expires=time.time()+1800,epoch=self.save.gate_state(k)['epoch']);self.write(k,s);return '已记下到场，等本轮结束后安全加入。'
    if not p['committed']:self.save.approve(k,s['authority']['actor'],p['id'],p['digest'],True)
    g=self.save.read(k);r=g['snapshot']['roles'].get(cid)
    if not r or r['qq_user_id']!=actor or r['status']!='active':raise ValueError('真实入场回执不匹配。')
    state['roster'][cid]=dict(user=actor,team='A',away=False,position='contact');state['revision']=g['revision']
    if cid not in state['ready']:state['ready'].append(cid)
    state['started']=True;e.write(c,k,state,dict(kind='voluntary_safe_join',cid=cid,actor=actor,message_id=mid,proposal=dict(id=p['id'],digest=p['digest'])))
    c.execute('delete from advisories where k=? and round=?',(k,state['round']))
  else:
   if not p['committed']:self.save.approve(k,s['authority']['actor'],p['id'],p['digest'],True)
   if not e.read(k):e.prepare(k,s['authority']['actor'],True)
   e.ready(k,actor)
  with self.save._db() as db:proof=db.execute('select status,digest from neon_v020_proposals where id=? and group_key=?',(p['id'],k)).fetchone()
  actual=self.save.read(k)['snapshot']
  if not proof or proof['status']!='committed' or proof['digest']!=p['digest'] or actual['world']['scene_status']!='active' or actual['roles'].get(cid,{}).get('status')!='active':raise ValueError('没有匹配的数据库开场回执，未宣称开场成功。')
  s['status']='started';s['queue'].pop(actor,None);self.write(k,s)
  return INTRO if first else cid+'号已从安全接洽点加入。之前的行动和成果保留；从这一轮起参与你自己的行动。'
 @serialized
 def flush(self,k):
  s=self.read(k)
  if not s or s.get('version')!=2 or not s.get('queue') or self.save.gate_state(k)['paused']:return []
  e=self.dynamic.get();state=e.read(k)
  if not state or state['events'] or state['proposal']:return []
  replies=[]
  for actor,request in list(s['queue'].items()):
   if request.get('expires',0)<time.time() or request.get('epoch')!=self.save.gate_state(k)['epoch']:
    current=self.read(k);current['queue'].pop(actor,None);self.write(k,current);continue
   try:replies.append(self.arrive(k,actor,request['mid']))
   except ValueError:pass
  return replies
 @serialized
 def cancel(self,k,actor,mid):
  s=self.read(k)
  if not s or s.get('version')!=2:return '没有你的待入场请求。'
  self.own(k,actor);e=self.dynamic.get();state=e.read(k)
  if state and any(r['user']==actor for r in state['roster'].values()):return '你已经在场。想暂离可以说“暂离”，不抹掉已经发生的行动。'
  receipt=s['joins'].get(actor)
  if receipt:
   try:self.save.consent(k,actor,receipt['pid'],receipt['digest'],False)
   except ValueError:pass
  s['queue'].pop(actor,None);s['joins'].pop(actor,None);self.write(k,s)
  return '已取消你的待入场请求，可以先旁观；不会自动把你加入后续窗口。'
 def fresh_start_message(self,event,k,actor,mid):
  contract=self.directory/'opening_entry_version.json'
  if contract.exists():
   cutoff=json.loads(contract.read_text()).get('activated_at')
   raw=getattr(event.message_obj,'raw_message',None)
   stamp=raw.get('timestamp') if isinstance(raw,dict) else getattr(raw,'timestamp',None)
   if cutoff:
    try:
     if isinstance(stamp,(int,float)) or isinstance(stamp,str) and stamp.isdigit():sent=float(stamp);sent=sent/1000 if sent>1e12 else sent
     else:
      parsed=datetime.datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
      if parsed.tzinfo is None:raise ValueError('missing source timezone')
      sent=parsed.timestamp()
    except (TypeError,ValueError):raise ValueError('没取得QQ原始消息时间，不能把它当成新的开场授权。请重新发送普通文本消息。')
    if sent<float(cutoff):raise ValueError('这是升级前的旧消息，不会自动开场。请重新发一句“现在开团”。')
  chat=self.directory/'conversation.sqlite3'
  if chat.exists():
   with sqlite3.connect(chat) as c:
    exists=c.execute("select 1 from sqlite_master where type='table' and name='replies'").fetchone()
    if exists and c.execute('select 1 from replies where k=? and actor=? and mid=?',(k,actor,mid)).fetchone():raise ValueError('这条旧消息之前按聊天处理过，不沿用它开场。请发一条新的“现在开团”。')
 async def handle(self,event,k,text):
  intent=opening_intent(text)
  if intent is None and text not in ('我来了','我到场了','取消入场','我先旁观','我先不加入'):return False
  event.stop_event();actor=str(event.get_sender_id());mid=str(event.message_obj.message_id or '');hashed=hashlib.sha256(text.encode()).hexdigest()
  try:
   if not actor or actor in ('None','True','False') or not mid:raise ValueError('没有取得QQ账号或消息编号，未执行开场。')
   admin=event.is_admin() is True
   if intent!='status':
    with self.db() as c:old=c.execute('select input_hash,response from opening_messages where k=? and actor=? and mid=?',(k,actor,mid)).fetchone()
    if old:
     if old['input_hash']!=hashed:raise ValueError('同一消息编号内容不同，未重复执行。')
     await event.send(event.plain_result('这条消息已处理过，不重复执行。'+self.status_reply(k,actor,admin)));return True
    self.fresh_start_message(event,k,actor,mid)
   if intent=='status':reply=self.status_reply(k,actor,admin)
   elif intent=='start':reply=self.start(k,actor,mid,admin)
   elif k not in json.loads(self.dynamic.allow.read_text()):reply='本群尚未开放主持入口；请先核对本群授权，未执行入场。'
   elif text in ('取消入场','我先旁观','我先不加入'):reply=self.cancel(k,actor,mid)
   else:reply=self.arrive(k,actor,mid)
   if intent!='status':
    with self.db() as c:c.execute('insert or ignore into opening_messages values(?,?,?,?,?)',(k,actor,mid,hashed,reply))
  except ValueError as ex:reply=str(ex)
  except Exception:reply='开场检查暂时出错，未宣称成功。请核对当前存档或重发原话；已有回执会沿用，不重复执行。'
  await event.send(event.plain_result(reply));return True
