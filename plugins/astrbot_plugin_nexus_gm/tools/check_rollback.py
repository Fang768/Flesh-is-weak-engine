"""Read-only preflight. Does NOT restore, delete, pause or change any database."""
import argparse,json,sqlite3
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True,help='AstrBot data/plugin_data directory');args=p.parse_args();base=Path(args.data_dir);gm=base/'astrbot_plugin_nexus_gm/rehearsal.sqlite3';count=0
with sqlite3.connect('file:'+str(gm)+'?mode=ro',uri=True) as c:
 for raw, in c.execute('SELECT v FROM sessions'):
  if json.loads(raw).get('onboarding_only'):count+=1
if count:raise SystemExit('检测到新版开场前安全控制点。不能仅退回旧GM代码；请保持停机、保留完整备份，由维护者选择可保留授权记录的方案，或明确批准恢复匹配的完整升级前备份。此检查未修改任何数据。')
print('未发现新版开场前控制点。此结果只允许继续人工评估代码回退；DIY仍需保留完整SQLite，旧版界面不会读取它。未执行回退或数据恢复。')
