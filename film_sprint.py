#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钢化膜核销冲刺看板 · 数据拉取 + 聚合 + HTML 生成

业务背景（晨哥 2026-10-25）：门店缺 200 张钢化膜，10 天内核销完毕。
  · 门店目标：200 张 / 10 天（日均 20 张）
  · 员工目标：每人每天 10 张
口径（与 shopbag_daily.py 完全一致，晨哥长期拍板）：
  · 普通零售单 + 门店商品分类含「贴膜」+ 行净额=0（rm_retailvouchlist）
数据源：
  · 历史：shopbag/data/raw_<date>.json（22:51 定时任务已落盘）
  · 当日：现拉 rm_retailvouchlist（店长号，Playwright）

用法：
  python3 film_sprint.py --auto          # 定时任务用：拉今天 + 刷新看板
  python3 film_sprint.py --date 2026-10-25 --pull   # 手动补拉某日
  python3 film_sprint.py --render-only   # 只用已落盘数据重渲染
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
SHOPBAG_DATA = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/shopbag/data"
OUT_DIR = os.path.join(BASE, "out")
HISTORY = os.path.join(BASE, "data", "sprint_history.json")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
YY = "https://c3.yonyoucloud.com"
ACCT = "18161914293"
REPORT_ID = "rm_retailvouchlist"

# ---------- 冲刺配置 ----------
CFG = {
    "start": "2026-10-25",          # 冲刺起始日（今天）
    "days": 10,                     # 10 天
    "store_target": 200,            # 门店总目标 200 张
    "emp_daily": 10,                # 每员工每日目标 10 张
    "emps": ["邵乐乐", "杨丽华", "李泽", "张博晨"],  # 实际开单员工（店长也参与核销）
}
CFG["end"] = (datetime.strptime(CFG["start"], "%Y-%m-%d") + timedelta(days=CFG["days"] - 1)).strftime("%Y-%m-%d")
CFG["daily_target"] = CFG["store_target"] / CFG["days"]  # 20 张/天


def log(msg):
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def get_yonyou_pwd():
    out = subprocess.run(["security", "find-generic-password", "-s", "yonyou", "-a", ACCT, "-w"],
                         capture_output=True, text=True, timeout=15)
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    raise RuntimeError("钥匙串无 yonyou 密码")


def pull_rows(target_date):
    """拉某日全量零售单行（复用 shopbag_daily.py 同款链路）"""
    from playwright.sync_api import sync_playwright
    all_rows = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=CHROME,
                              args=["--no-sandbox", "--disable-dev-shm-usage",
                                    "--disable-blink-features=AutomationControlled"])
        try:
            pg = b.new_page()
            pg.goto(YY, wait_until="domcontentloaded", timeout=60000)
            time.sleep(4)
            # 隐私协议/公告弹窗（shopbag_daily.py 同款处理）
            try:
                acc = pg.query_selector(".button_accept")
                if acc:
                    acc.click()
                    time.sleep(1.5)
            except Exception:
                pass
            lf = None
            for _ in range(12):
                for f in pg.frames:
                    if "euc.yonyoucloud.com" in (f.url or ""):
                        lf = f
                        break
                if lf and lf.query_selector("#username"):
                    break
                lf = None
                time.sleep(2)
            if not lf:
                raise RuntimeError("登录框未出现")
            lf.fill("#username", ACCT)
            time.sleep(0.3)
            lf.fill("#password", get_yonyou_pwd())
            time.sleep(0.3)
            lf.click("#submit_btn_login")
            for _ in range(20):
                time.sleep(2)
                if "login" not in pg.url.lower() and "cas" not in pg.url.lower():
                    break
            time.sleep(4)
            if "login" in pg.url.lower() or "cas" in pg.url.lower():
                raise RuntimeError("登录未完成")
            log("LOGGED_IN")
            start = "{} 00:00:00".format(target_date)
            end = "{} 23:59:59".format(target_date)
            page_idx = 1
            while True:
                body = {
                    "billnum": REPORT_ID,
                    "condition": {"commonVOs": [{"itemName": "vouchdate", "value1": start, "value2": end}]},
                    "page": {"pageSize": 1000, "pageIndex": page_idx},
                    "serviceCode": REPORT_ID,
                    "terminalType": "1",
                }
                resp_text = pg.evaluate("""(args) => {
                    const body = JSON.parse(args.body);
                    return fetch('/yonbip-mkt-retailweb/report/list?terminalType=1&serviceCode=rm_retailvouchlist&locale=zh_CN', {
                        method:'POST', credentials:'include',
                        headers:{'Content-Type':'application/json'},
                        body: JSON.stringify(body)
                    }).then(r=>r.text()).catch(e=>'FETCH_ERR:'+e.message);
                }""", {"body": json.dumps(body, ensure_ascii=False)})
                if resp_text.startswith("FETCH_ERR"):
                    raise RuntimeError("API fetch 失败: " + resp_text[:200])
                j = json.loads(resp_text)
                rows = (j.get("data") or {}).get("recordList", [])
                if not rows:
                    break
                all_rows.extend(rows)
                if len(rows) < 1000:
                    break
                page_idx += 1
                time.sleep(1)
        finally:
            b.close()
    return all_rows


def aggregate_day(rows, target_date):
    """按 shopbag_daily.py 同口径聚合当日每员工钢化膜核销张数"""
    day = [r for r in rows if (r.get("vouchdate") or "").startswith(target_date)]
    orders = {}
    for r in day:
        if r.get("iBusinesstypeid_name") != "普通":
            continue
        code = r.get("code")
        if not code:
            continue
        orders.setdefault(code, []).append(r)
    emp = defaultdict(lambda: {"film_orders": 0, "film_qty": 0.0})
    for code, lines in orders.items():
        first = lines[0]
        emp_name = (first.get("retailVouchDetails_iEmployeeid_name")
                    or first.get("retailVouchHeaderExtend_Employeename") or "未记录")
        film_qty = 0.0
        for x in lines:
            cls = x.get("retailVouchDetails_product_productClass_name") or ""
            qty = abs(float(x.get("retailVouchDetails_fQuantity") or 0))
            amt = float(x.get("retailVouchDetails_fMoney") or 0)
            if "贴膜" in cls and abs(amt) < 1e-6:
                film_qty += qty
        if film_qty > 0:
            emp[emp_name]["film_orders"] += 1
            emp[emp_name]["film_qty"] += film_qty
    return {k: {"film_orders": v["film_orders"],
                "film_qty": int(v["film_qty"]) if v["film_qty"] == int(v["film_qty"]) else v["film_qty"]}
            for k, v in emp.items()}


def load_history():
    """读冲刺历史（sprint_history.json）+ shopbag 落盘历史"""
    hist = {}
    # 1) 自己的冲刺历史（含现拉日期）
    if os.path.exists(HISTORY):
        hist = json.load(open(HISTORY, encoding="utf-8")).get("daily", {})
    # 2) shopbag 已落盘历史（补 10-25 之前的日子，用于趋势展示）
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
            hist[d] = aggregate_day(rows, d)
        except Exception as e:
            log("skip {}: {}".format(f, e))
    return hist


def main():
    mode_auto = "--auto" in sys.argv
    do_pull = mode_auto or "--pull" in sys.argv
    render_only = "--render-only" in sys.argv

    if "--date" in sys.argv:
        target = sys.argv[sys.argv.index("--date") + 1]
    else:
        target = datetime.now().strftime("%Y-%m-%d")

    hist = load_history()
    if do_pull and not render_only:
        # 优先复用 shopbag 22:51 已落盘的当日 raw（同源同口径，不重复登录拉数）
        shopbag_raw = os.path.join(SHOPBAG_DATA, "raw_{}.json".format(target))
        if os.path.exists(shopbag_raw):
            log("复用 shopbag 落盘: {}".format(shopbag_raw))
            try:
                rows = json.load(open(shopbag_raw, encoding="utf-8"))
                if isinstance(rows, dict):
                    rows = rows.get("rows") or rows.get("recordList") or []
                hist[target] = aggregate_day(rows, target)
                os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
                json.dump({"daily": hist, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")},
                          open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False)
                log("已落盘 {}".format(HISTORY))
            except Exception as e:
                log("复用失败：{}".format(e))
        else:
            log("shopbag 当日 raw 不存在，现拉 {} 零售单…".format(target))
            try:
                rows = pull_rows(target)
                log("拉到 {} 行".format(len(rows)))
                hist[target] = aggregate_day(rows, target)
                os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
                json.dump({"daily": hist, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")},
                          open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False)
                log("已落盘 {}".format(HISTORY))
            except Exception as e:
                log("拉取失败：{}".format(e))

    build_html(hist, target)
    log("DONE")


def compute_payload(hist, today):
    """计算冲刺看板数据（不写盘不渲染，供 build_unified.py 复用）"""
    start, end = CFG["start"], CFG["end"]
    sprint_dates = [(datetime.strptime(start, "%Y-%m-%d") + timedelta(days=i)).strftime("%Y-%m-%d")
                    for i in range(CFG["days"])]

    # 冲刺期内每日合计 + 每员工
    days_data = []
    for d in sprint_dates:
        emp = hist.get(d, {})
        total = sum(v["film_qty"] for v in emp.values())
        per_emp = {e: emp.get(e, {}).get("film_qty", 0) for e in CFG["emps"]}
        days_data.append({"date": d, "total": total, "perEmp": per_emp})

    done_total = sum(d["total"] for d in days_data)
    remain = CFG["store_target"] - done_total
    pct = min(100, done_total / CFG["store_target"] * 100)

    # 倒计时（剩余完整天数，含今天）
    today_dt = datetime.now()
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    days_left = max(0, (end_dt - today_dt).days + 1)

    # 已完成天数（含今天）
    done_days = sum(1 for d in days_data if d["date"] <= today)
    avg_done = done_total / done_days if done_days else 0
    need_daily = remain / days_left if days_left > 0 else remain
    # 预计完成日（按当前日均）
    eta = "—"
    if avg_done > 0 and remain > 0:
        eta_dt = today_dt + timedelta(days=remain / avg_done)
        eta = eta_dt.strftime("%m-%d")

    # 员工累计（冲刺期内）
    emp_stat = {}
    for e in CFG["emps"]:
        seq = [d["perEmp"].get(e, 0) for d in days_data if d["date"] <= today]
        emp_stat[e] = {
            "total": sum(seq),
            "today": days_data[-1]["perEmp"].get(e, 0) if days_data and days_data[-1]["date"] == today else (hist.get(today, {}).get(e, {}).get("film_qty", 0)),
            "best": max(seq) if seq else 0,
            "hitDays": sum(1 for x in seq if x >= CFG["emp_daily"]),
            "days": len(seq),
        }

    return {
        "cfg": CFG,
        "today": today,
        "daysData": days_data,
        "doneTotal": done_total,
        "remain": remain,
        "pct": round(pct, 1),
        "daysLeft": days_left,
        "avgDone": round(avg_done, 1),
        "needDaily": round(need_daily, 1),
        "eta": eta,
        "empStat": emp_stat,
        "genAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # 近期历史（用于背景趋势：start 前 14 天）
        "preDays": [{"date": d, "total": sum(v["film_qty"] for v in hist.get(d, {}).values())}
                    for d in sorted(hist.keys()) if d < start][-14:],
    }


def build_html(hist, today):
    """兼容旧入口：算数据 + 渲染 + 写独立 HTML（兜底用）"""
    P = compute_payload(hist, today)
    html = render_html(P)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "李家村_钢化膜核销冲刺.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log("看板已生成: {}".format(out))
    return P


def render_html(P):
    C = P["cfg"]
    max_day = max([d["total"] for d in P["daysData"]] + [C["daily_target"], 10])
    # 柱状图
    bars = []
    for d in P["daysData"]:
        is_today = d["date"] == P["today"]
        is_past = d["date"] < P["today"]
        h = (d["total"] / max_day * 100) if max_day else 0
        hit = d["total"] >= C["emp_daily"] * 2  # 门店日标 20 = 2人达标
        color = ("var(--grn)" if hit else ("var(--ind)" if is_past else ("var(--red)" if is_today else "#c7cbe0")))
        if not is_past and not is_today:
            color = "#d4d8e8"
        bars.append(f"""<div class="bar-col">
          <div class="bar-val num">{d['total'] if (is_past or is_today) else ''}</div>
          <div class="bar" style="height:{max(h,2)}%;background:{color}"></div>
          <div class="bar-x num">{d['date'][5:].replace('-','/')}</div>
        </div>""")
    # 目标线位置
    line_top = 100 - (C["daily_target"] / max_day * 100)

    emp_rows = []
    for e in C["emps"]:
        s = P["empStat"][e]
        pct_today = min(100, s["today"] / C["emp_daily"] * 100)
        ok = s["today"] >= C["emp_daily"]
        emp_rows.append(f"""<div class="emp-row">
          <div class="er-head"><span class="er-nm">{e}</span>
            <span class="er-today num" style="color:{'var(--grn)' if ok else 'var(--red)'}">今日 {s['today']}/{C['emp_daily']} 张{' ✓' if ok else ''}</span></div>
          <div class="er-bar"><i style="width:{pct_today}%;background:{'var(--grn)' if ok else 'var(--ind)'}"></i></div>
          <div class="er-sub num">冲刺累计 <b>{s['total']}</b> 张 · 达标 {s['hitDays']}/{s['days']} 天 · 单日最高 {s['best']} 张</div>
        </div>""")

    pre_bars = "".join(
        f"""<div class="bar-col mini"><div class="bar-val num">{d['total']}</div>
        <div class="bar" style="height:{max((d['total']/max_day*100),2)}%;background:#c7cbe0"></div>
        <div class="bar-x num">{d['date'][5:].replace('-','/')}</div></div>"""
        for d in P["preDays"])

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#7f1d1d">
<title>钢化膜核销冲刺</title>
<style>
:root{
  --bg:#f6f6f9; --card:#fff; --line:#e8e9f0; --line2:#d8dae6;
  --tx:#1a1c2e; --tx2:#575b73; --tx3:#8b90a6;
  --ind:#4f46e5; --red:#dc2626; --red-d:#b91c1c; --red-soft:#fef2f2;
  --grn:#16a34a; --grn-soft:#f0fdf4; --amb:#d97706; --amb-soft:#fffbeb;
  --shadow:0 1px 2px rgba(20,20,50,.05),0 4px 16px rgba(20,20,50,.06);
}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--tx);font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
.num{font-variant-numeric:tabular-nums}
::-webkit-scrollbar{display:none}
.appbar{position:sticky;top:0;z-index:50;background:rgba(127,29,29,.94);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);color:#fff;padding:calc(10px + env(safe-area-inset-top,0px)) 16px 10px}
.appbar .row{display:flex;align-items:center;gap:10px;max-width:960px;margin:0 auto}
.appbar h1{font-size:16.5px;font-weight:800;flex:1}
.appbar .live{font-size:10.5px;background:rgba(255,255,255,.16);padding:4px 10px;border-radius:12px;font-weight:700}
.main{max-width:960px;margin:0 auto;padding:14px 14px 40px}

/* 冲刺横幅 */
.hero{background:linear-gradient(135deg,#991b1b,#b91c1c 55%,#dc2626);border-radius:20px;padding:18px 17px;color:#fff;box-shadow:0 8px 26px rgba(185,28,28,.28);position:relative;overflow:hidden}
.hero:after{content:"";position:absolute;right:-30px;top:-30px;width:150px;height:150px;border-radius:50%;background:rgba(255,255,255,.07)}
.hero .t{font-size:11.5px;color:rgba(255,255,255,.75);font-weight:700;letter-spacing:.5px}
.hero .big{display:flex;align-items:baseline;gap:8px;margin-top:5px}
.hero .big .n{font-size:44px;font-weight:850;line-height:1}
.hero .big .u{font-size:14px;font-weight:700;color:rgba(255,255,255,.85)}
.hero .sub{font-size:12px;color:rgba(255,255,255,.8);margin-top:6px}
.hero .prog{height:12px;border-radius:8px;background:rgba(255,255,255,.18);margin-top:13px;overflow:hidden}
.hero .prog i{display:block;height:100%;border-radius:8px;background:linear-gradient(90deg,#fbbf24,#fde68a);transition:.4s}
.hero .grid{display:flex;gap:10px;margin-top:13px}
.hero .grid .g{flex:1;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.13);border-radius:12px;padding:9px 11px}
.hero .grid .g b{display:block;font-size:19px;font-weight:850}
.hero .grid .g span{font-size:10.5px;color:rgba(255,255,255,.7)}
.countdown{display:flex;align-items:center;gap:6px;margin-top:12px;font-size:12px;font-weight:700;color:#fde68a}

.sec{margin-top:18px}
.sec-h{display:flex;align-items:baseline;gap:8px;padding:0 2px 9px}
.sec-h h2{font-size:15.5px;font-weight:800}
.sec-h .d{font-size:11.5px;color:var(--tx3)}

/* 目标卡 */
.goal-cards{display:grid;grid-template-columns:1fr 1fr;gap:11px}
.gc{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:13px 14px;box-shadow:var(--shadow)}
.gc .t{font-size:11.5px;color:var(--tx3);font-weight:700}
.gc .v{font-size:26px;font-weight:850;margin-top:3px}
.gc .v small{font-size:12px;color:var(--tx3);font-weight:600}
.gc .d{font-size:11px;color:var(--tx2);margin-top:3px}
.gc.warn .v{color:var(--red)}

/* 今日员工战报 */
.emp-box{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:14px}
.emp-row{padding:10px 0;border-bottom:1px solid #f0f1f6}
.emp-row:last-child{border-bottom:none;padding-bottom:2px}
.er-head{display:flex;justify-content:space-between;align-items:baseline}
.er-nm{font-weight:800;font-size:14px}
.er-today{font-size:13px;font-weight:800}
.er-bar{height:8px;border-radius:5px;background:#eef0f6;margin-top:7px;overflow:hidden}
.er-bar i{display:block;height:100%;border-radius:5px;transition:.3s}
.er-sub{font-size:11px;color:var(--tx3);margin-top:5px}

/* 柱状图 */
.chart-box{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:15px 13px 11px;position:relative}
.chart{display:flex;gap:6px;align-items:flex-end;height:170px;position:relative}
.target-line{position:absolute;left:0;right:0;border-top:2px dashed var(--red);z-index:1}
.target-line span{position:absolute;right:0;top:-16px;font-size:10px;color:var(--red);font-weight:800;background:#fff;padding:0 4px}
.bar-col{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;position:relative;z-index:2}
.bar-val{font-size:10.5px;font-weight:800;color:var(--tx2);margin-bottom:2px;min-height:14px}
.bar{width:100%;max-width:26px;border-radius:5px 5px 2px 2px;min-height:2px;transition:.3s}
.bar-x{font-size:9.5px;color:var(--tx3);margin-top:5px;white-space:nowrap}
.pre-chart{display:flex;gap:6px;align-items:flex-end;height:90px;margin-top:4px}
.pre-chart .bar{background:#c9cddd!important}
.pre-chart .bar-val{color:var(--tx3)}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:10.5px;color:var(--tx3);margin-top:10px}
.legend i{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:3px;vertical-align:-1px}
.legend .dash{width:14px;height:0;border-top:2px dashed var(--red);border-radius:0}

/* 底部说明 */
.note{background:var(--amb-soft);border:1px solid #fde68a;border-radius:12px;padding:11px 13px;font-size:11.5px;color:#92400e;line-height:1.75;margin-top:16px}
.foot{text-align:center;font-size:10.5px;color:var(--tx3);margin-top:14px;line-height:1.7}
@media (min-width:768px){
  .main{padding-left:24px;padding-right:24px}
  .hero .big .n{font-size:52px}
  .emp-box,.chart-box{padding:18px}
  .chart{height:200px}
  .goal-cards{grid-template-columns:repeat(4,1fr)}
}
</style>
</head>
<body>
<header class="appbar"><div class="row">
  <h1>🎯 钢化膜核销冲刺</h1><span class="live">● 数据更新至 __TODAY__</span>
</div></header>
<main class="main">

  <div class="hero">
    <div class="t">10 天 200 张 · 门店冲刺总进度</div>
    <div class="big"><span class="n num">__DONE__</span><span class="u">/ __TARGET__ 张</span></div>
    <div class="sub">剩余 <b class="num">__REMAIN__</b> 张待核销 · 已完成 <b class="num">__PCT__%</b></div>
    <div class="prog"><i style="width:__PCT__%"></i></div>
    <div class="grid">
      <div class="g"><b class="num">__DAYSLEFT__</b><span>剩余天数</span></div>
      <div class="g"><b class="num">__NEED__</b><span>日均需核销(张)</span></div>
      <div class="g"><b class="num">__AVG__</b><span>当前日均(张)</span></div>
      <div class="g"><b class="num">__ETA__</b><span>预计完成</span></div>
    </div>
    <div class="countdown">⏳ 冲刺期 __START__ ~ __END__ · 还差 <b class="num">__REMAIN__</b> 张，加油！</div>
  </div>

  <div class="sec">
    <div class="sec-h"><h2>📌 今日战报</h2><span class="d">每人每天 10 张 · __TODAY__</span></div>
    <div class="emp-box">__EMPROWS__</div>
  </div>

  <div class="sec">
    <div class="sec-h"><h2>📈 冲刺 10 日</h2><span class="d">虚线 = 门店日均目标 20 张</span></div>
    <div class="chart-box">
      <div class="chart">
        <div class="target-line" style="top:__LINETOP__%"><span>目标 20 张/天</span></div>
        __BARS__
      </div>
      <div class="legend">
        <span><i style="background:var(--grn)"></i>达标日</span>
        <span><i style="background:var(--ind)"></i>已过日期</span>
        <span><i style="background:var(--red)"></i>今天</span>
        <span><i style="background:#d4d8e8"></i>未到日期</span>
        <span><i class="dash"></i>目标线</span>
      </div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-h"><h2>📉 冲刺前基线</h2><span class="d">开始前 14 天真实日均 __PREADAYS__ 张</span></div>
    <div class="chart-box"><div class="pre-chart">__PREBARS__</div></div>
  </div>

  <div class="note">📋 <b>口径</b>：钢化膜核销 = 普通零售单中「贴膜」分类商品且行净额为 0 的张数（用友零售单实时拉取）。数据每天 08:00 自动更新，与「🤖李家村数据看板」购物袋/钢化膜页同源同口径。</div>
  <div class="foot">生成时间 __GENAT__ · 李家村万达授权体验店</div>
</main>
</body>
</html>"""
    pre_avg = round(sum(d["total"] for d in P["preDays"]) / len(P["preDays"]), 1) if P["preDays"] else 0
    for k, v in {
        "__DONE__": P["doneTotal"], "__TARGET__": C["store_target"], "__REMAIN__": P["remain"],
        "__PCT__": P["pct"], "__DAYSLEFT__": P["daysLeft"], "__NEED__": P["needDaily"],
        "__AVG__": P["avgDone"], "__ETA__": P["eta"], "__START__": C["start"], "__END__": C["end"],
        "__EMPROWS__": "".join(emp_rows), "__BARS__": "".join(bars), "__PREBARS__": pre_bars,
        "__LINETOP__": round(line_top, 1), "__TODAY__": P["today"], "__GENAT__": P["genAt"],
        "__PREADAYS__": pre_avg,
    }.items():
        html = html.replace(k, str(v))
    return html


if __name__ == "__main__":
    main()
