import sys,types,importlib,unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
import test_engine
class Adapter(test_engine.Flow):
 def test_direct_send_failure_replay_home_bypass(self):
  import asyncio
  modules={}
  for name in ('astrbot','astrbot.api','astrbot.api.event','astrbot.api.star','astrbot.core','astrbot.core.utils','astrbot.core.utils.astrbot_path'):
   modules[name]=types.ModuleType(name)
  class Filters:
   class EventMessageType:GROUP_MESSAGE=1
   def command(self,*a,**k):return lambda f:f
   def event_message_type(self,*a,**k):return lambda f:f
  modules['astrbot.api.event'].AstrMessageEvent=object;modules['astrbot.api.event'].filter=Filters()
  modules['astrbot.api.star'].Star=object;modules['astrbot.api.star'].Context=object
  modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:self.tmp.name
  with patch.dict(sys.modules,modules):
   sys.modules.pop('astrbot_plugin_nexus_gm.main',None)
   m=importlib.import_module('astrbot_plugin_nexus_gm.main')
  plugin=object.__new__(m.NexusGM);plugin.engine=self.e;plugin.allowfile=Path(self.tmp.name)/'allowed.json';plugin.allowfile.write_text(json.dumps([self.k]));plugin.get=lambda ev:self.k
  class Semantic:
   locks={}
   async def analyze(self,*args):return dict(mode='check',method='交涉'),None,None
  plugin.semantic=Semantic()
  class Event:
   message_str='我要交涉';message_obj=types.SimpleNamespace(message_id='adapter1');stopped=False;sent=[];fail=False
   def get_platform_name(self):return 'qq_official'
   def get_group_id(self):return 'isolated'
   def get_sender_id(self):return 'u03'
   def stop_event(self):self.stopped=True
   def plain_result(self,t):return t
   async def send(self,t):
    if self.fail and '1d10=10' in t:raise OSError('模拟发送失败')
    self.sent.append(t)
  ev=Event();ev.sent=[];ev.fail=True
  asyncio.run(plugin.natural_action(ev));self.assertTrue(ev.stopped);self.assertTrue(any('难度14' in t for t in ev.sent))
  with self.e.db() as c:s=self.e.load(c,self.k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),1)
  ev.fail=False;ev.sent=[];asyncio.run(plugin.natural_action(ev));self.assertIn('1d10=10',ev.sent[0])
  for command in ('nexusopen','/nexusopen','neonapprove abc hash','/neonapprove abc hash','nexusresume','/nexusresumeconsent id hash','nexusresumeapprove id hash','neonwhoami','/neonwhoami','neonclaim 10 01 小号','/neontemplate 01'):
   cmd=Event();cmd.message_str=command;cmd.sent=[];asyncio.run(plugin.natural_action(cmd));self.assertFalse(cmd.stopped);self.assertEqual(cmd.sent,[])
  home=Event();home.get_group_id=lambda:'HOME';home.sent=[];asyncio.run(plugin.natural_action(home));self.assertFalse(home.stopped);self.assertEqual(home.sent,[])

 def test_slow_narration_does_not_block_next_player_or_pause(self):
  self.test_direct_send_failure_replay_home_bypass()
  # Re-open a fresh isolated engine for actual async handler scheduling.
  import asyncio
  from astrbot_plugin_nexus_gm.engine import Engine
  stubs={name:types.ModuleType(name) for name in ('astrbot','astrbot.api','astrbot.api.event','astrbot.api.star','astrbot.core','astrbot.core.utils','astrbot.core.utils.astrbot_path')}
  class F:
   class EventMessageType:GROUP_MESSAGE=1
   def command(self,*a,**kw):return lambda fn:fn
   def event_message_type(self,*a,**kw):return lambda fn:fn
  stubs['astrbot.api.event'].AstrMessageEvent=object;stubs['astrbot.api.event'].filter=F()
  stubs['astrbot.api.star'].Star=object;stubs['astrbot.api.star'].Context=object
  stubs['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:self.tmp.name
  with patch.dict(sys.modules,stubs):m=importlib.import_module('astrbot_plugin_nexus_gm.main')
  engine=Engine(Path(self.tmp.name)/'lock-test.db',self.save,rand=lambda n:8);engine.open(self.k,True)
  plugin=object.__new__(m.NexusGM);plugin.engine=engine;plugin.allowfile=self.root_allow=Path(self.tmp.name)/'lock-allow.json';plugin.allowfile.write_text(json.dumps([self.k]));plugin.get=lambda ev:self.k;plugin.engine_state_stage=lambda key:1
  class Event:
   def __init__(self,text,mid):self.message_str=text;self.message_obj=types.SimpleNamespace(message_id=mid);self.sent=[];self.stopped=False
   def get_platform_name(self):return 'qq_official'
   def get_group_id(self):return 'isolated'
   def get_sender_id(self):return 'u03'
   def stop_event(self):self.stopped=True
   def plain_result(self,t):return t
   async def send(self,t):self.sent.append(t)
  async def scenario():
   started=asyncio.Event();release=asyncio.Event()
   class Semantic:
    locks={}
    async def analyze(self,ev,*args):
     return (dict(mode='observe',method='') if '回顾' in ev.message_str else dict(mode='check',method='交涉')),object(),object()
    async def narrate(self,*args,**kwargs):started.set();await release.wait();return None
   plugin.semantic=Semantic();first=Event('我要交涉','one');task=asyncio.create_task(plugin.natural_action(first));await started.wait()
   second=Event('我回顾场景','two');await asyncio.wait_for(plugin.natural_action(second),.5)
   self.assertTrue(any('无需检定' in t for t in second.sent))
   release.set();await task
   # A pause bypasses an occupied group lock instead of waiting for model completion.
   lock=plugin.semantic.locks[self.k]
   async with lock:
    pause=Event('暂停','pause');await asyncio.wait_for(plugin.natural_action(pause),.5)
    self.assertTrue(any('已暂停' in t for t in pause.sent))
    missing=Event('【暂停】'+'不需要解释'*500,'');await asyncio.wait_for(plugin.natural_action(missing),.5)
    self.assertTrue(any('已暂停' in t for t in missing.sent))
    with engine.db() as conn:state=engine.load(conn,self.k)
    self.assertEqual(state['log'][-1]['text'],'【暂停】')
  asyncio.run(scenario())
 def test_late_model_after_pause_and_resume_cannot_apply(self):
  import asyncio
  from astrbot_plugin_nexus_gm.engine import Engine
  stubs={name:types.ModuleType(name) for name in ('astrbot','astrbot.api','astrbot.api.event','astrbot.api.star','astrbot.core','astrbot.core.utils','astrbot.core.utils.astrbot_path')}
  class F:
   class EventMessageType:GROUP_MESSAGE=1
   def command(self,*a,**kw):return lambda fn:fn
   def event_message_type(self,*a,**kw):return lambda fn:fn
  stubs['astrbot.api.event'].AstrMessageEvent=object;stubs['astrbot.api.event'].filter=F();stubs['astrbot.api.star'].Star=object;stubs['astrbot.api.star'].Context=object;stubs['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:self.tmp.name
  with patch.dict(sys.modules,stubs):m=importlib.import_module('astrbot_plugin_nexus_gm.main')
  engine=Engine(Path(self.tmp.name)/'late-model.db',self.save,rand=lambda n:8);engine.open(self.k,True)
  plugin=object.__new__(m.NexusGM);plugin.engine=engine;plugin.allowfile=Path(self.tmp.name)/'late-allow.json';plugin.allowfile.write_text(json.dumps([self.k]));plugin.get=lambda ev:self.k
  class Event:
   def __init__(self,text,mid):self.message_str=text;self.message_obj=types.SimpleNamespace(message_id=mid);self.sent=[]
   def get_platform_name(self):return 'qq_official'
   def get_group_id(self):return 'isolated'
   def get_sender_id(self):return 'u03'
   def stop_event(self):pass
   def plain_result(self,t):return t
   async def send(self,t):self.sent.append(t)
  async def scenario():
   started=asyncio.Event();release=asyncio.Event()
   class Semantic:
    locks={}
    async def analyze(self,*args):started.set();await release.wait();return dict(mode='check',method='交涉'),None,None
   plugin.semantic=Semantic();old=Event('我和负责人闲聊','old');work=asyncio.create_task(plugin.natural_action(old));await started.wait()
   pause=Event('【暂停】','pause');await plugin.natural_action(pause)
   request=engine.request_resume(self.k,'u03');engine.consent_resume(self.k,'u03',request['id'],request['digest']);engine.approve_resume(self.k,'admin',request['id'],request['digest'],True)
   release.set();await work
   self.assertFalse(any('1d10=' in text for text in old.sent))
   with engine.db() as c:state=engine.load(c,self.k)
   self.assertEqual(sum(bool(x.get('check')) for x in state['log']),0)
  asyncio.run(scenario())
