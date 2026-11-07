"""Player-facing wording only. Stored results and authorization remain unchanged."""
import re

def player_text(text,names=None):
 names=names or {}
 def name(cid):return names.get(cid,cid+'号角色')
 text=re.sub(r'(?<!\d)(0[1-9]|10)(?=声明|确认结束|：)',lambda m:name(m[1]),text)
 text=re.sub(r'(.+?)声明(交涉|调查|工程|潜行)，DV14，(1d10\+[^，]+)，无1/10自动成败。程序已登记，将自动掷骰并回传判定。',r'这需要一次\2检定：\3，难度14。现在掷骰。',text)
 text=text.replace('确认结束，未代替其他玩家行动。','已结束这次行动，其他人的选择仍由本人决定。')
 text=text.replace('本轮不免费重试，可在下一窗口改用其他方法或撤离。','这次尝试没有奏效。等同伴行动后，可以换个办法，或选择撤离。')
 text=text.replace('所有人已回应；终端仍锁定，下一窗口可换方法。','大家都行动过了。终端仍然锁着，你们可以另想办法。')
 text=text.replace('所有人已回应，模拟任务完成，可生成待审核草案。','大家都已确认，任务到这里告一段落。接下来可以申请结算，奖励仍需本人确认和管理员审核。')
 text=text.replace('记录已取回，返回维修站交付。每位玩家确认结束后生成结算草案。','记录已经取回，可以返回维修站交付了。你们各自还想做些什么？准备离开的玩家可以直接告诉我。')
 text=text.replace('维修站请求找回误锁的公开设备记录。可以交涉、调查或确认结束。','维修站想请你们找回一份误锁的公开设备记录。你们打算先从哪里入手？')
 text=text.replace('抵达无人值守的旧维修终端。可以工程、调查、潜行，或撤离。','你们来到旧维修终端前，这里无人值守。接下来打算怎么做？')
 text=text.replace('\n镜头：','\n')
 return text
