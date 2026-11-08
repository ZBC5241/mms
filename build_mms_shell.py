#!/usr/bin/env python3
# build_mms_shell.py —— 从每日成品 workbench.html 逐字剥离三段数据常量，生成可公开托管的纯 UI 骨架
# 用法：python3 build_mms_shell.py
# 产物：mms_shell.html（提交到 mms 仓库，由 GitHub Pages CDN 托管；不含任何会员 PII）
# 说明：UI 只在模板改版时才变化，日常 08:00 只更新数据，故本脚本无需进流水线。
import os, re, json, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(
    BASE, '..', 'storyos', 'server', 'data', 'workbench.html'))
OUT = os.path.join(BASE, 'mms_shell.html')
QUERY = os.path.join(BASE, 'data', 'member_query_cache.json')

# (变量名, 起始锚点, 结束锚点, 是否 HTML 转义态)
SEGMENTS = [
    ('Q', 'const Q = ', '\nconst GN = Q.gname;'),
    ('W', 'const W = ', '\nconst F = '),
    ('F', 'const F = ', '\nconst GN = W.gname;'),
]


def main():
    s = open(SRC, encoding='utf-8').read()
    spans = []
    for name, kstart, kend in SEGMENTS:
        i = s.find(kstart)
        if i < 0:
            raise SystemExit('锚点缺失: ' + kstart)
        st = i + len(kstart)
        j = s.find(kend, st)
        if j < 0:
            raise SystemExit('结束锚点缺失: ' + kend)
        spans.append((name, st, j, s[st:j]))
    # 从后往前替换，避免偏移错乱
    shell = s
    for name, st, j, seg in sorted(spans, key=lambda x: -x[1]):
        shell = shell[:st] + '__INJECT_%s__;' % name + shell[j:]
        print('%s 段 %d 字符 → 占位符' % (name, len(seg)))

    # ---- PII 扫描：字段名/函数名属 UI 渲染逻辑，只查「真实数据值」 ----
    phones = re.findall(r'(?<!\d)1[3-9]\d{9}(?!\d)', shell)
    names, mids, amounts, dates = [], [], [], []
    try:
        q = json.load(open(QUERY, encoding='utf-8'))
        for m in (q.get('members') or [])[:60]:
            n = (m.get('name') or '').strip()
            if len(n) >= 2:
                names.append(n)
            if m.get('mid'):
                mids.append(str(m['mid']))
            for c in (m.get('unused') or [])[:2]:
                if c.get('no'):
                    mids.append(str(c['no']))
            for p in ((m.get('consumption') or {}).get('purchases') or [])[:3]:
                if p.get('amount'):
                    amounts.append(str(p['amount']))
                if p.get('date'):
                    dates.append(str(p['date']))
        for ph, v in list((q.get('history') or {}).items())[:60]:
            if isinstance(v, dict):
                n = (v.get('name') or '').strip()
                if len(n) >= 2:
                    names.append(n)
            for p in ((v or {}).get('purchases') or [])[:3] if isinstance(v, dict) else []:
                if p.get('amount'):
                    amounts.append(str(p['amount']))
                if p.get('date'):
                    dates.append(str(p['date']))
    except Exception as e:
        print('样本读取失败(不阻断):', e)
    hit_names = [n for n in set(names) if n and n in shell]
    hit_mids = [m for m in set(mids) if len(m) >= 12 and m in shell]
    hit_amounts = [a for a in set(amounts) if len(a) >= 4 and a in shell]
    # 裸日期字符串会命中 CSS 注释里的版本日期，只认带键的数据对
    hit_dates = [d for d in set(dates) if len(d) >= 8 and
                 ('"date":"' + d + '"' in shell or
                  '&quot;date&quot;:&quot;' + d in shell)]
    # 带值的数据对形态（字段名单独出现属代码，不算）
    pairs = [k for k in ('"purchases":[', '&quot;purchases&quot;:[',
                         '"members":[', '"history":{', '"mid":"',
                         '"phone":"', '"name":"') if k in shell]
    print('骨架字符数: %d (%.0f KB)' % (len(shell), len(shell) / 1024))
    print('手机号 %d | 真实姓名 %d %s | 会员/券号 %d | 真实金额 %d | 日期数据对 %d | 数据对 %s'
          % (len(phones), len(hit_names), hit_names[:5], len(hit_mids),
             len(hit_amounts), len(hit_dates), pairs))
    if phones or hit_names or hit_mids or hit_amounts or hit_dates or pairs:
        raise SystemExit('❌ PII / 数据残留，拒绝生成公开骨架')
    for name in ('__INJECT_Q__', '__INJECT_W__', '__INJECT_F__'):
        if shell.count(name) != 1:
            raise SystemExit('占位符数量异常: ' + name)

    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(shell)
    os.replace(tmp, OUT)
    h = hashlib.sha256(shell.encode('utf-8')).hexdigest()[:16]
    print('✅ 已生成 %s | sha256[:16]=%s' % (OUT, h))
    with open(OUT + '.sig', 'w', encoding='utf-8') as f:
        f.write(h)


main()
