import tempfile,unittest,json,types
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_nexus_gm.enrollment_chat import EnrollmentChat
from astrbot_plugin_nexus_gm.gender_profile import gender_change
class GenderProfile(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(self.tmp.name);self.k='qq_official:isolated-gender';self.s.init(self.k,'admin');self.s.set_enrollment_auto(self.k,'admin',True,True)
  p=self.s.request_enrollment(self.k,'owner','claim','02',dict(appearance='粉色头发'));self.s.confirm_enrollment(self.k,'owner','yes',p['id'],p['digest'])
  p=self.s.request_enrollment(self.k,'owner','body','02',dict(adult_status='成年',body_features='有阴茎'),'profile','我的角色已成年，身体外观：有阴茎');self.s.confirm_enrollment(self.k,'owner','bodyyes',p['id'],p['digest'])
  self.chat=EnrollmentChat(self.s);self.i=0
 async def say(self,text,actor='owner'):
  self.i+=1;ev=types.SimpleNamespace(get_sender_id=lambda:actor,is_admin=lambda:False,message_obj=types.SimpleNamespace(message_id=str(self.i)))
  return await self.chat.handle(ev,self.k,text)
 async def test_exact_request_confirm_preserves_hair_body_and_stats(self):
  before=self.s.read(self.k)['snapshot'];profile=self.s.character_profile(self.k,'02')['details']
  text='虹膜变成女性';self.assertIsNone(self.chat.public_query(self.k,'owner',text,True));preview=await self.say(text)
  self.assertIn('女性',preview);self.assertNotIn('阴茎',preview);self.assertNotIn('gender',self.s.character_profile(self.k,'02')['details'])
  await self.say('确认');after=self.s.character_profile(self.k,'02')['details'];self.assertEqual(after,dict(profile,gender='女性'));self.assertEqual(before,self.s.read(self.k)['snapshot'])
  self.assertIn('已由持有者确认的性别设定是女性',self.chat.public_query(self.k,'other','请确认虹膜角色是男是女',True))
 async def test_other_account_and_duplicate_confirmation(self):
  with self.assertRaises(ValueError):await self.say('虹膜变成女性','other')
  await self.say('虹膜变成女性');await self.say('确认');rev=self.s.read(self.k)['revision'];await self.say('确认');self.assertEqual(rev,self.s.read(self.k)['revision'])
 async def test_pause_is_still_enforced(self):
  self.s.pause_group(self.k,'owner','pause')
  with self.assertRaises(ValueError):await self.say('虹膜变成女性')
 def test_questions_quotes_and_negation_are_not_changes(self):
  for text in ('请确认虹膜角色是男是女','虹膜变成女性可以吗','我不想让虹膜变成女性','他说虹膜变成女性','如果虹膜变成女性','性别是什么'):
   self.assertIsNone(gender_change(text))

 async def test_remaining_roster_natural_questions_are_read_only(self):
  before=self.s.read(self.k)
  for text in ('还有多少角色没领','还剩几个角色','哪些角色没人选','剩余角色名单','还能选谁','现在还有几个空位'):
   answer=self.chat.public_query(self.k,'owner',text,True);self.assertIsNotNone(answer,text);self.assertIn('9张',answer,text);self.assertNotIn('02号虹膜｜',answer)
  self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:self.assertEqual(c.execute("select count(*) from neon_enrollment_proposals where status='pending'").fetchone()[0],0)
 async def test_named_character_remaining_query_not_global_roster(self):
  self.assertIn('已被认领',self.chat.public_query(self.k,'owner','02号还没领吗',True));self.assertIn('尚未认领',self.chat.public_query(self.k,'owner','03号没人选吗',True))
 async def test_remaining_roster_separates_groups_and_claim_requests(self):
  other='qq_official:other';self.s.init(other,'admin');self.assertIn('10张',self.chat.public_query(other,'owner','还有多少角色没领',True));self.assertIsNone(self.chat.public_query(self.k,'owner','我想认领03号',True))

 async def test_claimed_roster_is_read_only_not_consent(self):
  before=self.s.read(self.k)
  for text in ('确认已领取的角色','哪些角色已经领取了','列出已认领角色','已经领取了哪些角色'):
   answer=self.chat.public_query(self.k,'owner',text,True);self.assertIn('已登记角色（1张）',answer,text);self.assertIn('02号虹膜｜待安全入场',answer);self.assertNotIn('阴茎',answer)
  self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:self.assertEqual(c.execute("select count(*) from neon_enrollment_proposals where status='pending'").fetchone()[0],0)
 async def test_claimed_query_distinguishes_empty_groups_and_remaining(self):
  key='qq_official:empty';self.s.init(key,'admin');self.assertIn('没有已登记角色',self.chat.public_query(key,'owner','确认已领取的角色',True))
  self.assertIn('9张',self.chat.public_query(self.k,'owner','还有哪些角色未被认领',True))
