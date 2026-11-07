#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会员画像补全：从 mm_memberslist 拉全量会员，按本店 366 人 mid 过滤，
抽取画像字段（等级/注册时间/累计消费/交易次数/最近交易/积分/卡号/可用券数），
合并进 member_query_cache.json 并重渲染内部中间产物 .build/会员查询_内联源.html（不落到工作区顶层）。

iStoreID 过滤器已失效（返全公司），故全量拉 + 本地按 mid 过滤。
"""
import importlib.util, json, os, re, time, datetime, sys
BASE = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint"


def _ensure_profiles_meta():
    """画像时点只允许来自真实扫描完成时刻；历史文件缺失 meta 时按真实文件时间回填，不伪造。"""
    p = os.path.join(BASE, "data", "profiles_meta.json")
    if os.path.exists(p):
        return
    fp = os.path.join(BASE, "data", "member_profiles.json")
    if not os.path.exists(fp):
        return
    try:
        prof = json.load(open(fp, encoding="utf-8"))
        mt = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
        bu.atomic_json(p, {"builtAt": mt, "hits": len(prof),
                           "note": "初始回填：完成时刻取自数据文件真实修改时间，非页面构建时间"}, indent=1)
    except Exception as e:
        log("画像时点回填失败（页面将如实显示未知）: {}".format(e))
sys.path.insert(0, BASE)
import data_asof  # 数据时点单一真源（券/消费/画像/核销各记各的时间）
_spec = importlib.util.spec_from_file_location("bu", os.path.join(BASE, "build_unified.py"))
bu = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(bu)

CACHE = os.path.join(BASE, "data", "member_query_cache.json")
PROF = os.path.join(BASE, "data", "member_profiles.json")
TPL = os.path.join(BASE, "template_query.html")
TPFL = os.path.join(BASE, "data", "tpl_full.json")
UJSON = os.path.join(BASE, "data", "unified_cache.json")
CJSON = os.path.join(BASE, "data", "cash_coupons.json")
CONS = os.path.join(BASE, "data", "member_consumption.json")
# 2026-11-05 单文件交付：内部中间产物，不落工作区顶层
OUT = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint/.build/会员查询_内联源.html"
GNAME = {'xl': '星联会员', 'xz': '新自由', 'mb': '膜套包',
         'ty': '通用现金券', 'dx': '定向现金券', 'other': '其他'}


def log(m):
    print("[{}] {}".format(time.strftime("%H:%M:%S"), m), flush=True)


def authoritative_mids():
    U = json.load(open(os.path.join(BASE, "data", "unified_cache.json"), encoding="utf-8"))
    C = json.load(open(os.path.join(BASE, "data", "cash_coupons.json"), encoding="utf-8"))
    s = set()
    for m in U.get("members", []):
        if m.get("mid"):
            s.add(m["mid"])
    for r in C.get("rows", []):
        mid = r.get("mid")
        if mid:
            s.add(mid)
    return s


def pick(m, *keys):
    for k in keys:
        v = m.get(k)
        if v not in (None, "", 0):
            return v
    return ""


def _extract_profile(m):
    return {
        "reg": (m.get("dRegisterDate") or m.get("createTime") or "")[:10],
        "level": m.get("cMemberLevelName") or "",
        "consume": pick(m, "fConsumeAmount", "fRealConsumeAmount"),
        "tradeCount": pick(m, "memberattach!iTradeCount", "iConsumeCount"),
        "lastTrade": (m.get("dLastTradeTime") or "")[:10],
        "firstTrade": (m.get("dFirstTradeTime") or "")[:10],
        "points": pick(m, "iPoints", "iTotalPoints"),
        "cardNo": m.get("cCardNo") or "",
        "sex": m.get("iSex"),
        "couponAvail": pick(m, "iAvailableCount"),
        "couponExpired": pick(m, "iExpiredCount"),
        "store": m.get("storeName") or "",
        # 2026-11-06 扩展：销售画像新维度（实测填充率见 .build/probe_members_raw.json 样本）
        "firstAmt": m.get("memberattach!fFirstTradeAmount") or 0,      # 首单金额→消费升降级信号
        "lastAmt": m.get("memberattach!fLastTradeAmount") or 0,        # 末单金额
        "levelValid": (m.get("dMemLevelValidDate") or "")[:10],        # 等级有效期→保级话术
        "channel": m.get("cChannelName") or "",                        # 来源渠道
        "wxOk": 1 if (m.get("WXStatus") or (m.get("cOpenID") or "")) else 0,  # 微信可触达
        "refundCount": m.get("memberattach!iRefundCount") or 0,        # 退款次数→服务补救
    }


def _one_member_page(pg, pi):
    return pg.evaluate("""async(pi)=>{const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({"page":{"pageSize":500,"pageIndex":pi,"totalCount":1},"billnum":"mm_memberslist","condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true},"bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})});const j=await r.json();if(!j||!j.data||!Array.isArray(j.data.recordList))throw new Error('用友会员接口返回异常（可能登录态失效）: '+JSON.stringify(j).slice(0,180));return j.data.recordList;}""", pi)


def _targeted_member_chunk(pg, chunk):
    return pg.evaluate("""async(chunk)=>{const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({"page":{"pageSize":500,"pageIndex":1,"totalCount":1},"billnum":"mm_memberslist","condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true},"simpleVOs":[{"field":"id","op":"in","value1":chunk}],"bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})});const j=await r.json();if(!j||!j.data||!Array.isArray(j.data.recordList))throw new Error('用友会员精准查询返回异常: '+JSON.stringify(j).slice(0,180));return j.data.recordList;}""", chunk)


def fetch_profiles(ljc):
    from playwright.sync_api import sync_playwright
    prof = {}
    ljc = set(ljc)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=bu.CHROME,
                              args=["--no-sandbox", "--disable-dev-shm-usage",
                                    "--disable-blink-features=AutomationControlled"])
        try:
            pg = b.new_page()
            pg.goto("https://c3.yonyoucloud.com", wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)
            ok = pg.evaluate("""async()=>{try{const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({"page":{"pageSize":1,"pageIndex":1,"totalCount":1},"billnum":"mm_memberslist","condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true},"bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})});const j=await r.json();return j.code===200;}catch(e){return false;}}""")
            if not ok:
                log("登录经理号…")
                bu.yonyou_login(pg, "18591910491")

            # 1) 精准反查：按 id in 分批（每批 50），一次拿全本店会员完整档案
            log("精准反查本店 {} 人档案（按 id in 分批）…".format(len(ljc)))
            mids = list(ljc)
            for i in range(0, len(mids), 50):
                chunk = mids[i:i + 50]
                rec = _targeted_member_chunk(pg, chunk)
                for m in rec:
                    mid = m.get("id")
                    if mid in ljc:
                        prof[mid] = _extract_profile(m)
                log("  批次 {}-{}：返回 {} 条，命中本店 {} 人".format(i + 1, i + len(chunk), len(rec), len(prof)))
            log("精准反查命中 {} / {} 人".format(len(prof), len(ljc)))

            # 2) 命中不足（id-in 过滤器可能失效）→ 退回全量翻页兜底
            if len(prof) < len(ljc) * 0.8:
                log("精准命中不足，退回全量翻页兜底…")
                for pi in range(1, 400):
                    rec = _one_member_page(pg, pi)
                    if not rec:
                        break
                    for m in rec:
                        mid = m.get("id")
                        if mid in ljc and mid not in prof:
                            prof[mid] = _extract_profile(m)
                    if len(rec) < 500:
                        break
                    if len(prof) >= len(ljc):
                        break
                log("全量兜底后命中 {} / {} 人".format(len(prof), len(ljc)))
        finally:
            b.close()
    return prof


def merge_and_render(prof, base=None):
    if base == "__v1__":
        cache = build_base()
        log("合并基础数据来源: v1 底座（unified_cache + cash_coupons）")
    elif base and os.path.exists(base):
        cache = json.load(open(base, encoding="utf-8"))
        log("合并基础数据来源: {}".format(os.path.basename(base)))
    elif os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
        log("合并基础数据来源: {}".format(os.path.basename(CACHE)))
    else:
        cache = build_base()
        log("合并基础数据来源: v1 底座（unified_cache + cash_coupons）")
    today = datetime.date.today()
    cons = json.load(open(CONS, encoding="utf-8")) if os.path.exists(CONS) else {}
    for mem in cache.get("members", []):
        mem["consumption"] = cons.get(mem.get("phone")) or None
        p = prof.get(mem.get("mid"))
        if not p:
            mem["profile"] = None
            continue
        # 派生标签
        reg = p.get("reg", "")
        last = p.get("lastTrade", "")
        try:
            reg_d = datetime.date.fromisoformat(reg) if reg else None
        except Exception:
            reg_d = None
        try:
            last_d = datetime.date.fromisoformat(last) if last else None
        except Exception:
            last_d = None
        reg_days = (today - reg_d).days if reg_d else None
        silent_days = (today - last_d).days if last_d else None
        tags = []
        if reg_days is not None and reg_days <= 30:
            tags.append("新客")
        if silent_days is None:
            if reg_days is not None and reg_days > 180:
                tags.append("沉睡")
        elif silent_days >= 180:
            tags.append("沉睡")
        p2 = dict(p)
        p2["regDays"] = reg_days
        p2["silentDays"] = silent_days
        p2["tags"] = tags
        mem["profile"] = p2
    out = dict(cache)
    # 🔴 全局红线：merge-v1 只是复用画像缓存重渲染，没有重新扫描，
    #    绝不能加「含画像」这类暗示新鲜的后缀，也不能把构建时间写成数据时间。
    base_ga = re.sub(r"\s*·\s*含画像$|\s*（v1·现有数据）\s*$", "", cache.get("generatedAt") or "")
    out["generatedAt"] = base_ga
    # 历史客户：有真实消费记录、但券已用完/不在在册持券池的客户。
    # 全量注入、不截断任何一条消费记录（数据真实性红线）；仅供搜索与详情，
    # 不进 KPI / 预警 / 客户池 / 品类，避免冲淡在册持券会员的回访重点。
    try:
        cons_all = json.load(open(CONS, encoding="utf-8"))
        in_pool = {m.get("phone") for m in cache.get("members", []) if m.get("phone")}
        hist = {ph: v for ph, v in cons_all.items() if ph not in in_pool}
        out["history"] = hist
        out["history_count"] = len(hist)
        log("历史消费客户 {} 位已注入（全量消费记录，搜索可查）".format(len(hist)))
    except Exception as e:
        out["history"] = {}
        out["history_count"] = 0
        log("历史客户注入失败（宁可为空也不伪造）: {}".format(e))
    _ensure_profiles_meta()
    # 分块真实数据时点（不拿构建时间冒充）
    out["asOf"] = data_asof.collect(BASE)
    bu.atomic_json(CACHE, out, indent=1)
    tpl = open(TPL, encoding="utf-8").read()
    html = tpl.replace("__DATA_Q__", bu.jsdata(out))
    bu.atomic_text(OUT, html)
    log("画像已合并并重渲染(内部中间产物): {} ({}KB)".format(OUT, len(html) // 1024))
    n_prof = sum(1 for m in out["members"] if m.get("profile"))
    log("本店会员 {} 人，其中带画像 {} 人".format(out["total_members"], n_prof))


def derive_face(g, name, sub, tpl_map):
    if sub and "面额" in sub:
        m = re.search(r"面额\s*(\d+(?:\.\d+)?)\s*元", sub)
        if m:
            return "{}元".format(m.group(1))
    t = tpl_map.get(name)
    if t:
        fa = t.get("fReduceAmount")
        if fa not in (None, "", 0):
            return "{}元".format(fa)
        if t.get("fDiscount") not in (None, "", 0):
            return "{}折".format(t["fDiscount"])
        if t.get("iAmount") not in (None, "", 0):
            return "{}次".format(t["iAmount"])
    return {"mb": "套包", "xl": "星联券", "xz": "新自由", "ty": "现金", "dx": "现金", "other": "—"}.get(g, "—")


def build_base():
    """v1 底座：unified_cache(跟踪族未用券) + cash_coupons(现金券全状态)。"""
    tpl_map = {x.get("cTitle"): x for x in json.load(open(TPFL, encoding="utf-8"))}
    Ud = json.load(open(UJSON, encoding="utf-8"))
    Cd = json.load(open(CJSON, encoding="utf-8"))
    members = {}
    for m in Ud["members"]:
        members[m["mid"]] = {"mid": m["mid"], "name": m.get("name") or "", "phone": m.get("phone") or "",
                             "emp": m.get("emp") or "未分配", "level": m.get("level") or "", "coupons": []}
    for r in Cd["rows"]:
        mid = r.get("mid")
        if mid and mid not in members:
            members[mid] = {"mid": mid, "name": r.get("name") or "", "phone": r.get("phone") or "",
                            "emp": r.get("emp") or "未分配", "level": r.get("level") or "", "coupons": []}
    for m in Ud["members"]:
        mid = m["mid"]
        for c in m.get("coupons", []):
            members[mid]["coupons"].append({
                "g": c["g"], "name": c["coupon"], "sub": c.get("sub") or "",
                "face": derive_face(c["g"], c["coupon"], c.get("sub"), tpl_map),
                "status": 0, "pub": c.get("time") or "", "end": c.get("end") or "", "sn": c.get("sn") or "",
                "left": c.get("left"), "total": c.get("total"),
            })
    for r in Cd["rows"]:
        mid = r.get("mid")
        if mid not in members:
            continue
        members[mid]["coupons"].append({
            "g": r["g"], "name": r["coupon"], "sub": r.get("sub") or "",
            "face": derive_face(r["g"], r["coupon"], r.get("sub"), tpl_map),
            "status": r.get("status"), "pub": r.get("time") or "", "end": r.get("end") or "", "sn": r.get("sn") or "",
            "left": r.get("left"), "total": 1,
        })
    out_members = []
    for mid, m in members.items():
        seen = set()
        coupons = []
        for c in m["coupons"]:
            key = (c["g"], c["name"], c["sn"])
            if key in seen:
                continue
            seen.add(key)
            coupons.append(c)
        if not coupons:
            continue
        unused = [c for c in coupons if c["status"] == 0]
        used = [c for c in coupons if c["status"] == 1]
        expired = [c for c in coupons if c["status"] not in (0, 1)]
        _unk = sum(1 for c in coupons if c["status"] is None)
        if _unk:
            log("⚠️ {} 张券 status 缺失，暂归已失效（未编造状态）".format(_unk))
        unused.sort(key=lambda c: (c["end"] == "", c["end"]))
        out_members.append({
            "mid": mid, "name": m["name"], "phone": m["phone"], "emp": m["emp"],
            "level": m["level"], "reg": "",
            "unused": unused, "used": used, "expired": expired,
            "n_unused": len(unused), "n_used": len(used), "n_expired": len(expired),
        })
    out_members.sort(key=lambda x: (-x["n_unused"], -x["n_used"]))
    return {"generatedAt": time.strftime("%Y-%m-%d %H:%M"), "store": "李家村万达店",
            "gname": GNAME, "total_members": len(out_members), "members": out_members}


def main():
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    # 归一化：允许带前导 -- 或不带
    mode = mode.lstrip("-")
    if mode == "fetch-only":
        ljc = authoritative_mids()
        prof = fetch_profiles(ljc)
        bu.atomic_json(PROF, prof, indent=1)
        bu.atomic_json(os.path.join(BASE, "data", "profiles_meta.json"),
                       {"builtAt": time.strftime("%Y-%m-%d %H:%M"), "hits": len(prof)}, indent=1)
        log("画像原始数据已写 {}（{} 人），扫描完成时点已记录".format(PROF, len(prof)))
    elif mode == "merge-v1":
        prof = json.load(open(PROF, encoding="utf-8"))
        merge_and_render(prof, base="__v1__")
    elif mode == "merge-only":
        prof = json.load(open(PROF, encoding="utf-8"))
        merge_and_render(prof)
    else:
        ljc = authoritative_mids()
        prof = fetch_profiles(ljc)
        bu.atomic_json(PROF, prof, indent=1)
        log("画像原始数据已写 {}".format(PROF))
        merge_and_render(prof)


if __name__ == "__main__":
    main()
