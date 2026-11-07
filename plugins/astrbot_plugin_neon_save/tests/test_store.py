"""Run: python -m unittest discover -s tests -v"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_neon_save.store import NeonStore, SaveError


class TestStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.s = NeonStore(self.temp.name)
        self.a = "qq_official:group-one"
        self.b = "qq_official:group-two"

    def test_init_and_group_isolation(self):
        self.assertEqual(self.s.init(self.a, "admin"), 1)
        a = self.s.read(self.a)
        self.assertIsNone(a["snapshot"]["world"]["team_cash"])
        self.assertFalse(a["snapshot"]["world"]["mission_offer"]["advance_paid"])
        self.assertEqual(a["snapshot"]["roles"], {})
        with self.assertRaises(SaveError): self.s.init(self.a, "admin")
        with self.assertRaises(SaveError): self.s.read(self.b)
        self.assertEqual(self.s.init(self.b, "admin"), 1)
        self.s.join(self.a, "admin", 1, "qqopen-1", "03", "扳手")
        self.assertEqual(self.s.read(self.b)["snapshot"]["roles"], {})

    def test_join_unique_and_optimistic(self):
        self.s.init(self.a, "admin")
        self.assertEqual(self.s.join(self.a, "admin", 1, "u1", "03", "扳手"), 2)
        with self.assertRaises(SaveError): self.s.join(self.a, "admin", 2, "u1", "04", "另一人")
        with self.assertRaises(SaveError): self.s.join(self.a, "admin", 2, "u2", "03", "另一人")
        with self.assertRaises(SaveError): self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"alert_level","value":1}],"test")
        self.assertEqual(self.s.read(self.a)["revision"],2)

    def test_atomic_multi_change_and_rollback(self):
        self.s.init(self.a, "admin")
        with self.assertRaises(SaveError):
            self.s.patch_many(self.a,"admin",1,[
                {"target":"world","field":"alert_level","value":2},
                {"target":"world","field":"team_cash","value":-10},
            ],"bad")
        self.assertEqual(self.s.read(self.a)["snapshot"]["world"]["alert_level"],0)
        self.assertEqual(self.s.read(self.a)["revision"],1)
        self.assertEqual(len(self.s.history(self.a)),1)
        self.assertEqual(self.s.patch_many(self.a,"admin",1,[
            {"target":"world","field":"team_cash","value":100},
            {"target":"world","field":"alert_level","value":1},
        ],"ok"),2)
        self.assertEqual(len(self.s.history(self.a)),2)

    def test_reopen_database_read_same_value(self):
        self.s.init(self.a, "admin")
        self.s.join(self.a, "admin",1,"u1","03","扳手")
        self.s.patch_many(self.a,"admin",2,[{"target":"role:03","field":"max_hp","value":30},
                                            {"target":"role:03","field":"hp","value":24}],"角色确认")
        second=NeonStore(self.temp.name)
        self.assertEqual(second.read(self.a)["snapshot"]["roles"]["03"]["hp"],24)
        self.assertEqual(second.read(self.a)["revision"],3)

    def test_dossier_no_secret_and_one_per_task(self):
        self.s.init(self.a, "admin")
        self.s.join(self.a,"admin",1,"u1","03","扳手")
        rev = 2
        for i in range(1,4):
            rev=self.s.dossier_task(self.a,"admin",rev,"03",f"mission-{i}")
        role=self.s.read(self.a)["snapshot"]["roles"]["03"]
        self.assertEqual(role["dossier_progress"],3)
        self.assertEqual(role["dossier_stage"],"ready")
        self.assertEqual(role["dossier_content"],"尚未生成")
        with self.assertRaises(SaveError): self.s.dossier_task(self.a,"admin",rev,"03","mission-3")
        with self.assertRaises(SaveError): self.s.reveal(self.a,"admin",rev,"03",1,"测试","奖励","QQ消息001")
        rev=self.s.patch_many(self.a,"admin",rev,[{"target":"role:03","field":"dossier_stage","value":"omen"}],"确认异象公开")
        rev=self.s.patch_many(self.a,"admin",rev,[{"target":"role:03","field":"dossier_stage","value":"consented"}],"玩家继续揭晓")
        rev=self.s.reveal(self.a,"admin",rev,"03",8,"玩家确认背景","奖励","QQ消息002")
        role=self.s.read(self.a)["snapshot"]["roles"]["03"]
        self.assertEqual(role["dossier_category_id"],8)
        self.assertEqual(role["dossier_stage"],"revealed")

    def test_export_restore_and_tamper(self):
        self.s.init(self.a,"admin")
        self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"team_cash","value":20}],"deposit")
        backup_id, file=self.s.export(self.a,"admin")
        self.assertTrue(file.is_file())
        self.s.patch_many(self.a,"admin",2,[{"target":"world","field":"team_cash","value":0}],"spend")
        self.assertEqual(self.s.restore(self.a,"admin",3,backup_id),4)
        self.assertEqual(self.s.read(self.a)["snapshot"]["world"]["team_cash"],20)
        self.assertEqual([x["action"] for x in self.s.history(self.a)][:2],["restore","patch"])
        with self.assertRaises(SaveError): self.s.restore(self.b,"admin",1,backup_id)
        with self.assertRaises(SaveError): self.s.restore(self.a,"admin",3,backup_id)
        payload=json.loads(file.read_text())
        payload["snapshot"]["world"]["team_cash"] = 12345
        file.write_text(json.dumps(payload))
        with self.assertRaises(SaveError): self.s.restore(self.a,"admin",4,backup_id)

    def test_field_guard(self):
        self.s.init(self.a,"admin")
        with self.assertRaises(SaveError): self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"admin","value":1}],"bad")
        with self.assertRaises(SaveError): self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"alert_level","value":True}],"bad")
        with self.assertRaises(SaveError): self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"location","value":"x"*9999}],"bad")
        self.assertEqual(self.s.read(self.a)["revision"],1)

    def test_sequential_revision_protection(self):
        self.s.init(self.a,"admin")
        rev=self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"alert_level","value":1}],"first")
        with self.assertRaises(SaveError):
            self.s.patch_many(self.a,"admin",1,[{"target":"world","field":"alert_level","value":2}],"stale")
        self.assertEqual(self.s.read(self.a)["revision"],rev)

if __name__ == "__main__": unittest.main()
