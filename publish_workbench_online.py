# -*- coding: utf-8 -*-
"""
会员工作台「在线版」发布（2026-11-07，18号）
把每日 08:00 链路产出的唯一成品发布到售票厅本机数据目录，供 GET /workbench 验票后下发。

安全边界：
  · 页面数据只写 server/data/workbench.html（0600），绝不进 GitHub Pages / 任何公开静态网址；
  · 无票 curl /workbench 只能拿到登录页，看不到任何会员明细；
  · 发布失败不改旧页面，而是把 meta.status 写成 fail，服务端会在页面顶部显式标红，
    绝不静默拿旧数据冒充本轮更新（数据真实性红线）。
"""
import json, os, time, sys

WS = "/Users/mac/WorkBuddy/2026-09-11-21-21-56"
SRC = os.path.join(WS, "会员管理工作台.html")
SERVER_DATA = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/storyos/server/data"
DST = os.path.join(SERVER_DATA, "workbench.html")
META = os.path.join(SERVER_DATA, "workbench_meta.json")
HEALTH = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint/data/health.json"
CORE = ("cash", "coupons", "render", "consumption", "retail")


def atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write_meta(status, note, nbytes=0):
    meta = {
        "status": status,
        "note": note,
        "publishedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sourceBytes": nbytes,
        "source": SRC,
    }
    atomic(META, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
    return meta


def health_status():
    """核心块任一非 ok → degraded；读不到健康文件也判 degraded，不猜「没问题」。"""
    try:
        with open(HEALTH, encoding="utf-8") as f:
            h = json.load(f)
        blocks = h.get("blocks")
        if not isinstance(blocks, dict):
            return "degraded", "健康文件 blocks 结构异常，无法确认本轮真实状态"
        bad = []
        for k in CORE:
            v = blocks.get(k)
            st = str(v.get("status", "")) if isinstance(v, dict) else str(v or "")
            if st != "ok":
                bad.append(k + ":" + (st or "缺失"))
        if bad:
            return "degraded", "核心块非 ok：" + ", ".join(bad)
        return "ok", ""
    except Exception as e:
        return "degraded", "健康状态读取失败：%s" % e


def main():
    try:
        if not os.path.exists(SRC):
            raise RuntimeError("唯一成品不存在：%s" % SRC)
        with open(SRC, "rb") as f:
            raw = f.read()
        if len(raw) < 1024 * 1024:
            raise RuntimeError("成品体积异常偏小（%d 字节），拒绝发布" % len(raw))
        s = raw.decode("utf-8")
        for ph in ("__DATA_W__", "__DATA_F__", "__DATA_Q__"):
            if ph in s:
                raise RuntimeError("成品仍含未注入占位符 %s，拒绝发布" % ph)
        if "srcdoc=" not in s or 'class="mq-frame"' not in s:
            raise RuntimeError("会员查询内层未 srcdoc 内联，拒绝发布")
        status, note = health_status()
        atomic(DST, raw)
        meta = write_meta(status, note, len(raw))
        print("OK 在线版已发布 status=%s bytes=%d at=%s" %
              (status, len(raw), meta["publishedAt"]))
        return 0
    except Exception as e:
        # 不动旧页面，只把状态标 fail：服务端会显式标红「本轮发布失败，显示的是 XX 时刻的页面」
        try:
            write_meta("fail", "本轮发布失败：%s" % e)
        except Exception as ee:
            print("❌ 连失败状态都写不进去：%s" % ee, file=sys.stderr)
        print("❌ 在线版发布失败：%s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
