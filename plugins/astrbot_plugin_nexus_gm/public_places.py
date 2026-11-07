"""Public geography from V3.2 chapter 01; never a current scene or secret."""
import re

PLACES={
 '沃森':'夜之城的城区之一，包括歌舞伎市场、北区和工业废墟。公开设定中的主要环境有街头市场、工业地带与地下诊所；漩涡帮、莫克斯都与这片区域有关。零号、白噪来自这里。NCPD是警察机构，不是地名。',
 '太平洲':'夜之城失落的度假区域。巫毒帮和野兽帮是公开设定中与这里有关的势力。虹膜出身于此，是地下数据掮客。',
 '圣多明戈':'以工厂、电站和住宅片区为主要环境的城区，公开势力包括六街帮。扳手的维修厂出身就位于这片工业与生活空间交织的区域。',
 '威斯特布鲁克':'包括日本街、北橡区和宪章山；公开背景涉及富豪、夜生活与虎爪帮。不同片区的生活环境有差异，不等于整区居民都有同一种身份。',
 '日本街':'位于威斯特布鲁克。理解夜莺的出身时，可以先了解该区的夜生活和虎爪帮背景；她曾是虎爪帮外围成员。',
 '海伍德':'包括谷地区、泉谷地区，公开设定强调居民社区以及瓦伦蒂诺帮等地方势力。债主和灰鸽都来自这里，但一个是消息贩子，一个是独立记者，职业与人生经历各不相同。',
 '市中心':'包括公司广场与市中心区域，公开背景中的主要力量是荒坂、军用科技等大型公司的安保势力。公司和街头的权力差距，是理解这里的一个切入点。',
 '公司广场':'属于市中心，是理解公司势力与公司安保环境的重要地点。琉璃曾是康陶情报员。',
 '恶土':'夜之城之外的活动舞台。公开设定包括流浪者、阿德卡多、乱刀会和走私路线；可从车队、移动生活和城外道路理解这类出身。回声有恶土流浪者血统。',
 '阿德卡多':'这是流浪者家族/组织，不是一个城区。V3.2把它列在恶土相关势力中。极速是“阿德卡多外围独立车手”。',
}
ORIGINS={'01':'沃森','02':'太平洲','03':'圣多明戈','04':'日本街','05':'海伍德','06':'沃森','07':'恶土','08':'阿德卡多','09':'公司广场','10':'海伍德'}

def places_query(text,templates):
 t=text.strip()
 if t.startswith(('/', '【暂停】')):return None
 if re.search(r'修改|改成|改为|更改|设定为|确认|批准|认领|入团|开场',t):return None
 related=any(w in t for w in ('出身','地址','地区','地名','地方','地域')) or any(p in t for p in PLACES)
 if not related or not re.search(r'介绍|讲讲|讲一下|讲解|背景|了解|是什么|什么地方|哪里|哪儿|看看|每个|全部|所有',t):return None
 requested=[p for p in PLACES if p in t]
 ids={f'{int(x):02d}' for x in re.findall(r'(?<!\d)(0?[1-9]|10)(?!\d)',t)}
 ids.update(cid for cid,card in templates.items() if card['name'] in t)
 if not requested and len(ids)==1:requested=[ORIGINS[next(iter(ids))]]
 if not requested:
  if '出身' not in t and not re.search(r'每个|全部|所有',t):return None
  requested=list(dict.fromkeys(ORIGINS.values()))
 lines=['这些是出身地的公开世界背景，不是本次剧情发生的事件：']
 for p in requested:lines.append(p+'：'+PLACES[p])
 return '\n\n'.join(lines)+'\n\n这些背景帮助你理解角色从怎样的环境走来，不改变角色卡；人物经历仍以各自公开设定为准。'
