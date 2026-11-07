#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量零售历史拉取器（场景①⑤数据底座）
按店长号登录用友，拉 rm_retailvouchlist（零售单明细），按月分片避免单次超量，
落盘到 data/retail_all.json（与 shopbag 抽样 raw 合并即全量消费数据）。

用法：
  python3 pull_retail_history.py                 # 默认 2026-01-01 ~ 今天（全量）
  python3 pull_retail_history.py 2026-06-01     # 指定起始日（按月分片）
  python3 pull_retail_history.py --recent 3     # 每日增量：最近 3 天，按天分片，追加去重
"""
import json
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "data", "retail_all.json")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
YY = "https://c3.yonyoucloud.com"
ACCT = "18161914293"
REPORT_ID = "rm_retailvouchlist"


def log(m):
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), m), flush=True)


def get_yonyou_pwd():
    out = subprocess.run(["security", "find-generic-password", "-s", "yonyou", "-a", ACCT, "-w"],
                         capture_output=True, text=True, timeout=15)
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    raise RuntimeError("钥匙串无 yonyou 密码")


def login(pg):
    pg.goto(YY, wait_until="domcontentloaded", timeout=60000)
    time.sleep(4)
    try:
        acc = pg.query_selector(".button_accept")
        if acc:
            acc.click(); time.sleep(1.5)
    except Exception:
        pass
    for _ in range(15):
        lf = None
        for f in pg.frames:
            if "euc.yonyoucloud.com" in (f.url or ""):
                lf = f; break
        if lf and lf.query_selector("#username"):
            break
        lf = None
        time.sleep(2)
    if not lf:
        raise RuntimeError("登录框未出现")
    lf.fill("#username", ACCT); time.sleep(0.3)
    lf.fill("#password", get_yonyou_pwd()); time.sleep(0.3)
    lf.click("#submit_btn_login")
    for _ in range(20):
        time.sleep(2)
        if "login" not in pg.url.lower() and "cas" not in pg.url.lower():
            break
    time.sleep(4)
    if "login" in pg.url.lower() or "cas" in pg.url.lower():
        raise RuntimeError("登录未完成")
    log("已登录店长号")


def pull_range(pg, start, end):
    all_rows = []
    page_idx = 1
    while True:
        body = {
            "billnum": REPORT_ID,
            "condition": {"commonVOs": [{"itemName": "vouchdate", "value1": start, "value2": end}]},
            "page": {"pageSize": 1000, "pageIndex": page_idx},
            "serviceCode": REPORT_ID,
            "terminalType": "1",
        }
        resp = pg.evaluate("""(args) => {
            const body = JSON.parse(args.body);
            return fetch('/yonbip-mkt-retailweb/report/list?terminalType=1&serviceCode=rm_retailvouchlist&locale=zh_CN', {
                method:'POST', credentials:'include',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify(body)
            }).then(r=>r.text()).catch(e=>'FETCH_ERR:'+e.message);
        }""", {"body": json.dumps(body, ensure_ascii=False)})
        if resp.startswith("FETCH_ERR"):
            raise RuntimeError("API fetch 失败: " + resp[:200])
        j = json.loads(resp)
        # 红线：接口报错/会话过期返回 {"code":500} 时不能当「本页 0 条」提前结束分页
        if not isinstance(j, dict) or not isinstance(j.get("data"), dict) \
                or not isinstance(j["data"].get("recordList"), list):
            raise RuntimeError("用友零售单接口返回异常（可能登录态失效）: " + str(j)[:200])
        rows = j["data"]["recordList"]
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 1000:
            break
        page_idx += 1
        time.sleep(1)
    return all_rows


def month_ranges(start_str, end_str):
    s = datetime.strptime(start_str, "%Y-%m-%d")
    e = datetime.strptime(end_str, "%Y-%m-%d")
    ranges = []
    cur = s.replace(day=1)
    while cur <= e:
        nxt = (cur + timedelta(days=32)).replace(day=1)
        ms = cur.strftime("%Y-%m-%d")
        me = (nxt - timedelta(days=1)).strftime("%Y-%m-%d")
        if me > end_str:
            me = end_str
        ranges.append((ms, me))
        cur = nxt
    return ranges


def main():
    end = datetime.now().strftime("%Y-%m-%d")
    if len(sys.argv) > 1 and sys.argv[1] == "--recent":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        n = max(1, min(n, 31))
        start = (datetime.now() - timedelta(days=n - 1)).strftime("%Y-%m-%d")
        ranges = [(start_dt, start_dt) for start_dt in
                  [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]]
        log("每日增量：最近 {} 天，按天分片".format(n))
    else:
        start = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
        ranges = month_ranges(start, end)
        log("拉取区间 {} ~ {} ，分 {} 个月片".format(start, end, len(ranges)))

    # 已有数据（追加 + 回补更新）：老单被补录/修改时用最新一行覆盖，历史不丢
    existing = []
    idx = {}
    if os.path.exists(OUT):
        try:
            d = json.load(open(OUT, encoding="utf-8"))
            existing = d if isinstance(d, list) else (d.get("recordList") or [])
            for i, r in enumerate(existing):
                key = (r.get("code"), r.get("vouchdate"), r.get("cMemberCode"))
                idx[key] = i
            log("已存在零售单 {} 行，将追加/回补更新".format(len(existing)))
        except Exception as e:
            # 红线：57M 历史读失败时绝不能 existing=[] 清空全量，否则当天只剩最近 7 天
            raise RuntimeError("已有全量零售单 {} 读取失败，拒绝以空历史覆盖: {}".format(OUT, e))
    all_rows = list(existing)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=CHROME,
                              args=["--no-sandbox", "--disable-dev-shm-usage",
                                    "--disable-blink-features=AutomationControlled"])
        try:
            pg = b.new_page()
            login(pg)
            for (ms, me) in ranges:
                rows = pull_range(pg, ms + " 00:00:00", me + " 23:59:59")
                added = 0
                updated = 0
                for r in rows:
                    key = (r.get("code"), r.get("vouchdate"), r.get("cMemberCode"))
                    if key in idx:
                        all_rows[idx[key]] = r   # 回补：老单变更用最新覆盖
                        updated += 1
                    else:
                        idx[key] = len(all_rows)
                        all_rows.append(r)
                        added += 1
                log("区间 {}~{} 拉取 {} 行，新增 {} / 更新 {}（累计 {})".format(
                    ms, me, len(rows), added, updated, len(all_rows)))
                time.sleep(1)
        finally:
            b.close()
    # 原子写：57M 大文件先写临时文件再替换，避免中断留下半截 JSON
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    log("全量零售单已落盘: {} ({} 行)".format(OUT, len(all_rows)))


if __name__ == "__main__":
    main()
