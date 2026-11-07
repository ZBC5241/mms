#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会员运营工作台 · 统一构建器（一个文件，手机/PC 自适应）

数据（每天 08:00 定时全量刷新）：
  ① 钢化膜核销：优先复用 shopbag 22:51 落盘 raw，否则现拉 rm_retailvouchlist（店长号）
  ② 会员持券：经理号拉 mm_membercouponlist（目标券实例）+ pt_couponreceive（剩余次数）+ mm_memberslist（员工归属）

用法：
  python3 build_unified.py --auto      # 定时任务：全量刷新 + 渲染
  python3 build_unified.py --render-only  # 用已落盘缓存离线重渲染
"""
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

def clean_emp(name):
    """用友对重名员工自动加尾随单大写字母后缀（A/B/C…），剥离还原真实姓名。"""
    if not name or name == '未分配':
        return name
    return re.sub(r'(?<=[\u4e00-\u9fff])[A-Z]$', '', name)

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)


def atomic_json(path, obj, **kw):
    """原子写 JSON：先写 .tmp 再 replace，避免中断留下半截文件被下一轮当真实数据读。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, **kw)
    os.replace(tmp, path)


def atomic_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def jsdata(o):
    """注入 <script> 的 JSON：< 转义，防真实数据里的 </script> 提前闭合脚本。"""
    return json.dumps(o, ensure_ascii=False).replace('</', '<\\/')


import data_asof  # 分块真实数据时点（券/消费/画像/核销）
SHOPBAG_DATA = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/shopbag/data"
CACHE = os.path.join(BASE, "data", "unified_cache.json")
OUT_HTML = "/Users/mac/WorkBuddy/2026-09-11-21-21-56/会员管理工作台.html"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# ---------- 加载 film_sprint 模块（复用其口径与函数） ----------
_fs_path = os.path.join(BASE, "film_sprint.py")
_spec = importlib.util.spec_from_file_location("film_sprint", _fs_path)
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

STORE = "2387138245738627078"  # 李家村
# 券分组（与 aggregate_workbench.py 一致）
Q1 = {'2552005719361060873', '2552009000726560773', '2656069280839761924',
      '2656070758311133186', '2552845007520792583', '2583272016121954309', '2583272600237506566'}
Q2 = {'2506885404289925125', '2443007025695686661', '2443022616426971140',
      '2443008185336856583', '2443022487573233670'}
# 现金券（2026-11-03 晨哥补充：50元通用/定向券，券模板名 星联会员-通用/定向现金券）
CASH_IDS = {'ty': '2551884343585275909',   # 星联会员-通用现金券
            'dx': '2551888741631787015'}   # 星联会员-定向现金券
# 现金券规则短句（源：券模板 cRemind 字段；面额动态取 fReduceAmount）
CASH_RULE = {'ty': '二次消费抵5%·单笔封顶300·不足50按50抵',
             'dx': '贴膜/care/配件/家居立减·≤399减50·＞399减100'}


# ---------- 券到期日推算 + 会员注册日注入（2026-11-06 数据治理点亮） ----------
# 依据（均为真实数据）：券模板 iExpMode=2（间隔过期）+ iintervalDaysExp（有效期数）+ iintervalExpUnit（1=天 2=月）
# 推算公式：到期日 end = 领取日(pubts) + 有效期。页面展示必须标注「按模板有效期推算」。
# iExpMode≠2 或无 iintervalDaysExp（如 2 个现金券模板）= 无固定有效期，end 留空（如实排除，不编造）。

def calc_end(tpl, pubts):
    """按模板有效期推算券到期日（YYYY-MM-DD）；无法推算返回空串。"""
    if not tpl or not pubts:
        return ''
    days = tpl.get('iintervalDaysExp')
    if not days or tpl.get('iExpMode') != 2:
        return ''
    try:
        import calendar
        base = datetime.strptime(str(pubts)[:10], '%Y-%m-%d')
        if tpl.get('iintervalExpUnit') == 2:  # 月
            months = int(days)
            total = base.year * 12 + (base.month - 1) + months
            y, m0 = total // 12, total % 12
            d = min(base.day, calendar.monthrange(y, m0 + 1)[1])
            return datetime(y, m0 + 1, d).strftime('%Y-%m-%d')
        return (base + timedelta(days=int(days))).strftime('%Y-%m-%d')
    except Exception:
        return ''


def patch_end_reg(payload):
    """对聚合产物补算 券推算到期日(end)、会员注册日(reg) 与销售画像扩展字段，幂等全量重算覆盖。
    数据源均为本地真实文件：tpl_full.json（模板有效期）+ member_profiles.json（画像）+ member_consumption.json（换机窗口）。
    真跑聚合与 --render-only 离线渲染统一走本函数，双路径行为一致。"""
    try:
        tpl = json.load(open(os.path.join(BASE, 'data', 'tpl_full.json'), encoding='utf-8'))
        tmap = {}
        for t in tpl:
            tmap.setdefault(t.get('cTitle') or '', t)  # 重名模板实测有效期一致，取首个
        prof = json.load(open(os.path.join(BASE, 'data', 'member_profiles.json'), encoding='utf-8'))
    except Exception as e:
        # 红线：源文件不可用时必须清空派生字段并标红，不能留着上一轮缓存里的 end/reg 冒充本轮推算
        log('patch_end_reg 数据源读取失败，本轮清空 end/reg/lastPhone 并标黄: {}'.format(e))
        for m in payload.get('members', []):
            m.pop('reg', None)
            m.pop('lastPhone', None)
            for c in m.get('coupons', []):
                c.pop('end', None)
        try:
            import data_health
            data_health.mark('derived', 'partial', '到期日/注册日推算源文件不可用，本轮这些字段为空')
        except Exception:
            pass
        return payload
    # 换机窗口：按手机号取消费明细里最近一次「手机」品类购买日期。
    # ⚠️ purchases[].class 是商品系列原始值（Pura80/Mate90/nova/贴膜/Watch…），没有"手机"二字；
    #    手机判定用系列白名单正则——MatePad/MateBook 含 Mate、Watch GT6 Pro 含 Pro，必须靠锚定开头排除。
    import re as _re
    _PHONE_SERIES = _re.compile(r'^(pura|mate\s?\d|mate\s?x\s?\d*|nova|畅享|pocket)', _re.I)
    def _is_phone_class(c):
        c = (c or '').strip()
        if not c or 'pad' in c.lower() or 'book' in c.lower():
            return False
        return bool(_PHONE_SERIES.match(c))
    last_phone = {}
    try:
        cons = json.load(open(os.path.join(BASE, 'data', 'member_consumption.json'), encoding='utf-8'))
        for ph, v in cons.items():
            dates = [p.get('date') for p in (v.get('purchases') or [])
                     if _is_phone_class(p.get('class')) and p.get('date')]
            if dates:
                last_phone[ph] = max(dates)
    except Exception as e:
        log('消费明细读取失败，换机窗口本轮不注入: {}'.format(e))
    n_end = n_reg = n_phone = 0
    for m in payload.get('members', []):
        for c in m.get('coupons', []):
            c['end'] = calc_end(tmap.get(c.get('coupon') or ''), c.get('time'))
            if c['end']:
                n_end += 1
        p = prof.get(str(m.get('mid'))) or {}
        m['reg'] = p.get('reg') or ''
        m['lastTrade'] = p.get('lastTrade') or ''          # 官方末次消费（比券领取时间更准）
        m['firstAmt'] = p.get('firstAmt') or 0             # 首单金额 → 消费升降级信号
        m['lastAmt'] = p.get('lastAmt') or 0               # 末单金额
        m['levelValid'] = p.get('levelValid') or ''        # 等级有效期 → 保级话术
        m['channel'] = p.get('channel') or ''              # 来源渠道
        m['wxOk'] = p.get('wxOk') or 0                     # 微信可触达
        m['refundCount'] = p.get('refundCount') or 0       # 退款次数 → 服务补救
        lp = last_phone.get(m.get('phone') or '') or ''
        m['lastPhone'] = lp                                # 最近购机日 → 换机窗口
        if m['reg']:
            n_reg += 1
        if lp:
            n_phone += 1
    log('到期日推算: {} 张券有 end · 注册日 {}/{} 人 · 购机记录 {}/{} 人'.format(
        n_end, n_reg, len(payload.get('members', [])), n_phone, len(payload.get('members', []))))
    return payload


def log(msg):
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------- 用友拉取（经理号） ----------
def yonyou_login(pg, account):
    pwd = fs.get_yonyou_pwd() if account == fs.ACCT else None
    if pwd is None:
        import subprocess
        out = subprocess.run(["security", "find-generic-password", "-s", "yonyou", "-a", account, "-w"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError("钥匙串无 {} 密码".format(account))
        pwd = out.stdout.strip()
    pg.goto("https://c3.yonyoucloud.com", wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    try:
        acc = pg.query_selector(".button_accept")
        if acc:
            acc.click()
            time.sleep(1.5)
    except Exception:
        pass
    lf = None
    for _ in range(15):
        for f in pg.frames:
            try:
                if "euc.yonyoucloud.com" in (f.url or "") and f.query_selector("#username"):
                    lf = f
                    break
            except Exception:
                pass
        if lf:
            break
        time.sleep(2)
    if not lf:
        raise RuntimeError("登录框未出现")
    lf.fill("#username", account)
    time.sleep(0.3)
    lf.fill("#password", pwd)
    time.sleep(0.3)
    lf.click("#submit_btn_login")
    for _ in range(20):
        time.sleep(2)
        if "login" not in pg.url.lower() and "cas" not in pg.url.lower():
            break
    time.sleep(4)
    if "login" in pg.url.lower() or "cas" in pg.url.lower():
        raise RuntimeError("登录未完成")


def pull_coupons():
    """经理号拉：会员列表 + 券模板 + 目标券实例 + 剩余次数"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=CHROME,
                              args=["--no-sandbox", "--disable-dev-shm-usage",
                                    "--disable-blink-features=AutomationControlled"])
        try:
            pg = b.new_page()
            # 经理会话复用：直接探测接口，失败才重登
            pg.goto("https://c3.yonyoucloud.com", wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)
            session_ok = False
            try:
                session_ok = pg.evaluate("""async()=>{
                  try{
                    const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                      method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                      body:JSON.stringify({"page":{"pageSize":1,"pageIndex":1,"totalCount":1},"billnum":"mm_memberslist",
                        "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true},
                        "bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})
                    });
                    const j=await r.json();
                    return j.code===200;
                  }catch(e){ return false; }
                }""")
            except Exception:
                session_ok = False
            if session_ok:
                log("经理号会话有效")
            else:
                log("经理号会话失效，重新登录…")
                yonyou_login(pg, "18591910491")

            # 1) 会员列表（李家村）
            log("拉会员列表…")
            members = pg.evaluate("""async(store)=>{
              const out=[];
              for(let pi=1;pi<=40;pi++){
                const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                  method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({"page":{"pageSize":500,"pageIndex":pi,"totalCount":1},"billnum":"mm_memberslist",
                    "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],
                    "filtersId":"87589122","solutionId":"2458405643108745224","bInit":true,
                    "simpleVOs":[{"field":"iStop","op":"in","value1":[0,1]},{"field":"bVisitor","value1":0,"op":"eq"},{"field":"iStoreID","op":"eq","value1":store}]},
                    "bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})
                });
                const j=await r.json();
                if(!j||!j.data||!Array.isArray(j.data.recordList))throw new Error('用友接口返回异常（可能登录态失效）: '+JSON.stringify(j).slice(0,180));
                const l=j.data.recordList;
                out.push(...l);
                if(l.length<500) break;
              }
              return out;
            }""", STORE)
            log("会员 {} 人".format(len(members)))

            # 2) 券模板（143个，识别新自由系列）
            log("拉券模板…")
            tpl = pg.evaluate("""async()=>{
              const out=[];
              for(let pi=1;pi<=3;pi++){
                const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020202&locale=zh_CN&sbillno=pt_couponmanagelist&terminalType=1',{
                  method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({"page":{"pageSize":100,"pageIndex":pi,"totalCount":1},"billnum":"pt_couponlist",
                    "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87572906","solutionId":"87582779","bInit":true},
                    "bEmptyWithoutFilterTree":true,"serviceCode":"SDMB020202","locale":"zh_CN","sbillno":"pt_couponmanagelist","ownDomain":"uhy","tplid":1106306})
                });
                const j=await r.json();
                if(!j||!j.data||!Array.isArray(j.data.recordList))throw new Error('用友接口返回异常（可能登录态失效）: '+JSON.stringify(j).slice(0,180));
                const l=j.data.recordList;
                out.push(...l);
                if(l.length<100) break;
              }
              return out;
            }""")
            log("券模板 {} 个".format(len(tpl)))
            xz_ids = {t['id'] for t in tpl if '新自由' in (t.get('cTitle') or '') and any(k in (t.get('cTitle') or '') for k in ['电源', '耳机'])}
            tpl_map = {t['id']: t for t in tpl}

            # 3) 目标券实例
            target_ids = list(Q1 | xz_ids | Q2 | set(CASH_IDS.values()))
            log("拉 {} 张目标券实例…".format(len(target_ids)))
            inst = pg.evaluate("""async(ids)=>{
              const out=[];
              for(const cid of ids){
                for(let pi=1;pi<=40;pi++){
                  const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                    method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({"page":{"pageSize":500,"pageIndex":pi,"totalCount":1},"billnum":"mm_membercouponlist",
                      "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true,
                        "simpleVOs":[{"field":"iCouponID","op":"eq","value1":cid}]},
                      "bEmptyWithoutFilterTree":true,"serviceCode":"SDMB020101","locale":"zh_CN","ownDomain":"uhy"})
                  });
                  const j=await r.json();
                  const l=(j.data&&j.data.recordList)||[];
                  out.push(...l);
                  if(l.length<500) break;
                }
              }
              return out;
            }""", target_ids)
            log("券实例 {} 条".format(len(inst)))

            # 4) 剩余次数（按 cSN in 批量）
            sns = [r['cSN'] for r in inst if r.get('cSN')]
            log("拉 {} 个券编码的剩余次数…".format(len(sns)))
            recv = pg.evaluate("""async(sns)=>{
              const out=[];
              for(let i=0;i<sns.length;i+=100){
                const chunk=sns.slice(i,i+100);
                const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                  method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({"page":{"pageSize":500,"pageIndex":1,"totalCount":1},"billnum":"pt_couponreceive",
                    "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true,
                      "simpleVOs":[{"field":"cSN","op":"in","value1":chunk}]},
                    "bEmptyWithoutFilterTree":true,"serviceCode":"SDMB020101","locale":"zh_CN","ownDomain":"uhy"})
                });
                const j=await r.json();
                if(!j||!j.data||!Array.isArray(j.data.recordList))throw new Error('用友领用记录接口返回异常: '+JSON.stringify(j).slice(0,180));
                out.push(...j.data.recordList);
              }
              return out;
            }""", sns)
            log("领用记录命中 {} 条".format(len(recv)))
            return {"members": members, "tpl": tpl, "inst": inst, "recv": recv}
        finally:
            b.close()


def aggregate_coupons(data):
    members = data["members"]
    tpl = data["tpl"]
    inst = data["inst"]
    recv = data["recv"]
    tpl_map = {t['id']: t for t in tpl}
    avl = {r['cSN']: r.get('iAvlTimes') for r in recv}
    mmap = {m['id']: m for m in members}
    ljc_ids = set(mmap.keys())
    xz_ids = {t['id'] for t in tpl if '新自由' in (t.get('cTitle') or '') and any(k in (t.get('cTitle') or '') for k in ['电源', '耳机'])}

    def group_of(cid):
        if cid in Q1: return 'xl'
        if cid in xz_ids: return 'xz'
        if cid in Q2: return 'mb'
        if cid == CASH_IDS.get('ty'): return 'ty'
        if cid == CASH_IDS.get('dx'): return 'dx'
        return 'other'

    rows = []
    unknown_left = 0   # 剩余次数接口未命中：不静默当满次数，需如实标注
    for r in inst:
        if r.get('iStatus') != 0: continue
        if (r.get('pubts') or '') < '2026-01-01': continue
        mid = r.get('iMemberID')
        if mid not in ljc_ids: continue
        m = mmap[mid]
        cid = r.get('iCouponID')
        g = group_of(cid)
        if g == 'other': continue
        t = tpl_map.get(cid, {})
        total = t.get('iAmount') or 1
        left = avl.get(r.get('cSN'))
        if left is None:
            left = total
            unknown_left += 1
        sub = r.get('cSubTitle') or ''
        if g in CASH_RULE and not sub:
            face = t.get('fReduceAmount')
            face_s = "面额{}元".format(int(face)) if face else ''
            sub = " · ".join(x for x in (face_s, CASH_RULE[g]) if x)
        rows.append({
            'mid': mid,
            'name': (m.get('cRealName') or '').strip() or (m.get('cPhone') or ''),
            'phone': m.get('cPhone') or '',
            'emp': clean_emp(m.get('employeeName') or m.get('csEmployeeName') or '未分配'),
            'level': m.get('cMemberLevelName') or '',
            'g': g,
            'coupon': r.get('cTitle') or '',
            'sub': sub,
            'time': (r.get('pubts') or '')[:16],
            'sn': r.get('cSN') or '',
            'left': left,
            'total': total,
        })

    by_member = defaultdict(list)
    for r in rows:
        by_member[r['mid']].append(r)
    member_rows = []
    for mid, cs in by_member.items():
        cs.sort(key=lambda x: x['time'], reverse=True)
        first = cs[0]
        member_rows.append({
            'mid': mid, 'name': first['name'], 'phone': first['phone'],
            'emp': first['emp'], 'level': first['level'],
            'n': len(cs),
            'leftSum': sum(c['left'] for c in cs),
            'groups': sorted({c['g'] for c in cs}),
            'coupons': [{'g': c['g'], 'coupon': c['coupon'], 'sub': c['sub'],
                         'time': c['time'], 'sn': c['sn'], 'left': c['left'], 'total': c['total']} for c in cs],
        })
    member_rows.sort(key=lambda x: (-x['leftSum'], -x['n']))

    by_emp = defaultdict(lambda: {'members': set(), 'unpaid': 0, 'leftSum': 0, 'byGroup': Counter(), 'membersWithUnpaid': set()})
    for m in members:
        by_emp[clean_emp(m.get('employeeName') or '未分配')]['members'].add(m['id'])
    for r in rows:
        e = by_emp[r['emp']]
        e['unpaid'] += 1
        e['leftSum'] += r['left']
        e['byGroup'][r['g']] += 1
        e['membersWithUnpaid'].add(r['mid'])
    emp_rows = []
    for name, d in by_emp.items():
        emp_rows.append({'emp': name, 'members': len(d['members']),
                         'membersWithUnpaid': len(d['membersWithUnpaid']),
                         'unpaid': d['unpaid'], 'leftSum': d['leftSum'],
                         'byGroup': {k: v for k, v in d['byGroup'].items()}})
    emp_rows.sort(key=lambda x: -x['unpaid'])

    # ---------- 顺手同步「现金券全状态明细」（零额外网络请求）----------
    # inst 本身含 iStatus=0/1/-1 全状态，上面聚合只取 status==0（在账券）。
    # 这里把 ty/dx 现金券的已用/失效历史也落盘，会员查询页的券历史才不会停在上次手动补拉。
    # 空结果不覆盖，避免接口异常把历史明细清空。
    try:
        inv_g = {CASH_IDS.get('ty'): 'ty', CASH_IDS.get('dx'): 'dx'}
        cash_rows = []
        for r in inst:
            g = inv_g.get(r.get('iCouponID'))
            if not g:
                continue
            m = mmap.get(r.get('iMemberID'))
            if not m:
                continue
            sn = r.get('cSN') or ''
            cash_rows.append({
                'mid': r.get('iMemberID'),
                'name': (m.get('cRealName') or '').strip() or (m.get('cPhone') or ''),
                'phone': m.get('cPhone') or '',
                'emp': clean_emp(m.get('employeeName') or m.get('csEmployeeName') or '未分配'),
                'level': m.get('cMemberLevelName') or '',
                'g': g, 'coupon': r.get('cTitle') or '', 'sub': r.get('cSubTitle') or '',
                'time': (r.get('pubts') or '')[:16], 'sn': sn,
                'left': avl.get(sn), 'status': r.get('iStatus'),
                'raw': {k: r.get(k) for k in ('iUsedTimes', 'iTotalTimes', 'dEndDate') if r.get(k) is not None},
            })
        if cash_rows:
            cash_out = {'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
                        'gname': {'ty': '通用现金券', 'dx': '定向现金券'},
                        'rows': cash_rows}
            cp = os.path.join(BASE, 'data', 'cash_coupons.json')
            tmp = cp + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cash_out, f, ensure_ascii=False, indent=1)
            os.replace(tmp, cp)
            log('现金券全状态明细已同步 {} 条（含已用/失效），零额外请求'.format(len(cash_rows)))
            import data_health
            note = '本轮券实例同步 {} 条，零额外请求'.format(len(cash_rows))
            if unknown_left:
                note += '；{} 张券剩余次数接口未命中，按满次数兜底（非实时，已标黄）'.format(unknown_left)
            data_health.mark('cash', 'partial' if unknown_left else 'ok', note)
        else:
            log('现金券明细本次为空，保留上一版不动')
            import data_health
            data_health.mark('cash', 'partial', '本轮券实例为空，现金券明细沿用上一版')
    except Exception as e:
        log('现金券明细同步失败，保留上一版: {}'.format(e))
        import data_health
        data_health.mark('cash', 'fail', '现金券明细同步失败，沿用上一版：{}'.format(e))

    return {
        'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'gname': {'xl': '星联会员', 'xz': '新自由', 'mb': '膜套包',
                  'ty': '通用现金券', 'dx': '定向现金券'},
        'members': member_rows,
        'emps': emp_rows,
        'total': {
            'unpaid': len(rows),
            'members': len(member_rows),
            'leftSum': sum(r['leftSum'] for r in member_rows),
            'byGroup': dict(Counter(r['g'] for r in rows)),
        },
    }


def load_film_hist(today):
    """film 历史：sprint_history.json + shopbag raws；当日优先复用 shopbag raw"""
    hist = {}
    sp = os.path.join(BASE, "data", "sprint_history.json")
    if os.path.exists(sp):
        hist = json.load(open(sp, encoding="utf-8")).get("daily", {})
    import re
    for f in sorted(os.listdir(SHOPBAG_DATA)):
        m = re.match(r"raw_(\d{4}-\d{2}-\d{2})\.json$", f)
        if not m:
            continue
        d = m.group(1)
        if d in hist:
            continue
        try:
            rows = json.load(open(os.path.join(SHOPBAG_DATA, f), encoding="utf-8"))
            if isinstance(rows, dict):
                rows = rows.get("rows") or rows.get("recordList") or []
            hist[d] = fs.aggregate_day(rows, d)
        except Exception as e:
            log("skip {}: {}".format(f, e))
    # 当日：优先 shopbag raw
    raw_today = os.path.join(SHOPBAG_DATA, "raw_{}.json".format(today))
    if os.path.exists(raw_today):
        try:
            rows = json.load(open(raw_today, encoding="utf-8"))
            if isinstance(rows, dict):
                rows = rows.get("rows") or rows.get("recordList") or []
            hist[today] = fs.aggregate_day(rows, today)
            log("当日核销复用 shopbag raw")
        except Exception as e:
            log("当日 raw 解析失败: {}".format(e))
    elif today not in hist:
        log("当日 raw 不存在且历史无记录，冲刺数据可能缺今天")
    return hist


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    if "--date" in sys.argv:
        today = sys.argv[sys.argv.index("--date") + 1]
    render_only = "--render-only" in sys.argv

    # ① film 数据
    film_hist = load_film_hist(today)
    film_payload = fs.compute_payload(film_hist, today)
    log("冲刺数据: {}/{} 张, 剩 {} 张".format(film_payload['doneTotal'], film_payload['cfg']['store_target'], film_payload['remain']))

    # ② 券数据
    if render_only and os.path.exists(CACHE):
        coupon_payload = json.load(open(CACHE, encoding="utf-8"))
        log("券数据用缓存渲染")
    else:
        data = pull_coupons()
        coupon_payload = aggregate_coupons(data)
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        atomic_json(CACHE, coupon_payload)
        log("券数据已缓存 {}".format(CACHE))

    # ②.5 到期日推算 + 注册日注入（真跑/离线双路径统一，幂等）
    # 🔴 必须写回缓存：内层会员查询页（enrich_profiles --merge-v1）随后读的是 CACHE，
    #    以前只在 render_only 写回，导致每天真跑后内层券 end 全空、临期券恒为 0（审计 11-06 修复）
    patch_end_reg(coupon_payload)
    # 促销行情属于「渲染期注入」，不落缓存：否则快照失败时旧行情会被下一轮当成新数据
    coupon_payload.pop('promo', None)
    for _m in coupon_payload.get('members', []):
        _m.pop('deals', None)
    atomic_json(CACHE, coupon_payload)
    log('到期日/注册日已写回缓存，供内层查询页复用；促销行情不落缓存')

    # ②.6 促销行情快照（产品降价/活动 → 话术与焦点卡；数据为管线快照，页面标真实时点）
    try:
        snap = json.load(open(os.path.join(BASE, 'data', 'promo_snapshot.json'), encoding='utf-8'))
        coupon_payload['promo'] = {
            'asOf': snap.get('asOf') or '',
            'count': len(snap.get('promos') or []),
            'list': [{'name': p.get('name'), 'start': p.get('start'), 'end': p.get('end'),
                      'n': len(p.get('items') or [])} for p in (snap.get('promos') or [])[:12]],
        }
        byphone = snap.get('dealsByPhone') or {}
        n_deal = 0
        for m in coupon_payload.get('members', []):
            ds = byphone.get(m.get('phone') or '') or []
            m['deals'] = ds[:2]
            if ds:
                n_deal += 1
        log('促销行情: 进行中 {} 个活动 · {} 位客户有买过机型的活动行情（快照 {}）'.format(
            coupon_payload['promo']['count'], n_deal, coupon_payload['promo']['asOf']))
    except Exception as e:
        # 红线：失败时不能留着缓存里上一轮的 promo/deals 冒充本轮行情
        coupon_payload.pop('promo', None)
        for m in coupon_payload.get('members', []):
            m.pop('deals', None)
        log('促销快照读取失败，本轮已清除 promo/deals（不沿用旧快照）: {}'.format(e))
        try:
            import data_health
            data_health.mark('promo', 'partial', '促销行情快照本轮不可用，页面不显示活动价')
        except Exception:
            pass

    # ③ 渲染统一 HTML（注入各块真实数据时点，不用构建时间冒充）
    coupon_payload["asOf"] = data_asof.collect(BASE)
    html = render_unified(coupon_payload, film_payload)
    atomic_text(OUT_HTML, html)
    log("统一工作台已生成: {} ({}KB)".format(OUT_HTML, len(html) // 1024))


def render_unified(W, F):
    return TEMPLATE.replace('__DATA_W__', jsdata(W)) \
                   .replace('__DATA_F__', jsdata(F))


# 模板独立文件（视觉系统与代码分离）
TEMPLATE_FILE = os.path.join(BASE, "template_unified.html")
with open(TEMPLATE_FILE, encoding="utf-8") as _f:
    TEMPLATE = _f.read()


if __name__ == "__main__":
    main()
