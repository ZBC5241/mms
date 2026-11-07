#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补拉：星联会员-通用现金券 + 定向现金券（晨哥说的 50元通用/定向券）
复用 build_unified 登录链路；拉券实例 + 剩余次数 + 李家村会员归属 → data/cash_coupons.json
"""
import importlib.util
import json
import os
import time
from collections import defaultdict

BASE = "/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint"

_spec = importlib.util.spec_from_file_location("bu", os.path.join(BASE, "build_unified.py"))
bu = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bu)

STORE = bu.STORE  # 李家村 2387138245738627078
TARGET = {
    "ty": "2551884343585275909",  # 星联会员-通用现金券
    "dx": "2551888741631787015",  # 星联会员-定向现金券
}


def log(m):
    print("[{}] {}".format(time.strftime("%H:%M:%S"), m), flush=True)


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=bu.CHROME,
                              args=["--no-sandbox", "--disable-dev-shm-usage",
                                    "--disable-blink-features=AutomationControlled"])
        try:
            pg = b.new_page()
            pg.goto("https://c3.yonyoucloud.com", wait_until="domcontentloaded", timeout=60000)
            time.sleep(8)
            session_ok = False
            try:
                session_ok = pg.evaluate("""async()=>{
                  try{
                    const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                      method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                      body:JSON.stringify({"page":{"pageSize":1,"pageIndex":1,"totalCount":1},"billnum":"mm_memberslist",
                        "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true},
                        "bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})
                    });
                    const j=await r.json();
                    return j.code===200;
                  }catch(e){ return false; }
                }""")
            except Exception:
                session_ok = False
            if session_ok:
                log("经理号会话有效")
            else:
                log("会话失效，登录经理号…")
                bu.yonyou_login(pg, "18591910491")

            # 1) 李家村会员（复用原逻辑）
            log("拉李家村会员列表…")
            members = pg.evaluate("""async(store)=>{
              const out=[];
              for(let pi=1;pi<=40;pi++){
                const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                  method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({"page":{"pageSize":500,"pageIndex":pi,"totalCount":1},"billnum":"mm_memberslist",
                    "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],
                    "filtersId":"87589122","solutionId":"2458405643108745224","bInit":true,
                    "simpleVOs":[{"field":"iStop","op":"in","value1":[0,1]},{"field":"bVisitor","value1":0,"op":"eq"},{"field":"iStoreID","op":"eq","value1":store}]},
                    "bEmptyWithoutFilterTree":true,"ownDomain":"uhy","tplid":"2458399037449043988"})
                });
                const j=await r.json();
                const l=(j.data&&j.data.recordList)||[];
                out.push(...l);
                if(l.length<500) break;
              }
              return out;
            }""", STORE)
            log("会员 {} 人".format(len(members)))
            mmap = {m['id']: m for m in members}

            # 2) 两个现金券模板的实例
            inst = pg.evaluate("""async(ids)=>{
              const out=[];
              for(const cid of ids){
                for(let pi=1;pi<=40;pi++){
                  const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                    method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({"page":{"pageSize":500,"pageIndex":pi,"totalCount":1},"billnum":"mm_membercouponlist",
                      "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true,
                        "simpleVOs":[{"field":"iCouponID","op":"eq","value1":cid}]},
                      "bEmptyWithoutFilterTree":true,"serviceCode":"SDMB020101","locale":"zh_CN","ownDomain":"uhy"})
                  });
                  const j=await r.json();
                  const l=(j.data&&j.data.recordList)||[];
                  out.push(...l);
                  if(l.length<500) break;
                }
              }
              return out;
            }""", list(TARGET.values()))
            log("现金券实例 {} 条".format(len(inst)))

            # 实例样本（看字段）
            if inst:
                s = dict(inst[0])
                for k in list(s.keys()):
                    if s[k] is None:
                        del s[k]
                log("样本字段: {}".format(json.dumps(s, ensure_ascii=False)[:600]))

            # 3) 剩余次数（cSN in 批量）
            sns = [r['cSN'] for r in inst if r.get('cSN')]
            log("拉 {} 个券码的剩余次数…".format(len(sns)))
            recv = pg.evaluate("""async(sns)=>{
              const out=[];
              for(let i=0;i<sns.length;i+=100){
                const chunk=sns.slice(i,i+100);
                const r=await fetch('/yonbip-mkt-vip/bill/list?serviceCode=SDMB020101&locale=zh_CN&terminalType=1',{
                  method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({"page":{"pageSize":500,"pageIndex":1,"totalCount":1},"billnum":"pt_couponreceive",
                    "condition":{"commonVOs":[{"itemName":"schemeName","value1":"默认方案"},{"itemName":"isDefault","value1":true}],"filtersId":"87589122","solutionId":"2458405643108745224","bInit":true,
                      "simpleVOs":[{"field":"cSN","op":"in","value1":chunk}]},
                    "bEmptyWithoutFilterTree":true,"serviceCode":"SDMB020101","locale":"zh_CN","ownDomain":"uhy"})
                });
                const j=await r.json();
                out.push(...((j.data&&j.data.recordList)||[]));
              }
              return out;
            }""", sns)
            log("领用记录 {} 条".format(len(recv)))

            avl = {r['cSN']: r.get('iAvlTimes') for r in recv}

            # 4) 聚合（不过滤状态，带 iStatus；只留李家村会员；不限年份——现金券可能是老券）
            inv = {v: k for k, v in TARGET.items()}
            rows = []
            skipped_other_store = 0
            for r in inst:
                mid = r.get('iMemberID')
                if mid not in mmap:
                    skipped_other_store += 1
                    continue
                m = mmap[mid]
                cid = r.get('iCouponID')
                g = inv.get(cid, 'unk')
                rows.append({
                    'mid': mid,
                    'name': (m.get('cRealName') or '').strip() or (m.get('cPhone') or ''),
                    'phone': m.get('cPhone') or '',
                    'emp': m.get('employeeName') or m.get('csEmployeeName') or '未分配',
                    'level': m.get('cMemberLevelName') or '',
                    'g': g,
                    'coupon': r.get('cTitle') or '',
                    'sub': r.get('cSubTitle') or '',
                    'time': (r.get('pubts') or '')[:16],
                    'sn': r.get('cSN') or '',
                    'left': avl.get(r.get('cSN'), None),
                    'status': r.get('iStatus'),
                    'raw': {k: r.get(k) for k in ('iUsedTimes', 'iTotalTimes', 'dEndDate') if r.get(k) is not None},
                })
            log("李家村会员持有 {} 条（他店 {} 条）".format(len(rows), skipped_other_store))

            out = {
                "generatedAt": time.strftime("%Y-%m-%d %H:%M"),
                "gname": {"ty": "通用现金券", "dx": "定向现金券"},
                "rows": rows,
            }
            path = os.path.join(BASE, "data", "cash_coupons.json")
            json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
            log("已存 {}".format(path))
            # 汇总打印
            by_g = defaultdict(int)
            used = 0
            for r in rows:
                by_g[r['g']] += 1
                if r['status'] != 0:
                    used += 1
            log("分布: {} ; 非正常状态 {} 条".format(dict(by_g), used))
        finally:
            b.close()


if __name__ == "__main__":
    main()
