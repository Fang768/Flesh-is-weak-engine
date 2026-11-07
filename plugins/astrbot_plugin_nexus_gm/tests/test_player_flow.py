import json,tempfile,unittest
from pathlib import Path
from astrbot_plugin_neon_save.safe import SafeStore,TEMPLATES
from astrbot_plugin_nexus_gm.engine import Engine
from astrbot_plugin_nexus_gm.player_flow import flow_request,player_flow
class PlayerFlow(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=Path(self.tmp.name);self.s=SafeStore(p/'save');self.k='qq_official:flow';self.s.init(self.k,'admin');self.e=Engine(p/'gm.sqlite3',self.s)
 def guide(self,entry=True,story=False,actor='p'):
  return player_flow(self.s,self.e,self.k,actor,TEMPLATES,entry,story)
 def request(self):
  self.s.set_enrollment_auto(self.k,'admin',False,True,'policy','关闭角色自动审核');return self.s.request_enrollment(self.k,'p','claim','03',{},'claim','我想玩03号')
 def test_requested_phrases_and_pause(self):
  for q in ('请告诉我游戏流程','新手流程','这个游戏怎么玩','下一步我该做什么','我现在该干嘛','怎么认领'):
   self.assertTrue(flow_request(q),q)
  for q in ('确认','批准03号入团','【暂停】告诉我游戏流程','我想玩03号','你好'):
   self.assertFalse(flow_request(q),q)
 def test_closed_home_explains_only_browsing_no_scene_created(self):
  before=self.s.read(self.k);out=self.guide(False)
  self.assertIn('认领还没开放',out);self.assertIn('现在不用回复',out);self.assertEqual(before,self.s.read(self.k))
  with self.e.db() as c:self.assertIsNone(c.execute('select 1 from sessions where k=?',(self.k,)).fetchone())
 def test_open_empty_next_step(self):
  self.assertIn('你还没选角色',self.guide());self.assertIn('预览',self.guide())
 def test_exact_owner_pending_preview_and_waiting_review(self):
  p=self.request();self.assertIn('已经拿到扳手的预览',self.guide());self.assertIn('你还没选角色',self.guide(actor='other'))
  self.s.confirm_enrollment(self.k,'p','yes',p['id'],p['digest'],'确认');self.assertIn('等待审核结果',self.guide())
 def test_stale_preview_does_not_prompt_old_consent(self):
  self.request()
  with self.s._db() as c:c.execute('update neon_enrollment_proposals set expires=0')
  self.assertIn('预览已失效',self.guide())
 def test_committed_pending_is_registered_not_waiting_approval(self):
  p=self.request();self.s.confirm_enrollment(self.k,'p','yes',p['id'],p['digest'],'确认');self.s.approve_enrollment(self.k,'admin','approve',p['id'],p['digest'],True,'批准03号入团')
  out=self.guide();self.assertIn('已经登记成功',out);self.assertIn('安全入场',out);self.assertNotIn('等待审核结果',out)
 def test_pause_comes_before_every_other_step(self):
  self.s.pause_group(self.k,'p','pause');before=self.s.read(self.k);out=self.guide()
  self.assertIn('目前处于暂停',out);self.assertEqual(before,self.s.read(self.k));self.assertTrue(self.s.gate_state(self.k)['paused'])
 def test_no_ids_hashes_or_fake_scene_instructions(self):
  out=self.guide(False)
  for term in ('revision','SHA256','/neon','/nexus','提案ID','米拉','死去的快递员'):self.assertNotIn(term,out)

 def test_active_turn_waiting_and_completion_are_readonly(self):
  p=self.request();self.s.confirm_enrollment(self.k,'p','yes',p['id'],p['digest'],'确认');self.s.approve_enrollment(self.k,'admin','approve',p['id'],p['digest'],True,'批准03号入团')
  with self.s._db() as c:
   snap=self.s.read(self.k)['snapshot'];snap['roles']['03']['status']='active';snap['world']['scene_status']='active';c.execute('update saves set snapshot=? where group_key=?',(json.dumps(snap),self.k))
  state=dict(roles={'03':snap['roles']['03'],'01':{}},done=[],pending={},complete=False,paused=False)
  def store():
   with self.e.db() as c:c.execute('insert or replace into sessions values(?,?)',(self.k,json.dumps(state)))
  store();before=self.s.read(self.k);self.assertIn('这轮还可以行动',self.guide(story=True))
  state['done']=['03'];store();self.assertIn('先等零号',self.guide(story=True))
  state['done']=[];state['pending']={'03':'工程'};store();self.assertIn('不要另起一次掷骰',self.guide(story=True))
  state['pending']={};state['complete']=True;store();self.assertIn('奖励仍需',self.guide(story=True))
  self.assertEqual(before,self.s.read(self.k))
