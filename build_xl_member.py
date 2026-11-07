# -*- coding: utf-8 -*-
"""
把最新的会员查询页（构建内部中间产物 .build/会员查询_内联源.html）内联进
会员管理工作台.html 的「会员查询」tab。
2026-11-05 晨哥拍板「只要一个文件」：唯一交付物就是 会员管理工作台.html，
会员查询页不再作为独立 HTML 落到工作区顶层，只作构建中间产物存在。
使用 <iframe srcdoc="..."> 方式：把会员查询整页作为单文件内联内容嵌入，
不依赖 file:// 跨文件加载（Safari/严格策略浏览器会拦截跨文件 iframe 导致黑屏）。
会员查询页自带 `window.self!==window.top` 判断，被内联时自动隐藏自身顶栏。
幂等：每次重跑都会用最新 会员查询.html 覆盖旧的 iframe（无论之前是 src= 还是 srcdoc=）。
"""
import re, os

WS = "/Users/mac/WorkBuddy/2026-09-11-21-21-56"
XL = os.path.join(WS, "会员管理工作台.html")
MQ = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint/.build/会员查询_内联源.html"

def esc(s: str) -> str:
    # 转义为 srcdoc 属性值：先 & 后其他，顺序很重要
    return (s.replace("&", "&amp;")
             .replace('"', "&quot;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))

def main():
    if not os.path.exists(MQ):
        raise SystemExit(f"未找到会员查询内部中间产物（请先跑 enrich_profiles.py --merge-v1）: {MQ}")
    member_html = open(MQ, encoding="utf-8").read()
    # 不要先做 </iframe> 预替换：esc() 会把 & 二次转成 &amp;，
    # 解码后内层 <script> 里的字面量会变成 &lt;/iframe&gt; 文本，破坏内层 JS。
    # esc() 已把 < 转成 &lt;，天然不会闭合外层 iframe。
    srcdoc = esc(member_html)

    xl = open(XL, encoding="utf-8").read()
    pat = r'<iframe class="mq-frame"[^>]*>[\s\S]*?</iframe>'
    if not re.search(pat, xl):
        raise SystemExit("会员管理工作台中未找到 <iframe class=\"mq-frame\"> 占位，无法内联")
    new_iframe = f'<iframe class="mq-frame" title="会员权益查询" srcdoc="{srcdoc}"></iframe>'
    xl2 = re.sub(pat, lambda m: new_iframe, xl, count=1)
    tmp = XL + '.tmp'
    open(tmp, 'w', encoding='utf-8').write(xl2)
    os.replace(tmp, XL)
    print(f"OK 内联完成：会员管理工作台现 {len(xl2)//1024}KB（含会员查询整页 srcdoc）")
    # 单文件交付护栏：顶层若又冒出独立页，明确告警（本脚本不会再写它）
    legacy = os.path.join(WS, "会员查询.html")
    if os.path.exists(legacy):
        print(f"⚠️ 单文件交付校验失败：工作区顶层仍存在独立页 {legacy}，请移入 .build/ 或删除")
    old_name = os.path.join(WS, "星联会员-膜套包汇总.html")
    if os.path.exists(old_name):
        print(f"⚠️ 旧文件名残留：{old_name}（2026-11-06 起唯一交付名为 会员管理工作台.html）")

if __name__ == "__main__":
    main()
