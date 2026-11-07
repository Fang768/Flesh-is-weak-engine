"""Event-sourced noncombat narrative MVP; all lasting effects need native approval."""
import copy,hashlib,json,math,re,sqlite3,time,uuid
from pathlib import Path
from ..appearance import appearance_reference

class NarrativeError(ValueError):pass
OUTLINE=json.loads(Path(__file__).with_name('act1.json').read_text())
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def adaptation(roles):
 n=len(roles)
 if not 1<=n<=10:raise NarrativeError('仅支持1—10名实际出场玩家')
 return dict(participants=n,parallel_goals=1 if n<4 else 2 if n<7 else 3,recommended_teams=1 if n<=5 else 2,ordinary_enemy_cap=min(4,max(1,math.ceil(n/2))),noncombat_exit_required=True,change_numeric_stats=False,player_choices_preserved=True)

class NarrativeEngine:
 def __init__(self,path,save):
  self.path=str(path);self.save=save
  with self.db() as c:c.executescript('''CREATE TABLE IF NOT EXISTS sessions(k TEXT PRIMARY KEY,v TEXT);
CREATE TABLE IF NOT EXISTS journal(id INTEGER PRIMARY KEY,k TEXT,detail TEXT);
CREATE TABLE IF NOT EXISTS gate_reservations(k TEXT,node TEXT,operation TEXT,round INTEGER,phase TEXT,PRIMARY KEY(k,node));
CREATE TABLE IF NOT EXISTS advisories(k TEXT,round INTEGER,body TEXT,PRIMARY KEY(k,round));
CREATE TABLE IF NOT EXISTS requests(k TEXT,actor TEXT,mid TEXT,hash TEXT,phase TEXT,response TEXT,epoch INTEGER,PRIMARY KEY(k,actor,mid));''')
 def db(self):
  c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row;return c
 def read(self,k):
  with self.db() as c:r=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()
  return json.loads(r[0]) if r else None
 def write(self,c,k,s,event):
  c.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)',(k,canonical(s)));c.execute('INSERT INTO journal(k,detail) VALUES(?,?)',(k,canonical(event)))
 def running(self,k,s=None):
  if self.save.gate_state(k)['paused']:raise NarrativeError('当前暂停，剧情不会继续。')
  if s and self.save.read(k)['revision']!=s['revision']:raise NarrativeError('存档版本已变化，先核对待审核结果；不会重掷或覆盖已有成果。')
 def prepare(self,k,actor,is_admin=False):
  if is_admin is not True:raise NarrativeError('请由AstrBot管理员发起开场。')
  self.running(k);g=self.save.read(k);roles={cid:r for cid,r in g['snapshot']['roles'].items() if r['status']=='active' and r['hp']>0}
  if g['snapshot']['world']['scene_status']!='active':raise NarrativeError('当前尚未安全开场。先完成现有安全入场审批；不会自动激活角色。')
  adaptation(roles)
  if len({r['qq_user_id'] for r in roles.values()})!=len(roles):raise NarrativeError('角色归属不唯一，先由管理员核对。')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE')
   if c.execute('SELECT 1 FROM sessions WHERE k=?',(k,)).fetchone():raise NarrativeError('已有剧情记录，不覆盖或重复开场。')
   s=dict(id=uuid.uuid4().hex,revision=g['revision'],roster={cid:dict(user=r['qq_user_id'],team='A',away=False,position='contact') for cid,r in roles.items()},ready=[],started=False,round=1,done=[],events=[],facts=['储物柜位置公开'],clues={},chip=False,chip_holder=None,key=False,choices={},alert=g['snapshot']['world']['alert_level'],goal='未决定是否接受委托',npc_attitudes={'mira':'尚未接洽','custodian':'正常营业'},npc_actions=[],approach_history=[],clock=0,proposal=None,pending_state=None,checkpoint_generation=0,attempts={},summaries=[],last_declared=None)
   self.write(c,k,s,dict(kind='prepare',actor=actor,participants=len(roles)))
  return '这是一段大纲驱动的非战斗试玩。请每位已出场玩家各自说“准备好了”；未确认前不会开场。'
 def owner(self,s,actor):
  ids=[cid for cid,r in s['roster'].items() if r['user']==actor]
  if len(ids)!=1:raise NarrativeError('只能操作自己已经安全出场的角色。')
  return ids[0]
 def ready(self,k,actor):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=json.loads(c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()[0]);self.running(k,s);cid=self.owner(s,actor)
   if cid in s['ready']:return '你已经准备好了，不会重复开场。'
   s['ready'].append(cid)
   s['started']=len(s['ready'])==len(s['roster']);self.write(c,k,s,dict(kind='ready',cid=cid))
  return '大家都准备好了。你们在沃森歌舞伎接到一份取回芯片的委托。米拉等待你们的回应；你们可以问条件、接下，也可以拒绝或另作打算。' if s['started'] else '已记下，等其他人也准备好。'
 def view(self,k,actor):
  s=self.read(k)
  if not s:raise NarrativeError('尚未发起动态剧情。')
  cid=self.owner(s,actor);team=s['roster'][cid]['team'];game=self.save.read(k)['snapshot'];r=game['roles'][cid]
  recent=[]
  for summary in s['summaries'][-3:]:recent.append(dict(round=summary['round'],events=[e for e in summary['events'] if e.get('team') in ('public',team)]))
  with self.db() as c:advisory=c.execute('SELECT body FROM advisories WHERE k=? AND round=?',(k,s['round'])).fetchone()
  public_outline={k:copy.deepcopy(OUTLINE[k]) for k in ('title','immutable_public_facts','conflicts','consequence_policy')}
  public_outline['npcs']={x:dict(name=v['name'],public_goal=v['public_goal']) for x,v in OUTLINE['npcs'].items()}
  return dict(outline=public_outline,round=s['round'],goal=s['goal'],position=s['roster'][cid]['position'],party=[dict(cid=x,team=v['team'],position=v['position'],hp=game['roles'][x]['hp'],ram=game['roles'][x]['ram'],skills=game['roles'][x]['skills'],injuries=game['roles'][x]['injuries'],status=game['roles'][x]['status']) for x,v in s['roster'].items() if not v['away']],own_character=dict(cid=cid,attributes=r['attributes'],skills=r['skills'],hp=r['hp'],cash=r['cash'],appearance=appearance_reference(cid,self.save.character_profile(k,cid)['details'])),adaptation=adaptation({x:v for x,v in s['roster'].items() if not v['away']}),confirmed_facts=s['facts'],known_clues={x:v['text'] for x,v in s['clues'].items() if v['team'] in ('public',team)},npc_attitudes=s['npc_attitudes'],npc_public_actions=s.get('npc_actions',[]),approach_history=s.get('approach_history',[])[-5:],recent_summary=recent,scene_suggestions=json.loads(advisory[0]) if advisory else None,waiting=[x for x,v in s['roster'].items() if not v['away'] and x not in s['done']])
 def change_participation(self,k,actor,away=False,team=None):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=json.loads(c.execute('select v from sessions where k=?',(k,)).fetchone()[0]);self.running(k,s);cid=self.owner(s,actor)
   if s['events'] or s['proposal']:raise NarrativeError('等当前行动窗口和审核完成后，在安全节点调整。')
   if away and sum(not x['away'] for x in s['roster'].values())<=1:raise NarrativeError('最后一位玩家请用暂停，不自动让世界继续。')
   if team is not None and team not in ('A','B'):raise NarrativeError('首版仅支持A、B两队，由每人自行选择。')
   s['roster'][cid]['away']=away
   if team:s['roster'][cid]['team']=team
   self.write(c,k,s,dict(kind='participation',cid=cid,away=away,team=team))
  return '已调整后续窗口安排，之前的行动与线索保持不变。真实角色暂离状态仍按原存档审核流程处理。'
 def action(self,k,actor,mid,text,operation,node='contact',declared=False,expected_round=None,expected_epoch=None):
  if operation not in ('talk','observe','move','unlock','buy_key','read_chip','share_clue','accept','decline','end','improvise','review_camera','review_account','review_locker'):raise NarrativeError('该裁定需要先核对规则，未执行。')
  if operation in ('unlock','buy_key') and declared is not True:raise NarrativeError('必须先把确定的方法和DV发送给玩家，再执行骰点。')
  if operation=='improvise':raise NarrativeError('这个新方法可以讨论。请先说明对象、依据和风险；未占用本轮行动，也没有新造DV。')
  if not isinstance(mid,str) or not mid or not isinstance(text,str) or len(text)>2000:raise NarrativeError('消息无效。')
  hashed=digest(dict(text=text,operation=operation,node=node));epoch=self.save.gate_state(k)['epoch']
  if expected_epoch is not None and epoch!=expected_epoch:raise NarrativeError('暂停恢复后旧行动失效，不重新执行。')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM requests WHERE k=? AND actor=? AND mid=?',(k,actor,mid)).fetchone()
   if old:
    if old['hash']!=hashed:raise NarrativeError('重复消息内容冲突。')
    if old['phase']=='done':return json.loads(old['response'])
    raise NarrativeError('上次执行中断，保留骰点记录待管理员核对；不会重新掷骰。')
   s=json.loads(c.execute('select v from sessions where k=?',(k,)).fetchone()[0]);self.running(k,s);cid=self.owner(s,actor)
   if expected_round is not None and s['round']!=expected_round:raise NarrativeError('行动窗口已变化，请重新核对你的选择。')
   if not s['started']:raise NarrativeError('先等参与玩家各自准备好。')
   if s['proposal']:raise NarrativeError('当前窗口的变化正在等待本人确认及管理员审核。')
   if s['roster'][cid]['away'] or cid in s['done']:raise NarrativeError('本轮已行动或暂离；不会跳过同伴或重复执行。')
   if c.execute("SELECT 1 FROM requests WHERE k=? AND actor=? AND phase='in_flight'",(k,actor)).fetchone():raise NarrativeError('有执行中断的证据待核对，不接受新的行动来绕过。')
   if operation=='read_chip' and (not s['chip'] or s.get('chip_holder')!=cid):raise NarrativeError('你还没有芯片访问权，先取得或请求同伴明确分享；未占用行动。')
   if operation in OUTLINE['gates'] and (s['chip'] or operation+':'+node in s.get('attempts',{})):raise NarrativeError('这个对象已经处理或已经尝试过同样方法，不能免费重掷；换方法或资源须先核对。')
   if operation=='buy_key':
    money=self.save.read(k)['snapshot']['roles'][cid]['cash']
    if type(money) is not int or money<100:raise NarrativeError('资金不足或未确认，未占用行动。')
   if operation in OUTLINE['gates'] and node!='locker':raise NarrativeError('其他对象的锁需要先核对裁定，未占用行动。')
   if operation=='move' and node=='free' and (re.search(r'太平洲|圣多明戈|市中心|恶土|日本街|海伍德',text) or not re.search(r'附近|街口|街上|街边|隔壁|周围|周边|旁边|路边|出门',text)):raise NarrativeError('可以改变目的地。请先说明行程范围、交通方式和可能费用；不擅自瞬移或扣费。')
   if operation in OUTLINE['gates']:
    reservation=c.execute('SELECT * FROM gate_reservations WHERE k=? AND node=?',(k,node)).fetchone()
    if reservation and (reservation['phase']=='in_flight' or reservation['round']==s['round']):raise NarrativeError('同一对象已有检定在处理，先核对那次结果，不并发刷骰。')
    c.execute('INSERT OR REPLACE INTO gate_reservations VALUES(?,?,?,?,?)',(k,node,operation,s['round'],'in_flight'))
   c.execute('insert into requests values(?,?,?,?,?,?,?)',(k,actor,mid,hashed,'in_flight','',epoch))
  # A crash after this point is fail-closed. Native roll evidence is retained; never auto-reroll.
  result=dict(cid=cid,team=s['roster'][cid]['team'],operation=operation,node=node,player_action=text,check=None,effects=[],pending_approval=True)
  if operation in ('unlock','buy_key'):
   if node!='locker':raise NarrativeError('原文固定检定仅适用于储物柜；其他对象请先核对裁定。')
   gate=OUTLINE['gates'][operation];role=self.save.read(k)['snapshot']['roles'][cid]
   if operation=='buy_key' and (type(role['cash']) is not int or role['cash']<100):raise NarrativeError('资金不足或未确认，不能购买钥匙。')
   # The caller must deliver the exact program declaration before calling action.
   die=self.save.roll(k,actor,cid,'check',1,10);value=die['dice'][0];mod=-2 if operation=='buy_key' and role['stress']>=4 else 0;total=value+role['attributes'][gate['attribute']]+role['skills'].get(gate['skill'],0)+mod
   result['check']=dict(roll_id=die['id'],die=value,attribute=gate['attribute'],attribute_value=role['attributes'][gate['attribute']],skill=gate['skill'],skill_value=role['skills'].get(gate['skill'],0),modifier=mod,dv=gate['dv'],total=total,success=total>=gate['dv'])
   result['effects']=['chip_access'] if total>=14 and operation=='unlock' else ['key_purchase_pending'] if total>=14 else ['alert_plus_one']
  elif operation=='read_chip':
   if not s['chip'] or s.get('chip_holder')!=cid:result['effects']=['need_chip_access']
   else:result['effects']=['reveal_chip_clue'];result['disclosure']=OUTLINE['clues']['chip']['text']
  elif operation=='decline':result['effects']=['respect_decline']
  elif operation=='accept':result['effects']=['accept_own_contract']
  elif operation=='share_clue':
   team=s['roster'][cid]['team'];result['effects']=['share_known_clues'] if any(v['team'] in (team,'public') for v in s['clues'].values()) else ['no_known_clue_to_share']
  elif operation=='move' and node=='free':result['effects']=['local_movement_pending']
  elif operation.startswith('review_'):result['effects']=[operation+'_access_requires_manual_ruling']
  elif operation=='improvise':result['effects']=['novel_method_needs_review']
  self.running(k,s)
  if self.save.gate_state(k)['epoch']!=epoch:raise NarrativeError('暂停状态已变化，本次结果不再推进；骰点证据保留。')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');fresh=json.loads(c.execute('select v from sessions where k=?',(k,)).fetchone()[0])
   if fresh['round']!=s['round'] or fresh['proposal']:raise NarrativeError('行动窗口变化，保留证据等待核对。')
   fresh['events'].append(result);fresh['done'].append(cid);
   if result['check']:
    fresh.setdefault('attempts',{})[operation+':'+node]=result['check']['roll_id'];c.execute("UPDATE gate_reservations SET phase='done' WHERE k=? AND node=?",(k,node))
   self.write(c,k,fresh,dict(kind='action',result=result));c.execute("update requests set phase='done',response=? where k=? and actor=? and mid=?",(canonical(result),k,actor,mid))
  return result
 def declaration(self,operation):
  g=OUTLINE['gates'][operation];return f"这需要{g['skill']}检定：1d10 + {g['attribute']} + {g['skill']} + 适用修正，DV{g['dv']}。"+('若交易完成，钥匙费用100€$仍需本人确认存档。' if operation=='buy_key' else '')
 def checkpoint(self,k):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=json.loads(c.execute('select v from sessions where k=?',(k,)).fetchone()[0]);return self._checkpoint(k,s,c)
 def _checkpoint(self,k,s,c):
  self.running(k,s)
  waiting=[cid for cid,v in s['roster'].items() if not v['away'] and cid not in s['done']]
  if waiting:return dict(status='waiting',waiting=waiting)
  if s['proposal']:return dict(status='pending',proposal=s['proposal'])
  next_state=copy.deepcopy(s);next_state['manual_ruling_required']=False;operations=[];descriptions=[]
  for e in s['events']:
   cid=e['cid'];team=s['roster'][cid]['team'];op=e['operation']
   descriptions.append(cid+'：'+op)
   if op=='move' and e['node'] in OUTLINE['nodes']:next_state['roster'][cid]['position']=e['node']
   elif op=='move' and e['node']=='free':next_state['roster'][cid]['position']='歌舞伎自由探索：'+e['player_action'][:120]
   for effect in e['effects']:
    if effect=='chip_access':next_state['chip']=True;next_state['chip_holder']=cid;next_state['roster'][cid]['position']='locker';next_state['facts'].append('储物柜已解锁，可接触芯片')
    elif effect=='key_purchase_pending':
     next_state['key']=True;next_state['chip']=True;next_state['chip_holder']=cid;next_state['roster'][cid]['position']='locker';next_state['facts'].append('购买钥匙并取得芯片访问权');operations.append(dict(kind='resource',cid=cid,field='cash',delta=-100,reason='第一幕钥匙交易；骰点见动态剧情日志'))
    elif effect=='alert_plus_one':next_state['alert']+=1;next_state['facts'].append('储物柜尝试失败，巡逻警戒增加')
    elif effect=='respect_decline':next_state['choices'][cid]='拒绝本人的委托';next_state['goal']='成员可以有不同目标，以各自明确选择为准';next_state['npc_attitudes']['mira']='尊重拒绝，保留联系'
    elif effect=='accept_own_contract':next_state['choices'][cid]='接受本人的委托';next_state['goal']='以成员各自的明确选择为准，承诺报酬不视为已到账'
    elif effect=='share_known_clues':
     for clue in next_state['clues'].values():
      if clue['team']==team:clue['team']='public'
    elif effect=='reveal_chip_clue':next_state['clues']['chip']=dict(text=OUTLINE['clues']['chip']['text'],team=team)
    elif effect.startswith('review_'):
     source=op.removeprefix('review_');next_state['manual_ruling_required']=True
     if source=='locker':next_state['chip']=True;next_state['chip_holder']=cid;next_state['facts'].append('经玩家和管理员核对的合法芯片访问依据：'+e['player_action'][:150])
     else:next_state['clues'][source]=dict(text=OUTLINE['clues'][source]['text'],team=team)
  next_state['clock']+=1
  next_state['npc_attitudes']['custodian']='继续正常营业，未自动发起攻击'
  npc_actions=[]
  if any(e['operation']=='decline' for e in s['events']):npc_actions.append(dict(npc='mira',motive='尊重尚未订约者的选择',action='不催促拒绝者，保留自愿联络',cause='本轮明确拒绝'))
  if any(e['operation']=='accept' for e in s['events']):
   next_state['npc_attitudes']['mira']='愿意与明确接约者保持联络，也尊重其他成员决定'
   npc_actions.append(dict(npc='mira',motive='希望取回证据',action='再次说明已经公开的储物柜位置，不预付未审核资金',cause='本轮有人自愿接约'))
  if next_state['alert']>s['alert']:npc_actions.append(dict(npc='custodian',motive='维持营业',action='留意柜区动静；没有自动发动攻击',cause='已执行尝试引起警戒'))
  if any(e['operation']=='talk' and e['node']=='contact' for e in s['events']):
   next_state['approach_history'].append('本轮通过公开交谈了解委托，没有自动取得额外金钱或情报')
  next_state['npc_actions']=npc_actions

  recap=('动态第一幕窗口'+str(s['round'])+'；'+'；'.join(descriptions)+'；目标：'+next_state['goal'])[:240]
  reference=digest(dict(events=s['events'],state=next_state))[:20];recap+='；事件校验 '+reference
  operations+= [dict(kind='world',field='recap',value=recap,reason='动态剧情窗口结算；完整事件保留GM日志'),dict(kind='world',field='scene',value='动态非战斗场景：各角色位置见已审核剧情日志',reason='动态非战斗场景'),dict(kind='world',field='alert_level',value=next_state['alert'],reason='第一幕已执行检定的后果'),dict(kind='world',field='action_window',value='动态窗口'+str(s['round']+1),reason='所有实际参与者均已回应')]
  if len(operations)>10:raise NarrativeError('本轮交易过多，需管理员拆分核对；不自动写入。')
  actor='system:dne:'+s['id']+':'+str(s['round'])+':'+str(s.get('checkpoint_generation',0));payload=dict(kind='batch',operations=operations)
  # Recover a proposal created before a crash; no second competing approval ticket.
  with self.save._db() as native:prior=native.execute("SELECT id,digest,payload,status FROM neon_v020_proposals WHERE group_key=? AND actor=?",(k,actor)).fetchall()
  if len(prior)>1:raise NarrativeError('存在冲突提案，先由管理员核对。')
  if prior:
   row=prior[0]
   if json.loads(row['payload'])!=payload:raise NarrativeError('旧提案与本轮证据不一致。')
   proposal=dict(id=row['id'],digest=row['digest'])
  else:
   p=self.save.propose(k,actor,s['revision'],payload);proposal=dict(id=p['id'],digest=p['digest'])
  fresh=s;fresh['proposal']=proposal;fresh['pending_state']=next_state;self.write(c,k,fresh,dict(kind='checkpoint',proposal=proposal,reference=reference,events=s['events'],proposed_state=next_state,source_revision=s['revision']))
  return dict(status='pending',proposal=proposal)
 def auto_continue(self,k):
  policy=self.save.data_dir/'narrative_auto.json'
  try: allowed=json.loads(policy.read_text()).get('groups',[])
  except (OSError,ValueError):return False
  if k not in allowed:return False
  s=self.read(k)
  if not s or not s.get('proposal') or s.get('pending_state',{}).get('manual_ruling_required'):return False
  p=s['proposal']
  with self.save._db() as db:row=db.execute('SELECT p.payload,p.status,p.expires,g.epoch FROM neon_v020_proposals p LEFT JOIN neon_safety_proposals g ON g.pid=p.id WHERE p.id=? AND p.group_key=?',(p['id'],k)).fetchone()
  if not row:return False
  ops=json.loads(row['payload']).get('operations',[])
  if not ops or any(op.get('kind')!='world' for op in ops):return False
  if row['status']=='committed':return bool(self.sync(k))
  if row['status']!='pending':raise NarrativeError('原剧情提案被撤销，需核对原因；不自动重建。')
  if row['epoch']!=self.save.gate_state(k)['epoch']:raise NarrativeError('暂停前的旧行动不自动恢复，请先核对恢复点。')
  self.running(k,s)
  if row['expires']<time.time():
   self.rebuild_checkpoint(k,'system:narrative-expiry-recovery',True)
   s=self.read(k);p=s['proposal']
  self.save.approve_narrative(k,p['id'],p['digest']);self.sync(k);return True
 def consent(self,k,actor):
  s=self.read(k);self.owner(s,actor)
  if not s['proposal']:raise NarrativeError('没有待确认的剧情窗口。')
  p=s['proposal'];self.save.consent(k,actor,p['id'],p['digest']);return '你的确认收到了，等其他人和管理员点头。'
 def approve(self,k,actor,is_admin=False,manual_ruling=False):
  s=self.read(k)
  if not s['proposal']:raise NarrativeError('没有待审核剧情。')
  if s['pending_state'].get('manual_ruling_required') and manual_ruling is not True:raise NarrativeError('这轮涉及原文未细化的访问条件。管理员先核对依据和必要检定，再明确说批准临场裁定。')
  p=s['proposal'];self.save.approve(k,actor,p['id'],p['digest'],is_admin);return self.sync(k)
 def sync(self,k):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone();s=json.loads(row[0]) if row else None
   if not s or not s['proposal']:return False
   with self.save._db() as native:r=native.execute('SELECT status,digest FROM neon_v020_proposals WHERE id=? AND group_key=?',(s['proposal']['id'],k)).fetchone()
   if not r or r['status']!='committed' or r['digest']!=s['proposal']['digest']:return False
   fresh=s['pending_state'];fresh['revision']=self.save.read(k)['revision'];fresh['summaries'].append(dict(round=s['round'],proposal=copy.deepcopy(s['proposal']),events=[dict(cid=e['cid'],team=e.get('team','A'),operation=e['operation'],effects=e['effects'],player_action=e['player_action'][:200]) for e in s['events']]))
   fresh['summaries']=fresh['summaries'][-20:];fresh['approach_history']=fresh.get('approach_history',[])[-20:]
   fresh.update(round=s['round']+1,done=[],events=[],proposal=None,pending_state=None)
   self.write(c,k,fresh,dict(kind='confirmed_checkpoint',proposal=s['proposal']))
  disclosed=[v['text'] for name,v in fresh['clues'].items() if name not in s['clues']]
  return '这段就按大家确认的结果继续。刚才取得的线索和成果都在。'+('\n本轮实际核对取得的线索：'+'；'.join(disclosed) if disclosed else '')

 def reconcile(self,k,actor,is_admin=False):
  if is_admin is not True:raise NarrativeError('请管理员在安全节点核对成员。')
  self.running(k)
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone();s=json.loads(row[0]) if row else None
   if not s or s['events'] or s['proposal'] or c.execute("SELECT 1 FROM requests WHERE k=? AND phase='in_flight'",(k,)).fetchone():raise NarrativeError('只能在没有待处理行动和提案的安全节点调整。')
   game=self.save.read(k);roles=game['snapshot']['roles'];active={cid:r for cid,r in roles.items() if r['status']=='active' and r['hp']>0};adaptation(active)
   if len({r['qq_user_id'] for r in active.values()})!=len(active):raise NarrativeError('身份绑定不唯一。')
   for cid,r in s['roster'].items():
    if cid in roles and roles[cid]['qq_user_id']!=r['user']:raise NarrativeError('角色归属变化，禁止自动继承其他人的剧情。')
    r['away']=cid not in active
   for cid,r in active.items():
    if cid not in s['roster']:
     s['roster'][cid]=dict(user=r['qq_user_id'],team='A',away=False,position='contact');s['started']=False
   s['revision']=game['revision'];self.write(c,k,s,dict(kind='safe_boundary_roster_sync',actor=actor))
  return '已按真实出场状态核对后续人数；新人仍需本人说准备好了。已有事实和选择不重置。'

 def rebuild_checkpoint(self,k,actor,is_admin=False):
  if is_admin is not True:raise NarrativeError('请管理员核对失效提案后重建。')
  self.running(k)
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=json.loads(c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()[0]);self.running(k,s)
   if not s['proposal']:raise NarrativeError('没有待重建提案。')
   p=s['proposal']
   with self.save._db() as native:
    r=native.execute('SELECT p.status,p.expires,g.epoch FROM neon_v020_proposals p LEFT JOIN neon_safety_proposals g ON g.pid=p.id WHERE p.id=? AND p.group_key=?',(p['id'],k)).fetchone()
   if not r or r['status']=='committed':raise NarrativeError('先核对真实提交回执，不能重建已提交结果。')
   if r['status']=='pending' and r['expires']>time.time() and r['epoch']==self.save.gate_state(k)['epoch']:raise NarrativeError('原提案仍有效，不创建第二个竞争提案。')
   s['proposal']=None;s['pending_state']=None;s['checkpoint_generation']=s.get('checkpoint_generation',0)+1;self.write(c,k,s,dict(kind='rebuild_expired_checkpoint',actor=actor,old_proposal=p))
  return self.checkpoint(k)

 def save_advisory(self,k,actor,round_no,epoch,body):
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=json.loads(c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()[0]);self.running(k,s);self.owner(s,actor)
   if s['round']!=round_no or s['events'] or self.save.gate_state(k)['epoch']!=epoch:return False
   c.execute('INSERT OR REPLACE INTO advisories VALUES(?,?,?)',(k,round_no,canonical(body)));self.write(c,k,s,dict(kind='unconfirmed_scene_suggestions',body=body))
  return True
