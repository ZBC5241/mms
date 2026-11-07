#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据时点采集器（单一真源）

页面上每一块数据来自不同的链路、不同的时刻。过去统一用「页面构建时间」
冒充数据时点，容易误读。本模块从各源文件的真实时间字段采集 asOf，
由 build_unified.py（外层 W）与 enrich_profiles.py（内层 Q）注入页面。

采集口径：
  coupons      会员/在账券   ← unified_cache.json.generatedAt（券拉取完成时刻）
  cash         现金券明细     ← cash_coupons.json.generatedAt
  consumption  消费记录/九类/话术 ← consumption_meta.json.maxDate（最新一张零售单日期）
  profiles     会员画像档案   ← profiles_meta.json.builtAt（全表扫描完成时刻）
  film         钢化膜核销     ← shopbag/data/raw_YYYY-MM-DD.json 最新日期
  built        本页构建时刻   ← 当前时间（只用于说明文件生成时间，不当作数据时点）

用法：python3 data_asof.py   # 打印当前采集结果，便于核对
"""
import json
import os
import re
from datetime import datetime
import data_health  # 数据健康状态（失败降级必须可见，全局红线）

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
SHOPBAG_DATA = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/shopbag/data"
NA = "—"


def _load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# 🔴 禁止用文件 mtime 冒充真实采集时刻（全局红线）：源文件没写 generatedAt 就如实留空，
#    页面会把 '—' 标红，而不是显示一个看似新鲜的文件修改时间。
def _mtime(name):
    return NA


def _consumption_max_date():
    """优先读聚合器写的 meta；缺失时回退遍历 member_consumption.json。"""
    meta = _load("consumption_meta.json")
    if meta.get("maxDate"):
        return meta["maxDate"]
    p = os.path.join(DATA, "member_consumption.json")
    if not os.path.exists(p):
        return NA
    try:
        with open(p, encoding="utf-8") as f:
            cons = json.load(f)
        dates = []
        for v in cons.values():
            for it in (v.get("purchases") or []):
                d = (it.get("date") or "")[:10]
                if d:
                    dates.append(d)
        return max(dates) if dates else NA
    except Exception:
        return NA


def _film_max_date():
    if not os.path.isdir(SHOPBAG_DATA):
        return NA
    days = []
    for f in os.listdir(SHOPBAG_DATA):
        m = re.match(r"raw_(\d{4}-\d{2}-\d{2})\.json$", f)
        if m:
            days.append(m.group(1))
    return max(days) if days else NA


def collect(base=None):
    global BASE, DATA
    if base:
        BASE = base
        DATA = os.path.join(base, "data")
    u = _load("unified_cache.json")
    c = _load("cash_coupons.json")
    cm = _load("consumption_meta.json")
    pm = _load("profiles_meta.json")
    return {
        "coupons": u.get("generatedAt") or NA,
        "cash": c.get("generatedAt") or NA,
        "consumption": _consumption_max_date(),
        "consumptionRows": cm.get("rows") or 0,
        "consumptionBuilt": cm.get("builtAt") or NA,
        "profiles": pm.get("builtAt") or NA,
        "profileHits": pm.get("hits") or 0,
        "film": _film_max_date(),
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        # 本轮各环节真实成败；页面必须据实显示降级，不许静默拿旧缓存冒充新数据
        "health": data_health.read(),
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
