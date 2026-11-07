import unittest,asyncio,json,types
from unittest.mock import AsyncMock
from test_dynamic_controller import ControllerTests
from astrbot_plugin_nexus_gm.dynamic.decision import validate
from astrbot_plugin_nexus_gm.dynamic.engine import NarrativeError
class HostDecision(ControllerTests):
 def test_scene_does_not_touch_world_or_turn(self):
  c,p,e=self.fixture();before=self.e.read(self.k);native=self.s.read(self.k)
  p.text_chat.return_value=types.SimpleNamespace(completion_text=json.dumps(dict(mode='scene',operation='',node='contact',question='')))
  self.assertEqual(asyncio.run(c.interpret(e,self.k,'米拉，你这是招人还是招冤大头？'))['mode'],'scene')
  self.assertEqual(self.e.read(self.k),before);self.assertEqual(self.s.read(self.k),native)
 def test_current_window_context_includes_actual_teammate_action(self):
  c,p,e=self.fixture(2);self.e.action(self.k,'u02','other','我们先不接，问问风险','talk')
  context=c.decision_context(self.k,'u01');self.assertEqual(context['current_window_actions'][0]['player_action'],'我们先不接，问问风险')
 def test_other_team_window_not_exposed(self):
  c,p,e=self.fixture(2);self.e.change_participation(self.k,'u02',team='B');self.e.action(self.k,'u02','other','PRIVATE_TEAM_B','talk')
  self.assertNotIn('PRIVATE_TEAM_B',json.dumps(c.decision_context(self.k,'u01')))
 def test_model_cannot_turn_scene_into_authority(self):
  for d in (dict(mode='scene',operation='unlock',node='locker',question=''),dict(mode='scene',operation='',node='free',question='',approved=True),dict(mode='review',operation='',node='free',question='')):
   with self.assertRaises(NarrativeError):validate(d)
