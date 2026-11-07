import asyncio,importlib,json,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_nexus_gm.desk import NaturalDesk

class ChatAdapter(unittest.TestCase):
 def test_real_sender_admin_routing_and_home_bypass_without_model(self):
  modules={n:types.ModuleType(n) for n in ('astrbot','astrbot.api','astrbot.api.event','astrbot.api.star','astrbot.core','astrbot.core.utils','astrbot.core.utils.astrbot_path')}
  class Filters:
   class EventMessageType:GROUP_MESSAGE=1
   def command(self,*a,**k):return lambda f:f
   def event_message_type(self,*a,**k):return lambda f:f
  modules['astrbot.api.event'].AstrMessageEvent=object;modules['astrbot.api.event'].filter=Filters();modules['astrbot.api.star'].Star=object;modules['astrbot.api.star'].Context=object;modules['astrbot.core.utils.astrbot_path'].get_astrbot_plugin_data_path=lambda:None
  with patch.dict(sys.modules,modules):
   sys.modules.pop('astrbot_plugin_nexus_gm.main',None);m=importlib.import_module('astrbot_plugin_nexus_gm.main')
  with tempfile.TemporaryDirectory() as tmp:
   store=SafeStore(Path(tmp)/'save');key='qq_official:test';store.init(key,'a');plug=object.__new__(m.NexusGM);plug.engine=types.SimpleNamespace(save=store);plug.directory=Path(tmp);plug.desk=NaturalDesk(store,tmp,TEMPLATES);plug.get=lambda e:key;plug.allowfile=Path(tmp)/'allow.json';plug.allowfile.write_text(json.dumps([key]))
   class Event:
    def __init__(self,text,actor='p',admin=False,mid='m',group='test'):self.message_str=text;self.actor=actor;self.admin=admin;self.message_obj=types.SimpleNamespace(message_id=mid);self.group=group;self.sent=[];self.stopped=False
    def get_platform_name(self):return 'qq_official'
    def get_group_id(self):return self.group
    def get_sender_id(self):return self.actor
    def is_admin(self):return self.admin
    def stop_event(self):self.stopped=True
    def plain_result(self,s):return s
    async def send(self,s):self.sent.append(s)
   def send(t,u='p',a=False,mid='m',g='test'):
    e=Event(t,u,a,mid,g);asyncio.run(plug.natural_action(e));return e
   self.assertIn('认领申请',send('我想认领03号扳手').sent[0]);self.assertFalse(hasattr(plug,'semantic'))
   self.assertIn('未记录同意',send('确认','outsider',mid='o').sent[0]);send('确认',mid='yes')
   self.assertIn('只有AstrBot管理员',send('批准03号入团','p',mid='bad').sent[0]);self.assertIn('已审核入团',send('批准03号入团','admin',True,'ok').sent[0]);self.assertFalse(hasattr(plug,'semantic'))
   home=send('认领02号','p2',mid='home',g='HOME');self.assertTrue(home.stopped);self.assertIn('未开放',home.sent[0])
