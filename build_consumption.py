#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""消费产品/品类聚合层（场景①⑤的数据底座）
把用友零售单(rm_retailvouchlist)按会员手机号聚合，产出每人「买过啥」。

输入：
  - shopbag/data/raw_*.json            （已落盘零售单，抽样日）
  - data/retail_all.json              （可选，pull_retail_history.py 全量拉取结果）
输出：
  - data/member_consumption.json      {phone: {...消费聚合...}}

品类/系列判定：基于商品名关键字（华为产品命名稳定），比 productClass 更贴合"系列"概念。
"""
import json
import os
import glob
import re
from collections import defaultdict
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
SHOPBAG_DATA = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/shopbag/data"
OUT = os.path.join(BASE, "data", "member_consumption.json")


# ---------- 品类/系列分类（基于商品名）----------
def classify(name):
    n = (name or "").lower()
    # 系列（手机型号档）
    series = ""
    if re.search(r"mate\s*book|matepad|平板|笔记本", n):
        cat = "电脑/平板"
        m = re.search(r"matebook|matepad|mate\s*book", n)
        series = m.group(0).upper() if m else "平板/笔电"
    elif re.search(r"watch|手环|gt\s*\d|手表", n):
        cat = "穿戴"
        m = re.search(r"watch\s*\d+|gt\s*\d+|手环|手表", n)
        series = m.group(0).upper() if m else "手表/手环"
    elif re.search(r"freebuds|耳机|音箱|sound|眼镜|vision", n):
        cat = "音频/生态"
        series = "音频"
    elif re.search(r"mate\s*\d{2}|pura|nova|畅享|麦芒|magic|hi|nova", n):
        cat = "手机"
        m = re.search(r"mate\s*\d{2}|pura\s*\d*|nova\s*\d*|畅享|麦芒", n)
        series = (m.group(0).upper() if m else "手机")
    elif re.search(r"贴膜|膜|钢化|保护壳|壳|充电器|线缆|配件|数据线", n):
        cat = "配件/服务"
        series = "配件"
    elif re.search(r"订金|定金|套餐|合约|权益|卡|券|余额|储值", n):
        cat = "套餐/服务"
        series = "套餐/服务"
    else:
        cat = "其他"
        series = ""
    return cat, series


def load_raw_rows():
    rows = []
    # 1) shopbag 抽样日
    for f in glob.glob(os.path.join(SHOPBAG_DATA, "raw_*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            rs = d if isinstance(d, list) else (d.get("rows") or d.get("recordList") or [])
            rows.extend(rs)
        except Exception as e:
            # 红线：读取失败不能静默跳过，否则残缺数据会被当成全量消费记录
            raise RuntimeError("消费数据源读取失败 {}: {}".format(f, e))
    # 2) 全量拉取结果（若存在）
    full = os.path.join(BASE, "data", "retail_all.json")
    if os.path.exists(full):
        try:
            d = json.load(open(full, encoding="utf-8"))
            rs = d if isinstance(d, list) else (d.get("rows") or d.get("recordList") or [])
            rows.extend(rs)
        except Exception as e:
            raise RuntimeError("全量零售单 {} 读取失败（不得静默沿用残缺数据）: {}".format(full, e))
    return rows


def main():
    rows = load_raw_rows()
    print(f"[消费聚合] 原始零售单行: {len(rows)}")

    cons = {}  # phone -> agg
    max_date = ""  # 真实消费数据截止日（供页面数据时点使用）

    def get_phone(r):
        return r.get("cMemberCode") or r.get("cMobileNo") or ""

    for r in rows:
        phone = get_phone(r)
        if not phone:
            continue
        # 逐行明细
        details = r.get("retailVouchDetails", [])
        if not isinstance(details, list):
            # 字段是扁平前缀 retailVouchDetails_xxx，需重组
            details = [r]
        # 处理扁平结构：同一行可能只有一组 retailVouchDetails_ 字段
        if not details:
            details = [r]
        for d in details:
            pname = (d.get("retailVouchDetails_product_cName")
                     or d.get("product_cName")
                     or d.get("retailVouchDetails_productsku_skuName") or "")
            if not pname:
                continue
            qty = float(d.get("retailVouchDetails_fQuantity") or 0)
            amt = float(d.get("retailVouchDetails_fMoney") or 0)
            cls = d.get("retailVouchDetails_product_productClass_name") or ""
            emp = (d.get("retailVouchDetails_iEmployeeid_name")
                   or r.get("retailVouchHeaderExtend_Employeename") or "未记录")
            date = (r.get("vouchdate") or r.get("dDate") or "")[:10]
            if date > max_date:
                max_date = date
            code = r.get("code") or ""
            if phone not in cons:
                cons[phone] = {
                    "phone": phone,
                    "name": r.get("iMemberid_name") or r.get("cCusperson") or "",
                    "purchases": [],
                    "cat": defaultdict(lambda: {"qty": 0.0, "amt": 0.0, "cnt": 0}),
                    "series": defaultdict(lambda: {"qty": 0.0, "amt": 0.0, "cnt": 0}),
                    "emps": set(),
                    "total_qty": 0.0,
                    "total_amt": 0.0,
                    "first": date,
                    "last": date,
                    "orders": set(),
                }
            a = cons[phone]
            a["purchases"].append({
                "date": date, "code": code, "product": pname,
                "class": cls, "qty": qty, "amt": amt, "emp": emp,
            })
            cat, series = classify(pname)
            a["cat"][cat]["qty"] += qty
            a["cat"][cat]["amt"] += amt
            a["cat"][cat]["cnt"] += 1
            if series:
                a["series"][series]["qty"] += qty
                a["series"][series]["amt"] += amt
                a["series"][series]["cnt"] += 1
            a["emps"].add(emp)
            a["total_qty"] += qty
            a["total_amt"] += amt
            a["orders"].add(code)
            if date and (not a["first"] or date < a["first"]):
                a["first"] = date
            if date and (not a["last"] or date > a["last"]):
                a["last"] = date

    # 收尾：set->list，defaultdict->dict，purchases 按日期倒序
    out = {}
    for phone, a in cons.items():
        a["emps"] = sorted(a["emps"])
        a["orders"] = sorted(a["orders"])
        a["cat"] = {k: dict(v) for k, v in a["cat"].items()}
        a["series"] = {k: dict(v) for k, v in a["series"].items()}
        a["purchases"].sort(key=lambda x: x["date"], reverse=True)
        # 只保留最近 50 条明细，避免体量爆炸
        a["purchase_count"] = len(a["purchases"])
        a["purchases"] = a["purchases"][:50]
        out[phone] = a

    def _atomic_json(path, obj):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    _atomic_json(OUT, out)
    # 数据时点：真实截止日 = 最新一张零售单的日期，不用构建时间冒充
    meta = {"maxDate": max_date or "—", "rows": len(rows),
            "members": len(out), "builtAt": datetime.now().strftime("%Y-%m-%d %H:%M")}
    _atomic_json(os.path.join(BASE, "data", "consumption_meta.json"), meta)
    print(f"[消费聚合] 真实数据截止日 {meta['maxDate']}，已写 consumption_meta.json")
    print(f"[消费聚合] 已写入 {OUT}")
    print(f"[消费聚合] 覆盖会员数(有消费记录): {len(out)}")
    # 抽样打印
    if out:
        sample_phone = next(iter(out))
        s = out[sample_phone]
        print(f"[消费聚合] 样例 {sample_phone} {s['name']}: "
              f"消费{s['purchase_count']}笔/共{len(s['orders'])}单, 金额{s['total_amt']:.0f}, "
              f"品类={list(s['cat'].keys())}, 归属={s['emps']}")


if __name__ == "__main__":
    main()
