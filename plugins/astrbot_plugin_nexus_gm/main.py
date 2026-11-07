import importlib,json,unicodedata
from pathlib import Path
from astrbot.api.event import AstrMessageEvent,filter
from astrbot.api.star import Star,Context
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from .engine import Engine,GMError,SCENES
from .dynamic.adapter import DynamicAdapter
from .simple_open import SimpleOpen
from .voice import human_read_reply
from .presentation import player_text
from .conversation import Conversation,social_hint
from .public_guide import GUIDE,guide_request
from .public_places import places_query
from .player_flow import flow_request,player_flow
from .semantic import SemanticController
from .desk import NaturalDesk
from .enrollment_chat import EnrollmentChat,management_language,explicit_consent,name_selection
import asyncio,secrets
from contextlib import asynccontextmanager

@asynccontextmanager
async def bounded_group_lock(lock):
 try:await asyncio.wait_for(lock.acquire(),timeout=30)
 except asyncio.TimeoutError as ex:raise GMError("上一条行动仍在处理中，本条尚未执行，请稍后重发") from ex
 try:yield
 finally:lock.release()


class NexusGM(Star):
 def __init__(self,context:Context):
  super().__init__(context)
  root=Path(get_astrbot_plugin_data_path());directory=root/'astrbot_plugin_nexus_gm';directory.mkdir(exist_ok=True)
  self.allowfile=directory/'allowed_groups.json'
  if not self.allowfile.exists():self.allowfile.write_text('[]')
  self.engine=None;self.root=root;self.directory=directory;self.context=context;self.semantic=None
  # Eager initialization adopts legacy pause before the message bus starts.
  safe=importlib.import_module('data.plugins.astrbot_plugin_neon_save.safe')
  if not hasattr(safe.SafeStore,'gate_state'):raise RuntimeError('请先安装配套Neon存档0.2.4-rc1或更高安全闸门版本')
  self.engine=Engine(self.directory/'rehearsal.sqlite3',safe.SafeStore(self.root/'astrbot_plugin_neon_save'))
  if not hasattr(self.engine.save,'request_enrollment'):raise RuntimeError('请先配套安装存档0.2.5-rc1')
  self.enrollment=EnrollmentChat(self.engine.save,self.context)
  self.conversation=Conversation(self.directory,self.engine.save,self.context,self.enrollment.templates)
  self.enrollfile=directory/'enrollment_groups.json'
  if not self.enrollfile.exists():self.enrollfile.write_text(self.allowfile.read_text())
  self.dynamic=DynamicAdapter(self.directory,self.engine.save,self.context,self.engine,self.conversation)
  self.simple_open=SimpleOpen(self.directory,self.engine.save,self.dynamic)
 def get(self,event):
  if event.get_platform_name()!='qq_official' or not event.get_group_id():raise GMError('仅限QQ官方群')
  key='qq_official:'+str(event.get_group_id())
  if key not in json.loads(self.allowfile.read_text()):raise GMError('本群未列入演练白名单，禁止启用')
  if self.engine is None:
   safe=importlib.import_module('data.plugins.astrbot_plugin_neon_save.safe')
   self.engine=Engine(self.directory/'rehearsal.sqlite3',safe.SafeStore(self.root/'astrbot_plugin_neon_save'))
  return key
 @filter.command('nexusopen')
 async def open(self,event:AstrMessageEvent):
  try:
   key=self.get(event);yield event.plain_result(self.engine.open(key,event.is_admin()))
  except Exception as ex:yield event.plain_result('未开启：'+str(ex))
 @filter.command('nexusstatus')
 async def status(self,event:AstrMessageEvent):
  try:
   key=self.get(event);s=self.engine.view(key)
   yield event.plain_result(f"公开演练｜{s['scene']}｜窗口{s['round']}｜暂停：{s['paused']}｜等待角色{','.join(s['waiting'])}\n"+'\n'.join(x['result'] for x in s['recap']))
  except Exception as ex:yield event.plain_result('查询失败：'+str(ex))
 @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE,priority=1000)
 async def natural_action(self,event:AstrMessageEvent):
  # Dedicated rehearsal group only. Stop the ordinary LLM pipeline before it can invent results.
  if event.get_platform_name()!='qq_official' or not event.get_group_id():return
  key='qq_official:'+str(event.get_group_id())
  text=event.message_str.strip()
  if hasattr(self,'simple_open') and await self.simple_open.handle(event,key,text):return
  if hasattr(self,'dynamic') and self.dynamic.active(key) and (self.dynamic.safety_command(text) or flow_request(text)):
   if await self.dynamic.handle(event,key,text):
    if hasattr(self,'simple_open'):
     for joined in self.simple_open.flush(key):await event.send(event.plain_result(joined))
    return
  if hasattr(self,'enrollment') and flow_request(text):
   with self.engine.save._db() as c:known=c.execute('SELECT 1 FROM saves WHERE group_key=?',(key,)).fetchone()
   if known:
    entry=key in json.loads(self.enrollfile.read_text());story=key in json.loads(self.allowfile.read_text())
    message=player_flow(self.engine.save,self.engine,key,str(event.get_sender_id()),self.enrollment.templates,entry,story)
    event.stop_event();await event.send(event.plain_result(message));return
  if hasattr(self,'enrollment'):
   place=places_query(text,self.enrollment.templates)
   if place is not None:
    with self.engine.save._db() as c:known=c.execute('SELECT 1 FROM saves WHERE group_key=?',(key,)).fetchone()
    if known:
     event.stop_event();await event.send(event.plain_result(place));return
  if guide_request(text):
   # Public reading is allowed in existing groups without opening enrollment or story.
   with self.engine.save._db() as c:known=c.execute('SELECT 1 FROM saves WHERE group_key=?',(key,)).fetchone()
   if known:
    event.stop_event();await event.send(event.plain_result(GUIDE));return
  enroll_allowed=key in json.loads((self.enrollfile if hasattr(self,'enrollfile') else self.allowfile).read_text())
  if hasattr(self,'enrollment'):
   with self.engine.save._db() as c:known=c.execute('SELECT 1 FROM saves WHERE group_key=?',(key,)).fetchone()
   if known:
    public=self.enrollment.public_query(key,str(event.get_sender_id()),text,enroll_allowed)
    if public is not None:
     event.stop_event();await event.send(event.plain_result(human_read_reply(public)));return
  if hasattr(self,'dynamic') and await self.dynamic.handle(event,key,text):
   if hasattr(self,'simple_open'):
    for joined in self.simple_open.flush(key):await event.send(event.plain_result(joined))
   return
  if key not in json.loads(self.allowfile.read_text()) and not enroll_allowed:
   # Never let management language fall through to a model which can invent a card/JSON.
   if explicit_consent(text) or name_selection(text) or NaturalDesk.parse(text) is not None or any(x in text for x in ('认领','批准','入团','自动审核','角色登记','申请结算','申请入场','激活角色')) or (management_language(text) and any(x in text for x in ('修改','调整','更改'))):
    event.stop_event()
    await event.send(event.plain_result('本群尚未开放程序化角色认领，未创建提案、未记录同意、未修改存档。请在霓虹余烬测试群验收；AI文字角色卡不代表登记成功。'))
   elif hasattr(self,'conversation'):
    # Existing saved QQ groups may chat read-only, without enabling enrollment/story.
    with self.engine.save._db() as c:known=c.execute('SELECT 1 FROM saves WHERE group_key=?',(key,)).fetchone()
    if known:
     event.stop_event()
     response=await self.conversation.reply(event,key,text)
     await event.send(event.plain_result(response['reply'] if response['kind']=='chat' else '本群还没有开放剧情。可以先聊天；认领和开场需要管理员另外安排。'))
   return
  if text and text.split()[0].lstrip('/') in ('nexusclaimauto','nexusresume','nexusresumeconsent','nexusresumeapprove','nexusresumereject','nexusopen','nexusstatus','nexusproposal','neonwhoami','neontemplate','neonclaim','neonpropose','neonroll','neonstatus','neoncard','neonview','neonconsent','neonapprove','neonreject','neonhistory','neonaudit','neonbackup','neonexport'):return
  event.stop_event()
  try:
   actor=str(event.get_sender_id());mid=str(event.message_obj.message_id or '')
   pausing=text.strip()=='暂停' or text.strip().startswith('【暂停】')
   if pausing:
    text='【暂停】'
    if not mid:mid='safety:'+secrets.token_hex(12)
   elif not mid or len(text)>2000:raise GMError('消息ID缺失或行动过长')
   if pausing and hasattr(self,'enrollment') and enroll_allowed:
    paused=self.engine.onboarding_pause(key,actor,mid)
    if self.semantic is not None:self.semantic.cancel_group(key)
    await event.send(event.plain_result(paused['response']));return
   if hasattr(self,'conversation') and not pausing and social_hint(text):
    response=await self.conversation.reply(event,key,text)
    if response['kind']=='chat':
     await event.send(event.plain_result(response['reply']));return
   if hasattr(self,'enrollment') and enroll_allowed and not pausing:
    try:
     if await self.enrollment.dispatch(event,key,text):return
    except Exception as ex:
     # A failed QQ send may follow a successful commit. Recover the real receipt
     # instead of claiming that nothing was written or rerunning the operation.
     try:receipt=self.engine.save.enrollment_replay(key,actor,mid,unicodedata.normalize('NFKC',text).strip())
     except Exception:receipt=None
     if receipt:message=self.enrollment.render(key,receipt)
     elif isinstance(ex,ValueError):message=str(ex)+'。请核对或调整后再说明。'
     elif isinstance(ex,asyncio.TimeoutError):message='角色语言分析超时，未生成新的认领提案。请稍后重试；不会补造同意或直接登记。'
     else:message='暂时无法核实本次申请或投递结果，请稍后重发同一消息查询；不会因此直接重复登记。'
     await event.send(event.plain_result(message));return
   if hasattr(self,'conversation') and not pausing:
    response=await self.conversation.reply(event,key,text)
    if response['kind']=='chat':
     await event.send(event.plain_result(response['reply']));return
   if hasattr(self,'dynamic') and self.dynamic.active(key):
    await event.send(event.plain_result('你是在调整角色设定还是进行场景行动？请再说明一下；没有执行新的行动。'));return
   if enroll_allowed and key not in json.loads(self.allowfile.read_text()):
    await event.send(event.plain_result('这里还没开场。我们可以聊天，也可以先选角色；开始冒险需要管理员先安排安全入场。'));return
   self.get(event)
   if hasattr(self,'enrollment'):
    with self.engine.db() as c:control=c.execute('SELECT v FROM sessions WHERE k=?',(key,)).fetchone()
    if control and json.loads(control[0]).get('onboarding_only'):
     await event.send(event.plain_result('当前只有登记安全控制，剧情尚未开场；不会进行检定或推进。'));return
   if not hasattr(self,'enrollment') and NaturalDesk.parse(text) is not None:
    if not hasattr(self,"desk"):
     safe=importlib.import_module("data.plugins.astrbot_plugin_neon_save.safe")
     self.desk=NaturalDesk(self.engine.save,self.directory,safe.TEMPLATES,self.engine)
    managed=self.desk.handle(key,actor,mid,text,event.is_admin())
    await event.send(event.plain_result(managed));return
   if self.semantic is None:self.semantic=SemanticController(self.engine,self.context)
   # Safety pause must not wait behind a model request holding the group lock.
   if pausing:
    paused=self.engine.natural(key,actor,mid,'【暂停】',intent_override='pause')
    if self.engine.is_paused(key) and hasattr(self.semantic,'cancel_group'):self.semantic.cancel_group(key)
    await event.send(event.plain_result(paused['response']));return
   if self.engine.is_paused(key):
    await event.send(event.plain_result('安全暂停仍有效；不会因聊天中的继续而恢复。请先/nexusresume申请，再由相关玩家确认及管理员批准。'));return
   request_epoch=self.engine.epoch(key)
   narration_job=None
   async with bounded_group_lock(self.semantic.locks.setdefault(key,asyncio.Lock())):
    prepared=self.engine.cached(key,actor,mid,text)
    provider=persona=None
    if prepared is None:
     # A plain safety pause never waits for a model or retrieval service.
     if text.strip().strip('。！!') in ('确认结束','【确认结束】','结束本轮行动','我确认结束本轮行动'):
      intent='end'
     else:
      await event.send(event.plain_result('让我结合眼前的情况想一想；稍等，我还没有掷骰。'))
      decision,provider,persona=await self.semantic.analyze(event,key,text)
      self.engine.assert_epoch(key,request_epoch)
      mode=decision['mode']
      if mode in ('clarify','review'):
       await event.send(event.plain_result(('需要管理员核对：' if mode=='review' else '请补充：')+decision['question']+'（未掷骰、未推进）'));return
      if mode=='observe':
       public=self.engine.view(key)
       await event.send(event.plain_result('已公开场景：'+public['scene']+'。'+SCENES[self.engine_state_stage(key)][1]+'（查看公开信息，无需检定，未推进）'));return
      intent=decision['method'] if mode=='check' else mode
     prepared=self.engine.natural(key,actor,mid,text,intent_override=intent,expected_epoch=request_epoch)
    await event.send(event.plain_result(self.player_message(prepared['response'])))
    if prepared['phase']=='prepared':
     result=self.engine.natural(key,actor,prepared['mid'],text,finish=True,expected_epoch=request_epoch)
     authoritative=result['response'].split('\n',1)[-1]
     await event.send(event.plain_result(self.player_message(authoritative)))
     if provider is not None:narration_job=(provider,persona,prepared['mid'],authoritative)
   # Narrative generation must not hold the group action lock.
   if narration_job:
    self.engine.assert_epoch(key,request_epoch)
    provider,persona,request_mid,authoritative=narration_job
    description=await self.semantic.narrate(provider,persona,text,authoritative,key=key)
    self.engine.assert_epoch(key,request_epoch)
    if description:
     self.engine.cache_narration(key,actor,request_mid,description)
     await event.send(event.plain_result(description))
    # The trusted result was already delivered; a missing flourish needs no extra system notice.
  except asyncio.CancelledError:
   if self.engine.is_paused(key) or ('request_epoch' in locals() and self.engine.epoch(key)!=request_epoch):return
   raise
  except Exception as ex:
   await event.send(event.plain_result('这次没能完成处理：'+(str(ex) or type(ex).__name__)+'。你可以重发刚才的话；已经掷过的骰子和完成的行动会沿用原结果。'))
  finally:event.stop_event()
 def player_message(self,text):
  templates=getattr(getattr(self,'enrollment',None),'templates',{})
  return player_text(text,{cid:card['name'] for cid,card in templates.items()})
 def engine_state_stage(self,key):
  with self.engine.db() as c:return self.engine.load(c,key)['stage']
 @filter.command('nexusproposal')
 async def proposal(self,event:AstrMessageEvent):
  try:
   key=self.get(event);p=self.engine.proposal(key,str(event.get_sender_id()),event.is_admin())
   yield event.plain_result(f"仅生成待审核提案，未结算。ID {p['id']}\n摘要 {p['digest']}\n请逐人用/neonview核对角色及world，再本人/neonconsent，管理员/neonapprove。")
  except Exception as ex:yield event.plain_result('未生成：'+str(ex))

 def control_key(self,event):
  if not hasattr(self,'enrollfile'):return self.get(event)
  if event.get_platform_name()!='qq_official' or not event.get_group_id():raise GMError('仅限QQ官方群')
  key='qq_official:'+str(event.get_group_id())
  if key not in set(json.loads(self.allowfile.read_text()))|set(json.loads(self.enrollfile.read_text())):raise GMError('本群未授权')
  return key
 @filter.command('nexusresume')
 async def resume_request(self,event:AstrMessageEvent):
  try:
   key=self.control_key(event)
   if hasattr(self,'enrollment') and self.engine.save.gate_state(key)['paused']:self.engine.ensure_onboarding_control(key)
   r=self.engine.request_resume(key,str(event.get_sender_id()),event.is_admin())
   yield event.plain_result(f"恢复申请 {r['id']}\n摘要 {r['digest']}\n恢复点：场景{('未开场登记控制' if r['round']==0 else SCENES[r['stage']][0])}，窗口{r['round']}，存档版本{r['revision']}。恢复本身不掷骰，旧在途请求失效。\n仍暂停，需角色{'、'.join(r['labels'])}分别本人确认，无需说明暂停原因。\n本人：/nexusresumeconsent {r['id']} {r['digest']}\n管理员：/nexusresumeapprove {r['id']} {r['digest']}\n拒绝：/nexusresumereject {r['id']} {r['digest']}\n30分钟有效，新的暂停会使本申请失效。")
  except Exception as ex:yield event.plain_result('未申请恢复：'+str(ex))
 @filter.command('nexusresumeconsent')
 async def resume_consent(self,event:AstrMessageEvent,rid:str,digest:str):
  try:
   key=self.control_key(event);yield event.plain_result(self.engine.consent_resume(key,str(event.get_sender_id()),rid,digest))
  except Exception as ex:yield event.plain_result('未确认恢复：'+str(ex))
 @filter.command('nexusresumereject')
 async def resume_reject(self,event:AstrMessageEvent,rid:str,digest:str):
  try:
   key=self.control_key(event);yield event.plain_result(self.engine.consent_resume(key,str(event.get_sender_id()),rid,digest,False))
  except Exception as ex:yield event.plain_result('未处理恢复拒绝：'+str(ex))
 @filter.command('nexusresumeapprove')
 async def resume_approve(self,event:AstrMessageEvent,rid:str,digest:str):
  try:
   key=self.control_key(event);yield event.plain_result(self.engine.approve_resume(key,str(event.get_sender_id()),rid,digest,event.is_admin()))
  except Exception as ex:yield event.plain_result('未授权恢复：'+str(ex))

 @filter.command('nexusclaimauto')
 async def enrollment_auto(self,event:AstrMessageEvent,mode:str):
  try:
   if event.get_platform_name()!='qq_official' or not event.get_group_id():raise GMError('仅限QQ官方群')
   key='qq_official:'+str(event.get_group_id())
   if key not in json.loads(self.enrollfile.read_text()):raise GMError('本群未授权角色认领，不可启用自动审核')
   if mode not in ('on','off','status'):raise GMError('可用选项为on/off/status')
   if not event.is_admin():raise GMError('只有AstrBot管理员可以操作开关')
   if mode=='status':p=self.engine.save.enrollment_policy(key)
   else:p=self.engine.save.set_enrollment_auto(key,str(event.get_sender_id()),mode=='on',True,str(event.message_obj.message_id),'nexusclaimauto '+mode)
   yield event.plain_result('本群角色自动审核：'+('开启' if p['enabled'] else '关闭')+'。仅限角色认领和DIY，不含其他变更。')
  except Exception as ex:yield event.plain_result('未更改角色审核设置：'+str(ex))
