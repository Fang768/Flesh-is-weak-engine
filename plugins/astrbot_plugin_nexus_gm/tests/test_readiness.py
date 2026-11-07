import unittest,tempfile,concurrent.futures,json,threading,types,asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from astrbot_plugin_neon_save.safe import SafeStore
from astrbot_plugin_neon_save.store import SaveError
from astrbot_plugin_nexus_gm.engine import Engine,GMError
from astrbot_plugin_nexus_gm.semantic import SemanticController

class Readiness(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def setup_game(self,n,label='g'):
  save=SafeStore(self.root/label);key='qq_official:'+label;save.init(key,'admin')
  def commit(op):
   p=save.propose(key,'admin',save.read(key)['revision'],op)
   for u in p['required']:save.consent(key,u,p['id'],p['digest'])
   save.approve(key,'admin',p['id'],p['digest'],True)
  commit(dict(kind='world',field='scene_status',value='active',reason='隔离验收'))
  for i in range(1,n+1):
   cid=f'{i:02}';commit(dict(kind='initialize',cid=cid,user='u'+cid,nickname='测试'+cid))
   commit(dict(kind='status',cid=cid,value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='安全据点',team='A')))
  engine=Engine(self.root/(label+'.db'),save,rand=lambda n:9);engine.open(key,True)
  return save,key,engine,commit
 def complete(self,n,label):
  save,k,e,commit=self.setup_game(n,label)
  before=save.read(k)
  ids=[f'{i:02}' for i in range(1,n+1)]
  def end(cid,stage):return e.natural(k,'u'+cid,'end'+stage+cid,'结束窗口'+stage,intent_override='end')
  with concurrent.futures.ThreadPoolExecutor(n) as pool:list(pool.map(lambda x:end(x,'contact'),ids))
  self.assertEqual(e.view(k)['scene'],'取回')
  count=[];lock=threading.Lock()
  def rand(n):
   with lock:count.append(1)
   return 9
  e.rand=rand
  def check(cid):
   text='检修任务终端'+cid;mid='check'+cid
   e.natural(k,'u'+cid,mid,text,intent_override='工程')
   result=e.natural(k,'u'+cid,mid,text,True)
   self.assertEqual(result['response'],e.natural(k,'u'+cid,mid,text,True)['response'])
   return result
  with concurrent.futures.ThreadPoolExecutor(n) as pool:list(pool.map(check,ids))
  self.assertEqual(len(count),n);self.assertEqual(e.view(k)['scene'],'交付')
  with concurrent.futures.ThreadPoolExecutor(n) as pool:list(pool.map(lambda x:end(x,'delivery'),ids))
  self.assertTrue(e.view(k)['complete']);self.assertEqual(save.read(k),before)
  return save,k,e,commit
 def test_3_to_10_concurrent_windows_and_duplicate_rolls(self):
  for n in range(3,11):
   with self.subTest(players=n):self.complete(n,'players'+str(n))
 def test_two_player_xp_full_authorization(self):
  save,k,e,commit=self.complete(2,'rewards');before=save.read(k)
  p=e.proposal(k,'admin',True)
  with self.assertRaises(SaveError):save.approve(k,'admin',p['id'],p['digest'],True)
  with self.assertRaises(SaveError):save.consent(k,'u01',p['id'],'bad')
  save.consent(k,'u01',p['id'],p['digest'])
  with self.assertRaises(SaveError):save.approve(k,'admin',p['id'],p['digest'],True)
  self.assertEqual(save.read(k),before)
  save.consent(k,'u02',p['id'],p['digest'])
  with self.assertRaises(SaveError):save.approve(k,'u01',p['id'],p['digest'],False)
  save.approve(k,'admin',p['id'],p['digest'],True)
  roles=save.read(k)['snapshot']['roles']
  self.assertEqual([(roles[x]['xp'],roles[x]['dossier_progress']) for x in ('01','02')],[(3,1),(3,1)])
  with self.assertRaises(SaveError):save.approve(k,'admin',p['id'],p['digest'],True)
 def test_concurrent_proposal_only_one_id(self):
  save,k,e,commit=self.complete(2,'proposal-race')
  with concurrent.futures.ThreadPoolExecutor(2) as pool:ids=list(pool.map(lambda _:e.proposal(k,'admin',True)['id'],[1,2]))
  self.assertEqual(ids[0],ids[1])
 def test_10_player_settlement_limit_explicit(self):
  save,k,e,commit=self.complete(10,'ten-rewards')
  with self.assertRaisesRegex(GMError,'批次最多10项'):e.proposal(k,'admin',True)
 def test_away_return_invalidates_window_no_secret_skip(self):
  save,k,e,commit=self.setup_game(3,'away')
  commit(dict(kind='status',cid='02',value='away'))
  with self.assertRaisesRegex(GMError,'真实存档已变化'):e.natural(k,'u01','a','结束',intent_override='end')
  self.assertEqual(e.view(k)['waiting'],['01','02','03'])
  commit(dict(kind='status',cid='02',value='active',entry=dict(safe_node=True,in_combat=False,check_locked=False,scene='安全节点',team='A')))
  with self.assertRaisesRegex(GMError,'真实存档已变化'):e.natural(k,'u02','b','结束',intent_override='end')
 def test_combat_is_not_implemented_and_fails_closed(self):
  save,k,e,commit=self.setup_game(2,'combat')
  for intent in ('initiative','attack','damage','enemy_turn','combat_retreat'):
   with self.subTest(intent=intent),self.assertRaises(GMError):e.action(k,'u01',intent,'战斗请求',intent)
  self.assertEqual(e.view(k)['recap'],[])
 def test_transient_model_retry_is_bounded_no_dice(self):
  save,k,e,commit=self.setup_game(2,'retry');controller=SemanticController(e,None)
  provider=types.SimpleNamespace(get_model=lambda:'deepseek-test',text_chat=AsyncMock(side_effect=[asyncio.TimeoutError(),types.SimpleNamespace(completion_text='ok')]))
  result=asyncio.run(controller.model_call(provider,'公开动作','规则',key=k,timeout=.01,attempts=2))
  self.assertEqual(result.completion_text,'ok');self.assertEqual(provider.text_chat.await_count,2)
  for call in provider.text_chat.call_args_list:self.assertEqual(call.kwargs['request_max_retries'],1)
  self.assertEqual(e.view(k)['recap'],[])
 def test_two_player_distinct_bonus_rewards_independent(self):
  save,k,e,commit=self.complete(2,'different-xp');before=save.read(k)
  payload=dict(kind='batch',operations=[dict(kind='world',field='current_task_id',value='bonus-two',reason='隔离奖励验证'),dict(kind='mission',cid='01',task='bonus-two',meaningful_action='公开有效行动',completed=True,survived=True,actually_present=True,completion_evidence='任务完成'),dict(kind='mission',cid='02',task='bonus-two',meaningful_action='公开有效行动',completed=True,survived=True,actually_present=True,completion_evidence='任务完成',personal_major_goal=True,personal_goal_evidence='本人重大目标证据',faction_shift=True,faction_shift_evidence='阵营变化证据')])
  p=save.propose(k,'admin',before['revision'],payload)
  for u in p['required']:save.consent(k,u,p['id'],p['digest'])
  save.approve(k,'admin',p['id'],p['digest'],True)
  roles=save.read(k)['snapshot']['roles'];self.assertEqual((roles['01']['xp'],roles['02']['xp']),(3,6))
 def test_same_end_text_new_message_does_not_double_advance(self):
  save,k,e,commit=self.setup_game(3,'end-duplicate')
  e.natural(k,'u01','one','我确认结束',intent_override='end')
  e.natural(k,'u01','one-retry','我确认结束',intent_override='end')
  self.assertEqual(e.view(k)['waiting'],['02','03'])
  for cid in ('02','03'):e.natural(k,'u'+cid,cid,'我确认结束',intent_override='end')
  self.assertEqual(e.view(k)['scene'],'取回')
  e.natural(k,'u01','late-retry','我确认结束',intent_override='end')
  self.assertEqual(e.view(k)['waiting'],['01','02','03'])
 def test_duplicate_replay_after_restart_and_timeout_does_not_roll(self):
  save,k,e,commit=self.setup_game(3,'restart')
  p=e.natural(k,'u01','prepare','我要询问负责人',intent_override='交涉')
  restored=Engine(e.path,save,rand=lambda n:4)
  a=restored.natural(k,'u01',p['mid'],'我要询问负责人',True)
  b=restored.natural(k,'u01','new-request','我要询问负责人')
  self.assertEqual(a['response'],b['response'])
  with restored.db() as c:s=restored.load(c,k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),1)

 def test_same_window_result_replays_after_text_ttl_without_model(self):
  save,k,e,commit=self.setup_game(2,'long-duplicate');e.clock=lambda:1000
  text='我递上工牌跟负责人聊聊';e.natural(k,'u01','first',text,intent_override='交涉');result=e.natural(k,'u01','first',text,True)
  e.clock=lambda:2000
  self.assertEqual(e.cached(k,'u01','retry-after-ttl',text)['response'],result['response'])
  controller=SemanticController(e,None)
  event=types.SimpleNamespace(get_sender_id=lambda:'u01')
  with self.assertRaisesRegex(GMError,'本轮行动已完成'):asyncio.run(controller.analyze(event,k,'我换种说法再来'))
  e.natural(k,'u02','second','结束接洽',intent_override='end')
  self.assertIsNone(e.cached(k,'u01','new-window',text))
  with e.db() as c:s=e.load(c,k)
  self.assertEqual(sum(bool(x.get('check')) for x in s['log']),1)
