import asyncio,json,tempfile,types,unittest
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.enrollment_chat import EnrollmentChat
from astrbot_plugin_nexus_gm.body_profile import parse_body
from astrbot_plugin_nexus_gm.conversation import Conversation
class Event:
 def __init__(self,actor,mid):self.actor=actor;self.message_obj=types.SimpleNamespace(message_id=mid);self.unified_msg_origin='isolated'
 def get_sender_id(self):return self.actor
 def is_admin(self):return False
class BodyProfile(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(self.tmp.name);self.k='qq_official:body-chat';self.s.init(self.k,'admin');self.s.set_enrollment_auto(self.k,'admin',True,True);p=self.s.request_enrollment(self.k,'owner','claim','03',{});self.s.confirm_enrollment(self.k,'owner','yes',p['id'],p['digest']);self.chat=EnrollmentChat(self.s);self.i=0
 async def say(self,text,actor='owner'):
  self.i+=1;return await self.chat.handle(Event(actor,str(self.i)),self.k,text)
 async def test_natural_body_confirmation_private_card_and_model_projection(self):
  before=self.s.read(self.k)['snapshot'];preview=await self.say('我想修改身体外观，我的角色已成年，生殖器外观：有阴茎')
  self.assertNotIn('阴茎',preview);self.assertIn('默认私密',preview);self.assertEqual(self.s.character_profile(self.k,'03')['details'],{})
  answer=await self.say('确认');self.assertIn('已保存',answer);self.assertEqual(self.s.read(self.k)['snapshot'],before)
  card=await self.say('我的角色卡');self.assertNotIn('阴茎',card);self.assertNotIn('身体',card)
  c=Conversation(self.tmp.name,self.s,None,self.chat.templates);self.assertNotIn('阴茎',json.dumps(c.public_context(self.k,'owner'),ensure_ascii=False))
 async def test_publish_hide_are_confirmed_separately(self):
  await self.say('我想修改身体外观，我的角色已成年，生殖器外观：有阳具');await self.say('确认')
  await self.say('公开我的身体外观');self.assertNotIn('阳具',await self.say('我的角色卡'));await self.say('确认');self.assertIn('阳具',await self.say('我的角色卡'))
  await self.say('隐藏我的身体外观');await self.say('确认');self.assertNotIn('阳具',await self.say('我的角色卡'))
 async def test_third_party_cannot_confirm_or_modify(self):
  await self.say('我想修改身体外观，我的角色已成年，生殖器外观：有阴茎')
  with self.assertRaises(ValueError):await self.say('确认','intruder')
  with self.assertRaises(ValueError):await self.say('修改03号身体外观，角色已成年，生殖器外观：有外阴','intruder')
 async def test_adult_gate_and_functional_implants(self):
  for text in ('修改身体外观，生殖器外观：有阴茎','修改身体外观，角色未成年，生殖器外观：有阴茎','修改身体外观，角色已成年，生殖器外观：电击功能的阴茎'):
   with self.assertRaises(ValueError):await self.say(text)
 async def test_claim_path_remains_pending_and_private(self):
  self.s.init('qq_official:new','admin');self.s.set_enrollment_auto('qq_official:new','admin',True,True)
  text='我想认领03号，我的角色已成年，生殖器外观：有阴茎'
  answer=await self.chat.handle(Event('other','new-claim'),'qq_official:new',text);self.assertNotIn('阴茎',answer)
  await self.chat.handle(Event('other','new-yes'),'qq_official:new','确认');self.assertEqual(self.s.read('qq_official:new')['snapshot']['roles']['03']['status'],'pending')
 def test_plain_anatomy_words_do_not_imply_publication(self):
  d=parse_body('我的角色已成年，身体外观：有阴茎');self.assertNotIn('body_visibility',d)

 async def test_gender_query_is_read_only_and_does_not_need_model(self):
  before=self.s.read(self.k)
  for text in ('请确认虹膜角色是男是女','虹膜是男的还是女的？','02号性别是什么？'):
   answer=self.chat.public_query(self.k,'owner',text,True);self.assertIn('未固定性别',answer);self.assertIn('没有创建认领申请',answer)
  self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:self.assertEqual(c.execute("select count(*) from neon_enrollment_proposals where status='pending'").fetchone()[0],0)
 async def test_gender_edit_is_not_misrouted_as_read_only(self):
  self.assertIsNone(self.chat.public_query(self.k,'owner','我想修改角色性别设定',True))
