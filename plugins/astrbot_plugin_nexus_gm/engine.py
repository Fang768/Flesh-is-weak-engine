"""Public rehearsal scenario, persistent isolated journal, no game-save mutation."""
import json, sqlite3, secrets, time, re, hashlib
from contextlib import nullcontext
from pathlib import Path
from .control import PauseControl

class GMError(ValueError): pass
SKILLS={'工程':'TECH','调查':'INT','交涉':'COOL','潜行':'COOL'}
SCENES=[('接洽','维修站请求找回误锁的公开设备记录。可以交涉、调查或确认结束。'),('取回','抵达无人值守的旧维修终端。可以工程、调查、潜行，或撤离。'),('交付','记录已取回，返回维修站交付。每位玩家确认结束后生成结算草案。')]
class Engine(PauseControl):
 control_error=GMError
 def __init__(self,path,save,clock=time.time,rand=secrets.randbelow):
  self.path=str(path);self.save=save;self.clock=clock;self.rand=rand
  with self.db() as c:c.executescript('''CREATE TABLE IF NOT EXISTS sessions(k TEXT PRIMARY KEY,v TEXT); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,k TEXT,detail TEXT);
CREATE TABLE IF NOT EXISTS requests(k TEXT,actor TEXT,mid TEXT,fingerprint TEXT,created REAL,phase TEXT,response TEXT,intent TEXT,PRIMARY KEY(k,actor,mid));
CREATE TABLE IF NOT EXISTS archives(id INTEGER PRIMARY KEY,k TEXT,v TEXT);
CREATE TABLE IF NOT EXISTS resume_requests(k TEXT,rid TEXT,body TEXT,PRIMARY KEY(k,rid));
CREATE TABLE IF NOT EXISTS resume_consents(k TEXT,rid TEXT,actor TEXT,digest TEXT,PRIMARY KEY(k,rid,actor));''')
  # Adopt pre-upgrade explicit pauses into the shared authoritative gate; never unpause on restart.
  with self.db() as c:
   for key,raw in c.execute('SELECT k,v FROM sessions').fetchall():
    state=json.loads(raw)
    pauses=[x for x in state.get('log',[]) if x.get('method')=='pause']
    if state.get('paused') and pauses and state['log'][-1].get('method')=='pause' and not self.save.gate_state(key)['paused']:
     cid=pauses[-1]['cid'];actor=state['roles'].get(cid,{}).get('qq_user_id','')
     self.save.pause_group(key,actor,'legacy:'+state['task']+':'+pauses[-1].get('message_id',''))
 def ensure_onboarding_control(self,k):
  """Explicit safety-only checkpoint for a group with no running story.
  No first act, scene, dice or game-save mutation is created here.
  """
  if not self.save.gate_state(k)['paused']:raise GMError('当前未安全暂停，不创建登记控制点')
  game=self.save.read(k)
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');prior=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()
   if prior:
    s=json.loads(prior[0])
    if not s.get('onboarding_only'):return
    if s.get('resume_request'):return
   else:s=dict(stage=0,round=0,done=[],pending={},seen=[],log=[],complete=False,proposed=False,task='onboarding-control',success=False,onboarding_only=True)
   if not self.save.gate_state(k)['paused']:raise GMError('暂停状态已变化，未创建控制点')
   game=self.save.read(k)
   s['revision']=game['revision'];s['roles']={cid:r for cid,r in game['snapshot']['roles'].items() if r['status']!='retired'};s['paused']=True
   self.write(c,k,s,{'onboarding_safety_checkpoint':True})
 def onboarding_pause(self,k,actor,mid):
  with self.db() as c:
   prior=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()
   if prior:
    s=json.loads(prior[0])
    # Replayed pauses must retain the original receipt, including after resume.
    replay=c.execute('SELECT 1 FROM requests WHERE k=? AND actor=? AND mid=?',(k,actor,mid)).fetchone()
    if not s.get('onboarding_only') or replay:return self.natural(k,actor,mid,'【暂停】',intent_override='pause')
  roles=self.save.read(k)['snapshot']['roles']
  if not any(r['qq_user_id']==actor and r['status']!='retired' for r in roles.values()):raise GMError('须由本人已登记角色发起安全暂停')
  self.save.pause_group(k,actor,'natural:'+mid+':declare')
  # A fresh pause revokes every older resume request before updating the roster.
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');fresh=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()
   if fresh:
    s=json.loads(fresh[0])
    if s.get('onboarding_only'):
     s['resume_request']=None;self.write(c,k,s,{'onboarding_new_pause':mid})
  self.ensure_onboarding_control(k)
  return self.natural(k,actor,mid,'【暂停】',intent_override='pause')
 def db(self):return sqlite3.connect(self.path,timeout=10)
 def load(self,c,k):
  row=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()
  if not row:raise GMError('本群未启用隔离演练')
  s=json.loads(row[0]);s['paused']=bool(s['paused'] or self.save.gate_state(k)['paused']);return s
 def write(self,c,k,s,event):
  c.execute('INSERT OR REPLACE INTO sessions VALUES(?,?)',(k,json.dumps(s,ensure_ascii=False)))
  c.execute('INSERT INTO events(k,detail) VALUES(?,?)',(k,json.dumps(event,ensure_ascii=False)))
 def open(self,k,admin=False):
  if not admin:raise GMError('仅管理员可启用')
  self.assert_epoch(k)
  game=self.save.read(k);roles=game['snapshot']['roles'];ids=[x for x,r in roles.items() if r['status']=='active' and r['hp']>0]
  if not 1<=len(ids)<=10 or game['snapshot']['world']['scene_status']!='active':raise GMError('需已开场及1—10名未倒地出场角色')
  if len({roles[x]['qq_user_id'] for x in ids})!=len(ids):raise GMError('玩家身份绑定不唯一')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE')
   prior=c.execute('SELECT v FROM sessions WHERE k=?',(k,)).fetchone()
   if prior:
    previous=json.loads(prior[0])
    withdrawn=previous.get('paused') and previous.get('log') and previous['log'][-1].get('method')=='withdraw'
    if not previous['complete'] and not withdrawn and not previous.get('onboarding_only'):raise GMError('已有未完成演练，禁止覆盖')
    c.execute('INSERT INTO archives(k,v) VALUES(?,?)',(k,prior[0]))
   s=dict(revision=game['revision'],roles={x:roles[x] for x in ids},stage=0,round=1,done=[],pending={},seen=[],log=[],paused=False,complete=False,proposed=False,task='nexus-demo-'+secrets.token_hex(6),success=False)
   self.write(c,k,s,{'open':True})
  return SCENES[0][1]
 def view(self,k):
  with self.db() as c:s=self.load(c,k)
  return {'task':s['task'],'scene':('未开场（登记安全控制）' if s.get('onboarding_only') else SCENES[s['stage']][0]),'round':s['round'],'waiting':[x for x in s['roles'] if x not in s['done']], 'paused':s['paused'],'complete':s['complete'],'resume_request':s.get('resume_request'),'epoch':self.epoch(k),'recap':s['log'][-10:]}
 def action(self,k,actor,message_id,text,intent,_db=None,expected_epoch=None):
  with (nullcontext(_db) if _db else self.db()) as c:
   if _db is None:c.execute('BEGIN IMMEDIATE')
   s=self.load(c,k)
   if s.get('onboarding_only') and intent!='pause':raise GMError('本群仅有登记安全控制，剧情尚未开始')
   if intent!='pause' and self.save.read(k)['revision']!=s['revision']:raise GMError('真实存档已变化，暂停演练并重新核对')
   if intent!='pause':self.assert_epoch(k,expected_epoch)
   ids=[x for x,r in s['roles'].items() if r['qq_user_id']==actor]
   if not ids and intent=='pause':ids=[x for x,r in self.save.read(k)['snapshot']['roles'].items() if r['qq_user_id']==actor and r['status']!='retired']
   if len(ids)!=1:raise GMError('只能操作本人已绑定出场角色')
   cid=ids[0]; action_stage=s['stage']; action_round=s['round']
   if not message_id:raise GMError('缺少平台消息ID，拒绝防重失效')
   if message_id in s['seen']:
    prior=next((e for e in s['log'] if e.get('message_id')==message_id and e['cid']==cid),None)
    if prior:return prior['result']
    raise GMError('旧消息已处理，禁止重掷')
   if len(text)>2000:raise GMError('行动过长')
   if intent=='pause':
    was_paused=s['paused'];g=self.save.pause_group(k,actor,message_id)
    s['pause_actors']=sorted((set(s.get('pause_actors',[])) if was_paused else set())|{actor})
    s['paused']=True;s['resume_request']=None;s['pause_epoch']=g['epoch']
    result='已暂停（安全锁定）；骰点、剧情及结算已锁定。无需解释原因。恢复须全员明确同意及管理员授权。'
   else:
    if s['paused'] or s['complete']:raise GMError('演练暂停或已完成')
    if cid in s['done']:raise GMError('本轮行动已完成，等待其他玩家')
    if intent=='roll':
     if cid not in s['pending']:raise GMError('须先声明行动并公布DV')
     check=s['pending'].pop(cid);die=self.rand(10)+1;r=s['roles'][cid];mod=-2 if check=='交涉' and r['stress']>=4 else 0
     total=die+r['attributes'][SKILLS[check]]+r['skills'].get(check,0)+mod;ok=total>=14
     if ok and s['stage']==1:s['success']=True
     check_record=dict(die=die,attribute=SKILLS[check],attribute_value=r['attributes'][SKILLS[check]],skill=check,skill_value=r['skills'].get(check,0),modifier=mod,dv=14,total=total,success=ok)
     result=f"{cid}：1d10={die} + {SKILLS[check]}{check_record['attribute_value']} + {check}{check_record['skill_value']} + 修正({mod}) = {total} / DV14，"+('成功' if ok else '失败；本轮不免费重试，可在下一窗口改用其他方法或撤离。')
     s['done'].append(cid)
    elif intent in SKILLS:
     if cid in s['pending']:raise GMError('已有待掷骰检定，请先处理')
     if intent=='交涉' and s['stage']!=0 or intent!='交涉' and s['stage']!=1:raise GMError('当前场景不支持该方法，请澄清')
     if any(e.get('cid')==cid and e.get('stage')==s['stage'] and e.get('method')==intent for e in s['log']):raise GMError('同一方法不可无成本重试，请改变方法')
     s['pending'][cid]=intent;result=f'{cid}声明{intent}，DV14，1d10+{SKILLS[intent]}+{intent}，无1/10自动成败。请本人明确发送【行动】掷骰。'
    elif intent=='end':
     if cid in s['pending']:raise GMError('请先解决待掷骰检定')
     s['done'].append(cid);result=f'{cid}确认结束，未代替其他玩家行动。'
    elif intent=='withdraw':s['paused']=True;result='已撤离并暂停，无任务完成结算。'
    else:raise GMError('无法安全识别，支持交涉/调查/工程/潜行/掷骰/确认结束/撤离；战斗请求交管理员，不自动裁定。')
    if len(s['done'])==len(s['roles']):
     if s['stage']==1 and not s['success']:
      s['round']+=1;s['done']=[];result+=' 所有人已回应；终端仍锁定，下一窗口可换方法。'
     elif s['stage']==2:s['complete']=True;result+=' 所有人已回应，模拟任务完成，可生成待审核草案。'
     else:
      s['stage']+=1;s['round']+=1;s['done']=[];result+=' '+SCENES[s['stage']][1]
   s['seen'].append(message_id)
   entry={'message_id':message_id,'check':check_record if intent=='roll' else None,'cid':cid,'round':action_round,'stage':action_stage,'method':intent,'text':text,'result':result};s['log'].append(entry)
   self.write(c,k,s,entry);return result
 def proposal(self,k,actor,admin=False):
  if not admin:raise GMError('仅管理员生成跨系统结算草案')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE')
   s=self.load(c,k)
   self.assert_epoch(k)
   if s.get('paused') or not s.get('success'):raise GMError('任务撤离、暂停或未成功，不生成完成结算')
   if s.get('proposal_result'):return s['proposal_result']
   if not s['complete'] or s['proposed']:raise GMError('任务未完成或旧草案已生成，请查询原审批记录')
   # Save schema supports audited world+mission batch; never approve or consent here.
   ops=[dict(kind='world',field='current_task_id',value=s['task'],reason='NEXUS隔离演练完成')]
   for cid in s['roles']:
    # A recorded actual check is participation evidence for player/admin review; waiting alone earns nothing.
    if not any(e['cid']==cid and e['method']=='roll' for e in s['log']):continue
    ops.append(dict(kind='mission',cid=cid,task=s['task'],meaningful_action='NEXUS演练公开检定与交付记录；需本人核对',completed=True,survived=True,actually_present=True,completion_evidence='演练日志任务 '+s['task']))
   if len(ops)>10:raise GMError('存档批次最多10项；10人任务需管理员分批结算，本MVP不自动拆批')
   if len(ops)==1:raise GMError('无可核对的有效参与记录')
   p=self.save.propose(k,actor,s['revision'],dict(kind='batch',operations=ops))
   s['proposed']=True;s['proposal_result']=p;self.write(c,k,s,{'proposal':p['id']});return p
 def classify(self,text):
  text=re.sub(r'【行动】|【确认结束】|\s+','',text)
  if any(x in text for x in ('不要','不想','不掷','别掷')):raise GMError('检测到否定表达，请明确想做什么；未执行')
  if '暂停' in text:return 'pause'
  if any(x in text for x in ('撤离','放弃任务')):return 'withdraw'
  if any(x in text for x in ('攻击','射击','开枪','杀死','威吓','黑客','破解','治疗','揭晓')):raise GMError('该动作超出现有演练范围，请管理员处理；未掷骰')
  choices=[]
  for intent,words in {'工程':('工程','撬开','维修','修理','拆开'),'调查':('调查','搜查','搜索','检查仓库'),'交涉':('交涉','谈判','询问','说服'),'潜行':('潜行','潜入','偷偷')}.items():
   if any(x in text for x in words):choices.append(intent)
  if len(choices)>1:raise GMError('有多个行动方法，请一次只声明一种')
  if choices:return choices[0]
  if '掷骰' in text:return 'roll'
  if any(x in text for x in ('确认结束','交付记录','交付设备记录','交回记录')):return 'end'
  raise GMError('请说明行动，例如“我要调查仓库”“我要撬开这扇门”，或“确认结束”；未执行检定')
 def natural(self,k,actor,mid,text,finish=False,intent_override=None,expected_epoch=None):
  """Durable response cache. Declaration is sent by adapter before finish rolls."""
  if not mid or len(text)>2000:raise GMError('消息ID缺失或行动过长')
  with self.db() as c:
   c.execute('BEGIN IMMEDIATE');s=self.load(c,k)
   if s.get('onboarding_only') and intent_override!='pause':raise GMError('本群仅有登记安全控制，剧情尚未开始')
   allowed=any(r['qq_user_id']==actor for r in s['roles'].values())
   if intent_override=='pause':allowed=allowed or any(r['qq_user_id']==actor and r['status']!='retired' for r in self.save.read(k)['snapshot']['roles'].values())
   if not allowed:raise GMError('只能操作本人已绑定角色')
   if intent_override!='pause':self.assert_epoch(k,expected_epoch)
   fingerprint=hashlib.sha256((s['task']+re.sub(r'【行动】|\s+','',text)).encode()).hexdigest()
   row=c.execute('SELECT mid,phase,response,intent FROM requests WHERE k=? AND actor=? AND mid=?',(k,actor,mid)).fetchone()
   if row is None and intent_override!='pause':
    row=c.execute('SELECT mid,phase,response,intent FROM requests WHERE k=? AND actor=? AND fingerprint=? AND created>=? ORDER BY created DESC LIMIT 1',(k,actor,fingerprint,self.clock()-120)).fetchone()
   if row:
    original,phase,response,intent=row
    if intent_override=='pause' and intent=='pause' and not self.is_paused(k):
     return dict(mid=original,phase='done',response='这条旧暂停消息已处理，当前已按授权恢复；如需再次暂停，请发送新的【暂停】消息。',replay=True)
    if phase=='done' or not finish:return dict(mid=original,phase=phase,response=response,replay=True)
    # Prepared declaration has been sent, resolve exactly once in the same transaction as cached response.
    result=self.action(k,actor,'natural:'+original+':roll',text,'roll',_db=c,expected_epoch=expected_epoch)
    response=response+'\n'+result
    c.execute("UPDATE requests SET phase='done',response=? WHERE k=? AND actor=? AND mid=?",(response,k,actor,original))
    return dict(mid=original,phase='done',response=response,replay=False)
   if finish:raise GMError('不存在已公布DV的行动')
   intent=intent_override if intent_override is not None else self.classify(text)
   if intent not in (*SKILLS,'end','pause','withdraw'):raise GMError('行动解释未通过程序白名单')
   response=self.action(k,actor,'natural:'+mid+':declare',text,intent,_db=c,expected_epoch=expected_epoch)
   if intent in SKILLS:response=response.replace('请本人明确发送【行动】掷骰。','程序已登记，将自动掷骰并回传判定。')
   phase='prepared' if intent in SKILLS else 'done'
   c.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',(k,actor,mid,fingerprint,self.clock(),phase,response,intent))
   return dict(mid=mid,phase=phase,response=response,replay=False)

 def cached(self,k,actor,mid,text):
  with self.db() as c:
   s=self.load(c,k)
   if not any(r['qq_user_id']==actor for r in s['roles'].values()):raise GMError('只能操作本人绑定角色')
   fingerprint=hashlib.sha256((s['task']+re.sub(r'【行动】|\s+','',text)).encode()).hexdigest()
   row=c.execute('SELECT mid,phase,response FROM requests WHERE k=? AND actor=? AND (mid=? OR (fingerprint=? AND created>=?)) ORDER BY created DESC LIMIT 1',(k,actor,mid,fingerprint,self.clock()-120)).fetchone()
   if row is None:
    cid=next(x for x,r in s['roles'].items() if r['qq_user_id']==actor)
    if cid in s['done'] or cid in s['pending']:
     # Keep same-window duplicate results available beyond the short text-dedup TTL.
     candidates=c.execute('SELECT mid,phase,response FROM requests WHERE k=? AND actor=? AND fingerprint=? ORDER BY created DESC',(k,actor,fingerprint)).fetchall()
     for candidate in candidates:
      if cid in s['pending'] and candidate[1]!='prepared':continue
      prior=next((x for x in s['log'] if x.get('message_id')=='natural:'+candidate[0]+':declare'),None)
      if prior and prior['stage']==s['stage'] and prior.get('round',s['round'])==s['round']:
       row=candidate;break
   return dict(mid=row[0],phase=row[1],response=row[2],replay=True) if row else None

 def cache_narration(self,k,actor,mid,description):
  with self.db() as c:
   c.execute("UPDATE requests SET response=response || ? WHERE k=? AND actor=? AND mid=? AND phase='done'",('\n镜头：'+description,k,actor,mid))
