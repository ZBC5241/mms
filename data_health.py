#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据健康状态（全局红线的代码落地）

晨哥 2026-11-06 定的最高红线：**坚决禁止「以旧数据充新数据」**。
任何一块数据拉取失败，都不能静默拿上一轮缓存顶上、还让页面/推送看起来像今天最新。

本模块由 run_film_sprint.sh 在每一步真实执行后打点，data_asof.py 把它注入页面：
  python3 data_health.py reset                     # 每轮任务开始，清空上一轮状态
  python3 data_health.py mark <key> <ok|fail|partial> [说明]
  python3 data_health.py bad <key>                # 输出 fail 原因（空 = 正常），供推群守门判断
  python3 data_health.py alert                    # 核心数据失败时，往群里发文本告警而不是推旧文件

状态含义：
  ok       本轮真实拉取/计算成功
  fail     本轮失败，页面显示的是旧缓存（必须在页面红色横幅里显式说明）
  partial  本轮部分成功（例如周一回补失败但 7 天增量成功）
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(BASE, "data", "health.json")

# 与页面「数据时点」一一对应；render/wecom 是链路状态，不是数据块
LABEL = {
    "retail": "消费记录增量",
    "monthly": "当月整月回补",
    "consumption": "消费聚合重算",
    "coupons": "券 / 会员拉取",
    "cash": "现金券明细",
    "promo": "促销行情快照",
    "derived": "到期日/注册日推算",
    "profiles": "会员画像全表扫描",
    "render": "页面渲染",
    "wecom": "群推送",
}
WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fe438f09-4124-47b9-9ff8-4a72783d55a0"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read():
    """健康状态文件损坏/缺失时返回显式 fail，绝不静默当「一切正常」（全局红线）。"""
    try:
        with open(P, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("blocks"), dict):
            return d
        raise ValueError("health.json 结构异常")
    except Exception as e:
        return {"runAt": None, "blocks": {
            "health": {"label": "数据健康状态文件", "status": "fail",
                        "note": "health.json 缺失或损坏，本轮各环节成败无法确认：{}".format(e),
                        "at": _now()}}}


def write(d):
    os.makedirs(os.path.dirname(P), exist_ok=True)
    tmp = P + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, P)
    return d


def reset():
    return write({"runAt": _now(), "blocks": {}})


def mark(key, status, note=""):
    d = read()
    if "blocks" not in d:
        d = {"runAt": _now(), "blocks": {}}
    d["blocks"][key] = {
        "label": LABEL.get(key, key),
        "status": status,
        "note": note,
        "at": _now(),
    }
    d["updatedAt"] = _now()
    return write(d)


def bad(key):
    d = read()
    b = d.get("blocks", {}).get(key) or {}
    return "" if b.get("status") == "ok" else (b.get("note") or b.get("status") or "未执行")


def alert():
    """核心数据失败：群里如实说明哪块坏了、当前文件不是新的，不推旧文件冒充。"""
    d = read()
    fails = [(k, v) for k, v in d.get("blocks", {}).items() if v.get("status") == "fail"]
    if not fails:
        return False
    lines = ["⚠️ 会员管理工作台 · 今日数据未能全部更新",
             "本轮以下环节失败，为避免旧数据冒充新数据，今天不推送工作台文件：", ""]
    for k, v in fails:
        lines.append("• {}：{}".format(v.get("label", k), v.get("note") or "拉取失败"))
    lines += ["", "本地文件仍是上一轮缓存，请查看日志 logs/run.log。",
              "运行时刻 " + (d.get("runAt") or "—")]
    payload = {"msgtype": "text", "text": {"content": "\n".join(lines)}}
    req = urllib.request.Request(WEBHOOK, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        print(r.read().decode("utf-8"))
    return True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "reset":
        reset()
        print("health reset")
    elif cmd == "mark":
        mark(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
    elif cmd == "bad":
        print(bad(sys.argv[2]))
    elif cmd == "alert":
        alert()
    else:
        print(json.dumps(read(), ensure_ascii=False, indent=2))
