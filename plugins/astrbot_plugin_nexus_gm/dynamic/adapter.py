"""Explicit group opt-in, real sender/admin identity, no automatic activation."""
import asyncio,json
from ..engine import Engine as SafetyEngine
from ..enrollment_chat import explicit_consent
from ..player_flow import flow_request
from .engine import NarrativeEngine,NarrativeError
from .controller import DynamicController
class DynamicAdapter:
 def __init__(self,directory,save,context,legacy_engine,conversation):
  self.directory=directory;self.save=save;self.context=context;self.legacy=legacy_engine;self.conversation=conversation;self.engine=None;self.controller=None;self.safety=None
  self.allow=directory/'dynamic_groups.json'
  if not self.allow.exists():self.allow.write_text('[]')
 def get(self):
  if self.engine is None:self.engine=NarrativeEngine(self.directory/'narrative.sqlite3',self.save);self.controller=DynamicController(self.engine,self.context,memory=getattr(self.conversation,'public_banter_memory',None))
  return self.engine
 def active(self,key):
  return key in json.loads(self.allow.read_text()) and self.get().read(key) is not None
 def safety_command(self,text):
  return text.strip()=='暂停' or text.startswith('【暂停】') or text.lstrip('/').split(' ')[0] in ('nexusresume','nexusresumeconsent','nexusresumeapprove','nexusresumereject') or text in ('申请恢复','同意恢复','我愿意恢复','批准恢复','拒绝恢复')
 def safety_engine(self):
  if self.safety is None:self.safety=SafetyEngine(self.directory/'dynamic_safety.sqlite3',self.save)
  return self.safety
 def ensure_safety_checkpoint(self,key):
  safety=self.safety_engine();state=self.get().read(key)
  from .engine import digest
  task='dne:'+state['id']+':'+digest(state)
  with safety.db() as c:
   row=c.execute('SELECT v FROM sessions WHERE k=?',(key,)).fetchone();old=json.loads(row[0]) if row else None
   g=self.save.gate_state(key)
   reusable=old and old.get('task')==task and old.get('resume_request') and old['resume_request']['epoch']==g['epoch']
   if old and not reusable:
    old['resume_request']=None;safety.write(c,key,old,dict(kind='new_dynamic_safety_checkpoint'))
  if not reusable:
   safety.ensure_onboarding_control(key)
   with safety.db() as c:
    s=safety.load(c,key);s['task']=task;safety.write(c,key,s,dict(kind='bind_dynamic_checkpoint',task=task))
  return safety
 async def handle_safety(self,event,key,text):
  actor=str(event.get_sender_id());mid=str(event.message_obj.message_id or '');engine=self.get();engine.owner(engine.read(key),actor) if not event.is_admin() else None
  if text.strip()=='暂停' or text.startswith('【暂停】'):
   # Historical platform retries never re-pause a resumed scene.
   with self.save._db() as c:
    seen=c.execute("SELECT 1 FROM neon_v020_events WHERE group_key=? AND action='safety_pause' AND json_extract(detail,'$.pause_id')=?",(key,'dne:'+mid)).fetchone()
   if seen:return '这条暂停已经处理过；不会重复执行或改变当前恢复状态。'
   self.save.pause_group(key,actor,'dne:'+mid);self.ensure_safety_checkpoint(key)
   return '已暂停，剧情、骰点及结算都停在这里。无需解释原因；恢复需要相关玩家各自同意和管理员授权。'
  if not self.save.gate_state(key)['paused']:
   return '当前没有需要解除的暂停；不会重演行动或掷骰。'
  safety=self.ensure_safety_checkpoint(key);parts=text.lstrip('/').split();command=parts[0]
  if command=='nexusresume' or text=='申请恢复':
   r=safety.request_resume(key,actor,event.is_admin());return '仍暂停。请角色'+ '、'.join(r['labels'])+'各自说“同意恢复”，再由管理员说“批准恢复”；无需解释暂停原因。'
  with safety.db() as c:r=safety.load(c,key).get('resume_request')
  if not r:raise NarrativeError('先申请恢复，再由各人确认。')
  rid,digest=(parts[1],parts[2]) if command in ('nexusresumeconsent','nexusresumeapprove','nexusresumereject') and len(parts)==3 else (r['id'],r['digest'])
  if text in ('同意恢复','我愿意恢复') or command=='nexusresumeconsent':return safety.consent_resume(key,actor,rid,digest)
  if text=='拒绝恢复' or command=='nexusresumereject':return safety.consent_resume(key,actor,rid,digest,False)
  if text=='批准恢复' or command=='nexusresumeapprove':return safety.approve_resume(key,actor,rid,digest,event.is_admin())
  raise NarrativeError('恢复命令不完整，保持暂停。')
 async def handle(self,event,key,text):
  if key not in json.loads(self.allow.read_text()):return False
  # Safety and existing account-management commands stay with their original handlers.
  if text.startswith('/') and not self.safety_command(text):return False
  if explicit_consent(text) or any(v in text for v in ('认领','角色卡','背包','自动审核','申请入场','激活角色','恢复申请','DIY')) or any(v in text for v in ('修改','调整','更改')) and any(v in text for v in ('性格','发型','发色','瞳色','外观','昵称','爱好')):return False
  engine=self.get();s=engine.read(key)
  controls=('开始跑团','准备好了','确认这段剧情','批准剧情','批准临场裁定','核对动态成员','重建剧情审核','动态剧情状态','加入A队','加入B队','暂离','回归')
  if not s and text not in controls:return False
  event.stop_event();actor=str(event.get_sender_id());mid=str(event.message_obj.message_id or '')
  async def send(msg):await asyncio.wait_for(event.send(event.plain_result(msg)),timeout=15)
  try:
   if not mid or not actor or actor=='None':raise NarrativeError('未取得真实平台身份或消息编号。')
   if self.safety_command(text) and s:
    await send(await self.handle_safety(event,key,text));return True
   if flow_request(text) and s:
    cid=engine.owner(s,actor);waiting=[x for x,r in s['roster'].items() if not r['away'] and x not in s['done']]
    hint=('目前暂停，先保持停下；恢复要大家同意和管理员授权。' if self.save.gate_state(key)['paused'] else '先等大家各自说准备好了。' if not s['started'] else '这轮变化正在等本人确认及管理员审核。' if s['proposal'] else '你本轮已行动，等同伴：'+ '、'.join(waiting) if cid in s['done'] else '直接说你的角色想做什么、怎么做；可以拒绝委托、另提办法或先问清情况。')
    await send(hint);return True
   if text=='开始跑团':await send(engine.prepare(key,actor,event.is_admin()));return True
   if not s:raise NarrativeError('先由管理员发起开始跑团；当前没有剧情。')
   if text=='准备好了':
    await send(engine.ready(key,actor))
    suggestion=await self.controller.plan_scene(event,key)
    if suggestion:await send(suggestion)
    return True
   if text=='重建剧情审核':
    engine.rebuild_checkpoint(key,actor,event.is_admin());await send('已保留原骰点与行动，重建待审核内容；需要玩家重新确认，不沿用旧同意。');return True
   if text=='核对动态成员':await send(engine.reconcile(key,actor,event.is_admin()));return True
   if text=='确认这段剧情':await send(engine.consent(key,actor));return True
   if text in ('批准剧情','批准临场裁定'):
    await send(engine.approve(key,actor,event.is_admin(),manual_ruling=text=='批准临场裁定'))
    suggestion=await self.controller.plan_scene(event,key)
    if suggestion:await send(suggestion)
    return True
   if text=='动态剧情状态':
    v=engine.view(key,actor);await send('动态第一幕｜等待'+('、'.join(v['waiting']) or '本轮审核')+'\n目标：'+v['goal']);return True
   if text in ('加入A队','加入B队','暂离','回归'):
    await send(engine.change_participation(key,actor,away=text=='暂离',team=text[2] if text.startswith('加入') else None));return True
   if self.save.gate_state(key)['paused']:
    response=await self.conversation.reply(event,key,text,extra_context=engine.view(key,actor))
    await send(response['reply'] if response['kind']=='chat' else '保持暂停。恢复仍需相关玩家同意和管理员授权。');return True
   engine.sync(key)
   engine.auto_continue(key)
   with engine.db() as c:old=c.execute('select phase,response from requests where k=? and actor=? and mid=?',(key,actor,mid)).fetchone()
   replay=bool(old)
   if old:
    if old['phase']!='done':raise NarrativeError('原行动中断，等待核对已有证据，不重新掷骰。')
    result=json.loads(old['response'])
    if result['player_action']!=text:raise NarrativeError('消息编号与内容不一致。')
   else:
    with self.conversation.db() as c:prior_chat=c.execute('SELECT kind FROM replies WHERE k=? AND actor=? AND mid=?',(key,actor,mid)).fetchone()
    if prior_chat and prior_chat[0]=='chat':
     r=await self.conversation.reply(event,key,text,extra_context=engine.view(key,actor));await send(r['reply'] if r['kind']=='chat' else '这条先前是聊天，不把它重新当成游戏行动。');return True
    request_prefix=next((prefix for prefix in ('申请核对监控：','申请核对账户：','申请核对取芯片方式：') if text.startswith(prefix)),None)
    if request_prefix:
     if not 5<=len(text[len(request_prefix):].strip())<=200:raise NarrativeError('请说明实际取得权限或访问依据；不只填写想要的结论。')
     op={'申请核对监控：':'review_camera','申请核对账户：':'review_account','申请核对取芯片方式：':'review_locker'}[request_prefix]
     current=engine.read(key);d=dict(mode='action',operation=op,node='camera' if op=='review_camera' else 'account' if op=='review_account' else 'locker',_round=current['round'],_epoch=self.save.gate_state(key)['epoch'])
    else:d=await self.controller.interpret(event,key,text)
    if d['mode']=='scene':
     description=await self.controller.narrate(event,key,text,dict(mode='scene',node=d['node'],check=None,effects=[],pending_approval=False))
     if description:
      await send(description)
      if hasattr(self.conversation,'remember_scene'):self.conversation.remember_scene(key,actor,'scene:'+mid,text,description)
     else:await send('刚才没接上你的话。你再问一次，我不会把它当成新的行动或重掷。')
     return True
    if d['mode']=='chat':
     r=await self.conversation.reply(event,key,text,extra_context=engine.view(key,actor));await send(r['reply'] if r['kind']=='chat' else '你是嘴上说说，还是准备真这么干？');return True
    if d['mode'] in ('clarify','review'):await send(d['question'] or '请再说明行动对象、目的和准备怎么做；还没有掷骰。');return True
    declared=False
    if d['operation'] in ('unlock','buy_key'):
     await send(engine.declaration(d['operation']));declared=True
    result=engine.action(key,actor,mid,text,d['operation'],d['node'],declared,expected_round=d['_round'],expected_epoch=d['_epoch'])
   check=result['check']
   if check:
    await send(f"{result['cid']}：1d10={check['die']} + {check['attribute']}{check['attribute_value']} + {check['skill']}{check['skill_value']} + 修正({check['modifier']}) = {check['total']} / DV{check['dv']}，"+('成功。' if check['success'] else '失败。')+' 后果待本轮确认审核，不会自动扣款或发奖励。')
   elif replay:await send('这是上次已保存的行动记录，没有再次执行：'+result['player_action'])
   if result.get('disclosure'):await send('你实际查看到的芯片内容：'+result['disclosure'])
   checkpoint=engine.checkpoint(key)
   if not replay:
    description=await self.controller.narrate(event,key,text,result)
    if description:
     await send(description)
     if hasattr(self.conversation,'remember_scene'):self.conversation.remember_scene(key,actor,'scene:'+mid,text,description)
    elif not check and checkpoint['status']!='pending':await send('这次的描述没接上。刚才的行动会保留，不会重做。')
   if checkpoint['status']=='pending' and engine.auto_continue(key):return True
   if checkpoint['status']=='pending':
    current=engine.read(key);pending=current['pending_state'];changes=[]
    if pending['alert']!=current['alert']:changes.append('警戒：'+str(current['alert'])+' → '+str(pending['alert']))
    if pending['goal']!=current['goal']:changes.append('目标：'+pending['goal'])
    if pending['npc_attitudes']['mira']!=current['npc_attitudes']['mira']:changes.append('米拉：'+pending['npc_attitudes']['mira'])
    for item in engine.read(key)['events']:
     if 'key_purchase_pending' in item['effects']:changes.append(item['cid']+'号钥匙交易：扣100€$并取得访问权')
     if item['operation'].startswith('review_'):changes.append('须管理员临场裁定：'+item['player_action'][:200])
     if item['operation']=='move':changes.append(item['cid']+'号位置：'+pending['roster'][item['cid']]['position'])
     if 'chip_access' in item['effects']:changes.append(item['cid']+'号获得已解锁储物柜的芯片访问权')
    if changes:await send('这段会有这些变化，先看一眼：'+ '；'.join(changes))
    await send('这段没问题的话，各自说“确认这段剧情”；管理员再说“批准剧情”。')
  except asyncio.CancelledError:raise
  except Exception as ex:
   await send('先停在这里：'+(str(ex) if isinstance(ex,(NarrativeError,ValueError)) else '模型或投递暂时不可用；已完成的骰点会保留，不重新执行。'))
  return True
