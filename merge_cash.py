#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把现金券（data/cash_coupons.json）合并进 unified_cache.json，供 --render-only 立即生效。
口径：仅 iStatus==0（与膜套包聚合一致）；交集会员追加券，纯现金券会员新增条目。
每日链路已由 build_unified.py 的 CASH_IDS 扩展覆盖（现为每日 08:00 自动含现金券）。
"""
import json
import os
import time
from collections import Counter, defaultdict

BASE = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint"
CACHE = os.path.join(BASE, "data", "unified_cache.json")
CASH = os.path.join(BASE, "data", "cash_coupons.json")

W = json.load(open(CACHE, encoding="utf-8"))
C = json.load(open(CASH, encoding="utf-8"))
rows = [r for r in C["rows"] if r.get("status") == 0]
print("缓存生成时间 {} → 合并现金券 {} 条（status==0）".format(W.get("generatedAt"), len(rows)))

gname_add = {"ty": "通用现金券", "dx": "定向现金券"}
W["gname"].update(gname_add)

by_mid = defaultdict(list)
for r in rows:
    by_mid[r["mid"]].append(r)

mmap = {m["mid"]: m for m in W["members"]}
old_mids = set(mmap.keys())
new_mids = [mid for mid in by_mid if mid not in old_mids]

# ① 追加/新增 members
for mid, cs in by_mid.items():
    cs.sort(key=lambda x: x["time"], reverse=True)
    first = cs[0]
    conv = [{"g": c["g"], "coupon": c["coupon"], "sub": c["sub"] or "",
             "time": c["time"], "sn": c["sn"], "left": c["left"] if c["left"] is not None else 1,
             "total": 1} for c in cs]
    if mid in mmap:
        m = mmap[mid]
        m["coupons"] = conv + m["coupons"]
        m["n"] = len(m["coupons"])
        m["leftSum"] = sum(c["left"] for c in m["coupons"])
        m["groups"] = sorted({c["g"] for c in m["coupons"]}, key=lambda g: ["xl", "xz", "mb", "ty", "dx"].index(g))
    else:
        W["members"].append({
            "mid": mid, "name": first["name"], "phone": first["phone"],
            "emp": first["emp"], "level": first["level"],
            "n": len(conv), "leftSum": sum(c["left"] for c in conv),
            "groups": sorted({c["g"] for c in conv}),
            "coupons": conv,
        })

W["members"].sort(key=lambda x: (-x["leftSum"], -x["n"]))

# ② emps 增量重算
emap = {e["emp"]: e for e in W["emps"]}
cash_cnt = Counter(r["g"] for r in rows)
for r in rows:
    e = emap.setdefault(r["emp"], {"emp": r["emp"], "members": 0, "membersWithUnpaid": 0,
                                   "unpaid": 0, "leftSum": 0, "byGroup": {}})
    e["unpaid"] += 1
    e["leftSum"] += (r["left"] if r["left"] is not None else 1)
    e["byGroup"] = dict(Counter(e["byGroup"]) + Counter({r["g"]: 1}))
for mid in new_mids:
    emp = by_mid[mid][0]["emp"]
    e = emap.setdefault(emp, {"emp": emp, "members": 0, "membersWithUnpaid": 0,
                              "unpaid": 0, "leftSum": 0, "byGroup": {}})
    e["membersWithUnpaid"] += 1
    e["members"] += 1  # 该员工原 members 计数不含他时近似 +1（交集人原已计）
W["emps"] = sorted(emap.values(), key=lambda x: -x["unpaid"])

# ③ total 重算
old_total = W.get("total", {"unpaid": 0, "members": 0, "leftSum": 0, "byGroup": {}})
W["total"] = {
    "unpaid": old_total["unpaid"] + len(rows),
    "members": len(W["members"]),
    "leftSum": old_total["leftSum"] + sum((r["left"] if r["left"] is not None else 1) for r in rows),
    "byGroup": dict(Counter(old_total["byGroup"]) + Counter(cash_cnt)),
}
W["generatedAt"] = time.strftime("%Y-%m-%d %H:%M")

json.dump(W, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
print("合并完成：会员 {} 人（新增 {}），券总数 {}，byGroup={}".format(
    len(W["members"]), len(new_mids), W["total"]["unpaid"], W["total"]["byGroup"]))
print("员工数:", len(W["emps"]))
