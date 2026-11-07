"""Read-only, state-aware player instructions. Never opens or approves a game."""
import json,re,time

STEPS='''不用先背完整个世界观，先记住这几个步骤就够了：

1. 看角色：问“有什么角色可以选”，或者“介绍虹膜”。不懂家乡和词条，可以随时追问。
2. 选角色：认领开放后，说“我想玩02号虹膜”。可以补充外观、性格等小细节；固定数值和核心背景不变。
3. 核对并确认：认真看预览，确定是自己想要的内容后，本人回复“确认”。是否自动通过取决于本群设置，需要审核时等管理员处理。
4. 等待入场：登记成功不等于已经进入剧情。等管理员安排安全入场，并明确开场后再行动。
5. 自然地玩：直接说“我递上工牌，想和负责人打听情况”。需要检定时会先说明，再取得真实骰点。每个人决定自己的行动，不替同伴选择。

不知道怎么做就问我，不用自己编技能名或判定。觉得不舒服可以直接说“暂停”，无需解释原因。'''

def flow_request(text):
 t=text.strip()
 if t.startswith(('/', '【暂停】')) or t=='暂停':return False
 if re.search(r'批准|取消认领|开启自动审核|关闭自动审核',t):return False
 return bool(re.search(r'游戏流程|跑团流程|玩家流程|流程说明|新手流程|新手指南|入门指南|怎么开始(?:玩|游戏|跑团)?|如何开始|接下来.{0,5}(?:做什么|干什么|怎么办)|下一步.{0,5}(?:做什么|干什么|怎么)|现在.{0,5}(?:该做什么|该干嘛)|怎么认领|如何选角|怎么选角色|这个游戏怎么玩|这游戏怎么玩',t))

def next_step(save,engine,key,actor,templates,entry_open,story_open):
 data=save.read(key);s=data['snapshot'];gate=save.gate_state(key)
 with engine.db() as c:r=c.execute('SELECT v FROM sessions WHERE k=?',(key,)).fetchone()
 session=json.loads(r[0]) if r else None
 if gate['paused'] or s['world']['scene_status']=='paused' or session and session.get('paused'):
  return '目前处于暂停。你可以看资料或聊天，先不要进行游戏行动。恢复需要相关玩家明确同意及管理员授权，不用公开解释暂停原因。'
 owned=[(cid,r) for cid,r in s['roles'].items() if r['qq_user_id']==actor]
 if len(owned)>1:return '你的角色归属需要管理员核对，先不要重复认领或确认。'
 if owned:
  cid,role=owned[0];name=templates[cid]['name'];status=role['status']
  if status=='pending':return f'你的{name}已经登记成功，现在只需等待管理员安排安全入场。可以先问角色能力或出身背景，不用重新认领，也不要再发一次确认。'
  if status in ('away','retired'):return f'你的{name}目前'+('暂离。' if status=='away' else '已退团。')+'先联系管理员安排回归或后续角色，不直接进入当前行动。'
  if not story_open or not session or session.get('onboarding_only'):return f'你的{name}已在册，但当前还没有可继续的演练场景。先等管理员核对开场，不要重复认领。'
  if session.get('complete'):return '这段任务已告一段落。可以回顾经过或申请结算，奖励仍需按原有确认和审核流程处理。'
  if cid not in session.get('roles',{}):return '你已在册，但尚未加入当前任务。等待管理员安排合理的安全节点。'
  if cid in session.get('pending',{}):return '刚才的检定还没有完成，先等那次结果。如果没收到回复，可以重发原来的话取回，不要另起一次掷骰。'
  if cid in session.get('done',[]):
   waiting=[templates[x]['name'] for x in session['roles'] if x not in session.get('done',[]) and x in templates]
   return '你这轮已经行动过了，先等'+('、'.join(waiting) if waiting else '同伴')+'的选择；可以聊天，不用重复行动。'
  return f'你的{name}这轮还可以行动。直接描述想做什么、怎么做；如果不清楚现场，先问我情况。'
 if not entry_open:return '本群现在可以看角色、读介绍和聊天，认领还没开放。你先挑喜欢的卡，请管理员开放入口后再申请；现在不用回复“确认”。'
 with save._db() as c:
  p=c.execute("SELECT * FROM neon_enrollment_proposals WHERE k=? AND owner=? AND kind='claim' AND status='pending' ORDER BY rowid DESC LIMIT 1",(key,actor)).fetchone()
  if p:
   p=dict(p);valid=(p['expires']>time.time() and p['revision']==data['revision'] and p['epoch']==gate['epoch'] and p['digest']==save._enroll_digest(p))
   if not valid:return '上次角色预览已失效。重新说你想选哪张卡，再核对新预览；旧确认不会沿用。'
   consent=c.execute('SELECT 1 FROM neon_enrollment_consents WHERE pid=? AND actor=? AND digest=?',(p['id'],actor,p['digest'])).fetchone()
   if consent:return '你已确认这份角色预览，接下来等待审核结果；不用重复认领。'
   return f"你已经拿到{templates[p['cid']]['name']}的预览。确认内容无误后，用申请时同一个账号回复“确认”；这一步只确认你自己的申请。"
 if len(s['roles'])>=10:return '本群的预设角色目前都已有归属，请管理员安排候补；不要重复申请已占用角色。'
 return '你还没选角色。先问“有什么角色可以选”，选中后说出名字或编号，例如“我想玩02号虹膜”。'

def player_flow(save,engine,key,actor,templates,entry_open,story_open):
 return '你现在可以这样开始：\n'+next_step(save,engine,key,actor,templates,entry_open,story_open)+'\n\n'+STEPS
