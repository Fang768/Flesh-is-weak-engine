import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_nexus_gm.appearance import DEFAULTS,appearance_reference,appearance_text
from astrbot_plugin_nexus_gm.enrollment_chat import EnrollmentChat
from test_enrollment_chat import Event
class Appearance(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(Path(self.tmp.name)/'save');self.k='qq_official:appearance';self.s.init(self.k,'admin');self.chat=EnrollmentChat(self.s,None)
 def test_complete_optional_and_separate_from_game_templates(self):
  self.assertEqual(len(DEFAULTS),10)
  for cid,d in DEFAULTS.items():
   self.assertTrue(d['hair']);self.assertTrue(d['eyes']);self.assertNotIn('attributes',d);self.assertNotIn('equipment',d)
   self.assertIn('非固定规则',appearance_text(cid))
  self.s._enroll_templates() # Existing approved fingerprint still passes.
 async def test_preview_is_cosmetic_only_no_new_profile_or_stats(self):
  before=copy.deepcopy(self.s.read(self.k));out=await self.chat.handle(Event(mid='preview'),self.k,'认领角色 琉璃')
  self.assertIn('黑色直发',out);self.assertIn('灰绿色',out);self.assertIn('基础外貌参考',out);self.assertEqual(before,self.s.read(self.k))
  with self.s._db() as c:
   p=c.execute('select payload,profile,preview from neon_enrollment_proposals').fetchone();self.assertEqual(json.loads(p['profile']),{});role=json.loads(p['preview'])['roles']['09'];self.assertEqual(role['attributes'],TEMPLATES['09']['attributes']);self.assertEqual(role['hp'],TEMPLATES['09']['hp']);self.assertNotIn('eyes',json.loads(p['payload']))
 async def test_all_public_queries_do_not_write_roles(self):
  before=self.s.read(self.k);out=self.chat.public_query(self.k,'p','介绍每个人物的基础外貌，包括发色瞳色',False)
  for cid,t in TEMPLATES.items():self.assertIn(cid+'号'+t['name'],out)
  self.assertEqual(before,self.s.read(self.k));self.assertIn('不额外提供护甲',out)
 async def test_player_diy_wins_without_default_color_conflict(self):
  self.s.set_enrollment_auto(self.k,'admin',True,True,'on','开启角色自动审核')
  out=await self.chat.handle(Event(mid='preview'),self.k,'我想玩03号扳手，希望他留银色长发')
  self.assertIn('银色长发',out);self.assertNotIn(DEFAULTS['03']['hair'],out)
  await self.chat.handle(Event(mid='consent'),self.k,'确认');profile=self.s.character_profile(self.k,'03')['details'];self.assertEqual(appearance_reference('03',profile)['source'],'player_diy')
  before=self.s.read(self.k);out=self.chat.public_query(self.k,'p','我的发色是什么',True);self.assertIn('银色长发',out);self.assertNotIn(DEFAULTS['03']['hair'],out);self.assertEqual(before,self.s.read(self.k))
  other=self.chat.public_query(self.k,'other','介绍03号外貌',True);self.assertNotIn('银色长发',other);self.assertIn(DEFAULTS['03']['hair'],other)
 async def test_roster_and_specific_card_display_consistent_reference(self):
  out=self.chat.public_query(self.k,'p','介绍琉璃',False);self.assertIn(DEFAULTS['09']['eyes'],out)
  out=self.chat.public_query(self.k,'p','琉璃长什么样',False);self.assertIn(DEFAULTS['09']['eyes'],out)
 async def test_read_query_does_not_steal_personal_edit_or_consent(self):
  for t in ('确认','我想修改角色发色','把瞳色改成绿色','认领角色03，希望短发'):
   self.assertIsNone(self.chat.public_query(self.k,'p',t,True),t)
