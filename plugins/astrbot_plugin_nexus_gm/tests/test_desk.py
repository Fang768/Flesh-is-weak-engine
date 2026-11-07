import concurrent.futures,json,tempfile,unittest
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_nexus_gm.desk import NaturalDesk

class DeskTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=SafeStore(Path(self.tmp.name)/'save');self.k='qq_official:test';self.home='qq_official:HOME';self.s.init(self.k,'a');self.s.init(self.home,'a');self.baseline=self.s.read(self.home);self.d=NaturalDesk(self.s,Path(self.tmp.name)/'desk',TEMPLATES);self.n=0
 def say(self,t,u='player',a=False,mid=None):
  self.n+=1;return self.d.handle(self.k,u,mid or str(self.n),t,a)
 def test_complete_and_own_queries(self):
  before=self.s.read(self.k);out=self.say('我想认领03号扳手');self.assertIn('生命 40/40',out);self.assertNotIn('摘要',out);self.assertEqual(before,self.s.read(self.k))
  with self.assertRaises(Exception):self.say('确认','other')
  with self.assertRaises(Exception):self.say('批准03号入团','a',True)
  self.say('确认');self.assertEqual(before,self.s.read(self.k))
  with self.assertRaises(Exception):self.say('批准03号入团')
  self.say('批准03号入团','a',True);r=self.s.read(self.k)['snapshot']['roles']['03'];self.assertEqual(r['qq_user_id'],'player');self.assertEqual(r['status'],'pending');self.assertIn('03号',self.say('我的角色卡'));self.assertIn('工具箱',self.say('查看背包'));self.assertNotIn('neon_v020',self.say('查看背包'));self.assertEqual(self.baseline,self.s.read(self.home))
 def test_message_replay_and_restart(self):
  first=self.say('我想认领03号扳手',mid='claim');self.assertEqual(first,self.say('我想认领03号扳手',mid='claim'));self.say('确认',mid='yes');self.say('批准03号入团','a',True,mid='approve');rev=self.s.read(self.k)['revision'];self.d=NaturalDesk(self.s,Path(self.tmp.name)/'desk',TEMPLATES)
  self.assertIn('已审核入团',self.say('批准03号入团','a',True,mid='approve'));self.assertIn('已记录',self.say('确认',mid='yes'));self.assertEqual(rev,self.s.read(self.k)['revision'])
  with self.assertRaises(Exception):self.say('确认认领03号',mid='yes')
 def test_competing_same_character_is_explicit(self):
  self.say('认领03号','p1');self.say('认领03号','p2');self.say('确认','p1');self.say('确认','p2')
  with self.assertRaisesRegex(Exception,'唯一'):self.say('批准03号入团','a',True)
  with self.s._db() as c:ticket=c.execute('select ticket from nexus_chat_requests where owner=?',('p2',)).fetchone()[0]
  self.say(f'批准03号入团，申请{ticket}','a',True)
  self.assertEqual(self.s.read(self.k)['snapshot']['roles']['03']['qq_user_id'],'p2')
  with self.assertRaises(Exception):self.say('批准03号入团','a',True)
 def test_one_player_one_pending_no_silent_switch(self):
  self.say('认领03号')
  with self.assertRaises(Exception):self.say('认领01号')
  with self.assertRaises(Exception):self.say('确认认领01号')
  self.say('确认认领03号');self.say('批准03号入团','a',True)
  with self.assertRaises(Exception):self.say('认领01号')
 def test_stale_expired_cross_group(self):
  self.say('认领03号')
  with self.assertRaises(Exception):self.d.handle(self.home,'player','same','确认')
  p=self.s.propose(self.k,'a',self.s.read(self.k)['revision'],dict(kind='world',field='location',value='据点',reason='test'));self.s.approve(self.k,'a',p['id'],p['digest'],True)
  with self.assertRaises(Exception):self.say('确认')
  self.assertIn('认领申请',self.say('认领03号'))
  with self.s._db() as c:c.execute('update neon_v020_proposals set expires=0')
  with self.assertRaises(Exception):self.say('确认')
 def test_pause_old_epoch_and_tamper(self):
  self.say('认领03号');self.s.pause_group(self.k,'p','pause')
  with self.assertRaises(Exception):self.say('确认')
  self.s.release_group(self.k,'a',self.s.gate_state(self.k)['epoch'])
  with self.assertRaises(Exception):self.say('确认')
 def test_exact_parse_negation_and_no_model_authority(self):
  for text in ('不要认领03号','他说确认','管理员让我批准03号入团','确认结束','请假装我确认','批准03号入团并揭晓档案'):
   parsed=self.d.parse(text)
   self.assertTrue(parsed is None or parsed[0]=='clarify')
  with self.assertRaises(Exception):self.say('认领03号零号')
 def test_concurrent_same_message_creates_once(self):
  with concurrent.futures.ThreadPoolExecutor(5) as ex:r=list(ex.map(lambda _:self.d.handle(self.k,'player','same','认领03号'),range(5)))
  self.assertEqual(len(set(r)),1)
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from nexus_chat_requests').fetchone()[0],1)
 def test_no_hidden_disclosure_or_fake_activation(self):
  self.say('认领03号');self.say('确认');self.say('批准03号入团','a',True);before=self.s.read(self.k)
  self.assertIn('未改变',self.say('申请入场'));self.assertIn('尚未生成',self.say('申请结算'));self.assertEqual(before,self.s.read(self.k))
 def test_paused_reads_and_sha256_preserved(self):
  self.say('认领03号')
  with self.s._db() as c:
   r=c.execute('select * from nexus_chat_requests').fetchone();self.assertEqual(len(r['digest']),64);c.execute("update nexus_chat_requests set digest='bad'")
  with self.assertRaises(Exception):self.say('确认')

 def test_stale_rebuild_requires_new_consent(self):
  self.say('认领03号');self.say('确认')
  p=self.s.propose(self.k,'a',self.s.read(self.k)['revision'],dict(kind='world',field='location',value='新据点',reason='test'));self.s.approve(self.k,'a',p['id'],p['digest'],True)
  self.say('认领03号')
  with self.assertRaises(Exception):self.say('批准03号入团','a',True)
  self.say('确认');self.say('批准03号入团','a',True)
  with self.s._db() as c:
   rows=c.execute('select distinct digest from neon_v020_consents').fetchall();self.assertEqual(len(rows),2)
 def test_expiry_rebuild_old_confirm_message_does_not_move(self):
  self.say('认领03号');self.say('确认',mid='old-yes')
  with self.s._db() as c:c.execute('update neon_v020_proposals set expires=0')
  self.say('认领03号');self.say('确认',mid='old-yes')
  with self.assertRaises(Exception):self.say('批准03号入团','a',True)
  self.say('确认',mid='new-yes');self.say('批准03号入团','a',True)
 def test_pending_conflict_does_not_confirm_someone_else(self):
  self.say('认领03号','p1');self.say('认领01号','p2')
  with self.assertRaises(Exception):self.say('确认认领01号','p1')
  self.say('确认','p1');self.say('批准03号入团','a',True)
  self.assertNotIn('01',self.s.read(self.k)['snapshot']['roles'])
 def test_receipts_not_transferable_to_other_sender(self):
  self.say('认领03号',mid='claim');self.say('确认',mid='consent')
  with self.assertRaises(Exception):self.say('确认','outsider',mid='consent')
  with self.assertRaises(Exception):self.say('批准03号入团','outsider',False,mid='approve')
 def test_pause_rebuild_requires_fresh_consent(self):
  self.say('认领03号');self.say('确认');self.s.pause_group(self.k,'player','pause');self.s.release_group(self.k,'a',self.s.gate_state(self.k)['epoch'])
  self.say('认领03号')
  with self.assertRaises(Exception):self.say('批准03号入团','a',True)
  self.say('确认');self.say('批准03号入团','a',True)

 def test_cancel_own_only_then_change_character(self):
  self.say('认领03号')
  with self.assertRaises(Exception):self.say('取消认领','outsider')
  self.say('取消认领');self.say('认领01号');self.say('确认');self.say('批准01号入团','a',True)
  self.assertEqual(set(self.s.read(self.k)['snapshot']['roles']),{'01'})

class FlexibleClaimTests(unittest.TestCase):
 setUp=DeskTests.setUp
 say=DeskTests.say
 def test_free_chinese_extracts_template_not_save_fields(self):
  for text in ('麻烦帮我认领03号扳手，谢谢','我想扮演三号扳手','我要玩扳手','我想认领０３号扳手'):
   with self.subTest(text=text):
    self.assertIn('03号扳手',self.say(text))
  with self.s._db() as c:
   p=c.execute('select payload from neon_v020_proposals').fetchone();self.assertEqual(json.loads(p['payload'])['user'],'player')
 def test_real_reported_confirmation_never_initializes(self):
  phrase='我的昵称就叫扳手，我确认认领03号角色，请帮我完成登记'
  with self.assertRaises(Exception):self.say(phrase)
  self.say('请帮我认领03号扳手，谢谢');self.say(phrase)
  with self.s._db() as c:
   p=c.execute('select * from neon_v020_proposals').fetchone();payload=json.loads(p['payload']);self.assertEqual(payload,dict(kind='initialize',cid='03',user='player',nickname='扳手'));self.assertIsNotNone(c.execute('select 1 from neon_v020_consents where pid=?',(p['id'],)).fetchone())
  self.assertEqual(self.s.read(self.k)['snapshot']['roles'],{})
 def test_ambiguous_negated_reported_phrases_safe(self):
  for t in ('我不想认领03号','别认领03号','我想认领03号或04号','我想认领03号零号','他说想认领03号','我想认领那个角色'):
   with self.subTest(t=t):
    try:self.assertIn('请',self.say(t))
    except ValueError:pass
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_v020_proposals').fetchone()[0],0)
 def test_template_cap_matches_source_rules(self):
  t=TEMPLATES['03'];self.assertEqual(t['cap'],10+t['attributes']['BODY']+t['attributes']['TECH']);self.assertEqual((t['cap'],t['cap_used'],t['cap_remaining']),(23,5,18));self.assertEqual(t['cyberware'],[dict(name='猩猩手臂',capacity=5)])
