import asyncio,json,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
import test_dynamic_engine as support
from astrbot_plugin_nexus_gm.dynamic.adapter import DynamicAdapter
class AdapterTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.fixture=support.DynamicEngine();self.fixture.setUp();self.addCleanup(self.fixture.tmp.cleanup);self.fixture.players(3);self.s=self.fixture.s;self.e=self.fixture.e;self.k=self.fixture.k
  self.p=types.SimpleNamespace(get_model=lambda:'deepseek-chat',text_chat=AsyncMock(return_value=types.SimpleNamespace(completion_text=json.dumps(dict(text='你俯身靠近柜门，手指沿锁孔轻轻移动。'),ensure_ascii=False))))
  ctx=types.SimpleNamespace(get_using_provider_async=AsyncMock(return_value=self.p));self.chat=types.SimpleNamespace(db=lambda:__import__('sqlite3').connect(Path(self.fixture.tmp.name)/'chat.sqlite3'),reply=AsyncMock())
  with self.chat.db() as c:c.execute('create table replies(k,actor,mid,kind)')
  self.adapter=DynamicAdapter(Path(self.fixture.tmp.name),self.s,ctx,None,self.chat);self.adapter.allow.write_text(json.dumps([self.k]));self.adapter.engine=self.e
  from astrbot_plugin_nexus_gm.dynamic.controller import DynamicController
  self.adapter.controller=DynamicController(self.e,ctx)
 def event(self,text,actor='u03',mid='m',admin=False):
  e=types.SimpleNamespace(get_sender_id=lambda:actor,is_admin=lambda:admin,message_obj=types.SimpleNamespace(message_id=mid),unified_msg_origin='isolated',stop_event=lambda:None,plain_result=lambda x:x,sent=[])
  async def send(t):e.sent.append(t)
  e.send=send;return e
 async def test_program_sends_real_result_and_retry_no_model_or_roll(self):
  self.adapter.controller.interpret=AsyncMock(return_value=dict(mode='action',operation='unlock',node='locker',question='',_round=1,_epoch=self.s.gate_state(self.k)['epoch']))
  e=self.event('用细铁丝拨开锁簧')
  with patch('astrbot_plugin_neon_save.safe.secrets.randbelow',return_value=7):await self.adapter.handle(e,self.k,'用细铁丝拨开锁簧')
  self.assertTrue(any('DV14' in t for t in e.sent));self.assertTrue(any('1d10=8' in t and '开锁3' in t for t in e.sent))
  calls=self.adapter.controller.interpret.await_count;e2=self.event('用细铁丝拨开锁簧');await self.adapter.handle(e2,self.k,'用细铁丝拨开锁簧');self.assertEqual(calls,self.adapter.controller.interpret.await_count)
  with self.s._db() as c:self.assertEqual(c.execute('select count(*) from neon_v020_rolls').fetchone()[0],1)
 async def test_disabled_groups_cannot_start_or_create_session(self):
  e=self.event('开始跑团',actor='admin',admin=True);before=self.s.read(self.k);self.assertFalse(await self.adapter.handle(e,'qq_official:HOME','开始跑团'));self.assertIsNone(self.e.read('qq_official:HOME'));self.assertEqual(before,self.s.read(self.k))
 async def test_pause_bypasses_model_and_no_autoresume(self):
  e=self.event('暂停');self.assertTrue(await self.adapter.handle(e,self.k,'暂停'));self.assertTrue(self.s.gate_state(self.k)['paused']);self.p.text_chat.assert_not_awaited()
 async def test_plain_confirmation_cannot_approve_world(self):
  for i in range(1,4):self.e.action(self.k,'u'+f'{i:02}',str(i),'问情况','talk')
  self.e.checkpoint(self.k);before=self.s.read(self.k)
  e=self.event('批准剧情',admin=False);await self.adapter.handle(e,self.k,'批准剧情');self.assertEqual(before,self.s.read(self.k));self.assertTrue(any('管理员' in t for t in e.sent))
 async def test_timeout_keeps_window_waiting_no_dice(self):
  self.adapter.controller.interpret=AsyncMock(side_effect=asyncio.TimeoutError())
  e=self.event('新问题');await self.adapter.handle(e,self.k,'新问题');self.assertEqual(self.e.read(self.k)['done'],[])

 async def test_unanimous_resume_reuses_existing_protocol_and_pause_replay(self):
  pause=self.event('暂停',actor='u03',mid='pause');await self.adapter.handle(pause,self.k,'暂停')
  ask=self.event('申请恢复',actor='u03',mid='ask');await self.adapter.handle(ask,self.k,'申请恢复')
  for uid in ('u01','u02'):
   e=self.event('同意恢复',actor=uid,mid=uid);await self.adapter.handle(e,self.k,'同意恢复')
  early=self.event('批准恢复',actor='admin',admin=True,mid='early');await self.adapter.handle(early,self.k,'批准恢复');self.assertTrue(self.s.gate_state(self.k)['paused'])
  e=self.event('同意恢复',actor='u03',mid='yes3');await self.adapter.handle(e,self.k,'同意恢复')
  approval=self.event('批准恢复',actor='admin',admin=True,mid='final');await self.adapter.handle(approval,self.k,'批准恢复');self.assertFalse(self.s.gate_state(self.k)['paused']);self.assertEqual(self.e.read(self.k)['round'],1)
  await self.adapter.handle(pause,self.k,'暂停');self.assertFalse(self.s.gate_state(self.k)['paused'])
