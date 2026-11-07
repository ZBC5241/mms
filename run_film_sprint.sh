#!/bin/bash
# 会员运营工作台 · 每日 08:00 自动全量刷新（系统里唯一一个本工作台定时任务）
# 2026-11-06 11:0x 晨哥指令：时间由 22:55 改为每天早上 8 点。
# 唯一交付文件：会员管理工作台.html，四 Tab（总览/员工/客户/会员查询）。
# 🔴 2026-11-06 晨哥最高全局红线：禁止「以旧数据充新数据」。每一步真实成败写进
#    data/health.json（data_health.py），页面显式标红降级；核心券数据失败时
#    不推文件，改推文本告警说明哪块坏了，绝不拿旧缓存冒充今日更新。
# 2026-11-05 晨哥拍板「只要一个文件」：会员查询页降为 .build/ 内部中间产物，
# 本链路自动刷新它并 srcdoc 内联，顶层与群里都不再出现第二个 HTML。
# 2026-11-06 晨哥要「彻底一个任务」：原 23:50 独立任务 memberquery.refresh 的
#   会员画像全表扫描已并入本链第二段，其 plist 已归档（不再作为定时任务存在）。
#   第一段约 08:05 出文件 + 推群（全天唯一一次推送）；第二段约 08:45 用最新画像更新
#   本地文件、不再推群，避免群里出现两版。数据链路：
#   ① 消费记录增量：最近 7 天按天分片 + 每周一当月回补 + 追加/覆盖更新 → 重算「买过啥」/九类/话术
#   ② 钢化膜核销：优先复用 shopbag 22:51 落盘 raw；会员持券：经理号拉券实例 + 剩余次数 + 员工归属
#   ③ 合并渲染 → 会员管理工作台.html → srcdoc 内联 → webhook 推群（约 08:05）
#   ④ 会员画像全表扫描（用友过滤器失效只能全表扫，约 40 分钟）→ 重渲染 + 重新内联（仅本地）
# 注意：需要跳过第 ④ 段时设 SKIP_PROFILE_FETCH=1（例如只做快速重出）。
# 🔴 顺序不可换：① 必须先于 ③ 渲染。否则 build_unified 采集到的消费时点还是上一轮，
#    外层总览「数据时点」会比内层会员查询慢一天（2026-11-06 实测踩过）。
# ⚠️ 各块数据时点不同，页面已分块标注，别拿构建时间当数据时间：
#    消费/九类/话术=最近一张零售单日期（① 拉最近 7 天，周一额外回补当月，历史 2026-01-01 起全量在库）；券/会员=② 拉取时刻；
#    画像档案=上一段 ④ 扫描完成时刻（08:45 才更新本地，群里那份是上一轮缓存）；
#    钢化膜当日=昨晚 22:51 raw（08:00 时今天的 raw 还没生成，开盘本就接近 0）。
PY=/Users/mac/.local/share/TeleAgent/runtimes/python/bin/python3
DIR="/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint"
LOG="$DIR/logs"

mkdir -p "$LOG"
"$PY" "$DIR/data_health.py" reset >> "$LOG/run.log" 2>&1
echo "[$(date '+%F %T')] unified workbench start (health reset)" >> "$LOG/run.log"

WBPY=/Users/mac/.workbuddy/binaries/python/versions/3.13.12/bin/python3

# ── 第一段：出文件 + 推群（约 08:05，含消费增量约 1 分钟）──
# ── ① 消费记录增量（近 7 天 + 周一当月回补）：让九类/消费记录/回访话术每天自己变新 ──
# 按天分片只拉几十行，追加/覆盖更新到 retail_all.json 后本地全量重算；不重跑全年 57M 网络拉取。
# 每周一额外把当月整月重拉一遍，捞回 7 天前才补录/修改的老单；历史从不清空。
# 失败时保留上一版数据，不阻断出文件与推群。
# 🔴 必须在 build_unified 渲染之前跑：渲染时会采集消费数据截止日，顺序反了外层时点会旧一轮。
echo "[$(date '+%F %T')] retail delta start (recent 7 days)" >> "$LOG/run.log"
if [ "$(date +%u)" = "1" ]; then
  MONSTART="$(date +%Y-%m-01)"
  echo "[$(date '+%F %T')] monthly backfill start $MONSTART" >> "$LOG/run.log"
  if "$PY" "$DIR/pull_retail_history.py" "$MONSTART" >> "$LOG/run.log" 2>&1; then
    "$PY" "$DIR/data_health.py" mark monthly ok "当月整月回补 $MONSTART 完成"
  else
    "$PY" "$DIR/data_health.py" mark monthly partial "当月回补失败，7天前补录的老单可能未回补"
    echo "[$(date '+%F %T')] WARN monthly backfill failed" >> "$LOG/run.log"
  fi
else
  "$PY" "$DIR/data_health.py" mark monthly ok "今日非周一，无需当月回补"
fi
if "$PY" "$DIR/pull_retail_history.py" --recent 7 >> "$LOG/run.log" 2>&1; then
  "$PY" "$DIR/data_health.py" mark retail ok "最近 7 天增量拉取完成"
else
  "$PY" "$DIR/data_health.py" mark retail fail "最近 7 天增量拉取失败，消费数据沿用上一版"
  echo "[$(date '+%F %T')] WARN retail delta failed, keep previous data (page will flag it)" >> "$LOG/run.log"
fi
if "$PY" "$DIR/build_consumption.py" >> "$LOG/run.log" 2>&1; then
  "$PY" "$DIR/data_health.py" mark consumption ok "本地全量重算完成"
else
  "$PY" "$DIR/data_health.py" mark consumption fail "重算失败，消费/九类/话术沿用上一版"
  echo "[$(date '+%F %T')] WARN consumption rebuild failed (page will flag it)" >> "$LOG/run.log"
fi
echo "[$(date '+%F %T')] retail delta done" >> "$LOG/run.log"
# ── ② 券数据 + ③ 渲染主文件 ──
if $PY "$DIR/build_unified.py" --auto >> "$LOG/run.log" 2>&1; then
  "$PY" "$DIR/data_health.py" mark coupons ok "券实例 / 会员 / 剩余次数本轮真实拉取"
  "$PY" "$DIR/data_health.py" mark render ok "主页面渲染完成"
else
  "$PY" "$DIR/data_health.py" mark coupons fail "券数据拉取或主渲染失败，当前为上一轮缓存"
  "$PY" "$DIR/data_health.py" mark render fail "主渲染失败，已用缓存离线重出并标红"
  echo "[$(date '+%F %T')] FATAL core data failed -> re-render with RED warning, DO NOT push stale file" >> "$LOG/run.log"
  # 本地仍出一份带红色降级横幅的文件（如实说明），但绝不推群冒充今日更新
  "$PY" "$DIR/build_unified.py" --render-only >> "$LOG/run.log" 2>&1
  "$WBPY" "$DIR/enrich_profiles.py" --merge-v1 >> "$LOG/run.log" 2>&1
  "$WBPY" "$DIR/build_xl_member.py" >> "$LOG/run.log" 2>&1
  # 核心失败时仍发布带红条的页面，服务端 meta 标 degraded，在线版同步显示告警
  "$PY" "$DIR/publish_workbench_online.py" >> "$LOG/run.log" 2>&1
  "$PY" "$DIR/data_health.py" alert >> "$LOG/run.log" 2>&1
  echo "[$(date '+%F %T')] finish (no push, alert sent)" >> "$LOG/run.log"
  exit 1
fi
echo "[$(date '+%F %T')] unified workbench done" >> "$LOG/run.log"
# 用现有画像缓存重渲染内部中间产物（纯本地，秒级；此时消费数据已是最新的）
"$WBPY" "$DIR/enrich_profiles.py" --merge-v1 >> "$LOG/run.log" 2>&1
echo "[$(date '+%F %T')] member query source refreshed (internal only)" >> "$LOG/run.log"
# 渲染后立刻内联进本页（幂等；避免 build_unified 覆盖后会员查询 tab 空白）
"$WBPY" "$DIR/build_xl_member.py" >> "$LOG/run.log" 2>&1
echo "[$(date '+%F %T')] member query inlined" >> "$LOG/run.log"
# 在线版：把同一份成品发布到售票厅本机数据目录（验票后才下发，不进任何公开仓库）
"$PY" "$DIR/publish_workbench_online.py" >> "$LOG/run.log" 2>&1 || echo "[$(date '+%F %T')] WARN online publish failed (page will flag it)" >> "$LOG/run.log"
echo "[$(date '+%F %T')] online workbench published" >> "$LOG/run.log"
# 每日构建后推送企业微信群（群机器人 Webhook 直推，仅用晨哥指定地址；失败安全跳过）
# 🔴 全天只推这一次、只推这一个文件；第二段更新本地文件后不再推群
CORE_BAD="$("$PY" "$DIR/data_health.py" bad coupons)"
if [ -n "$CORE_BAD" ]; then
  echo "[$(date '+%F %T')] FATAL coupons not ok ($CORE_BAD), skip push" >> "$LOG/run.log"
  "$PY" "$DIR/data_health.py" alert >> "$LOG/run.log" 2>&1
else
  if bash "$DIR/send_workbench_wecom.sh" >> "$LOG/run.log" 2>&1; then
    "$PY" "$DIR/data_health.py" mark wecom ok "主文件已推送"
  else
    "$PY" "$DIR/data_health.py" mark wecom fail "群推送失败（上传或发送未成功），文件仍已更新到本地"
    echo "[$(date '+%F %T')] WARN wecom push failed" >> "$LOG/run.log"
  fi
fi
echo "[$(date '+%F %T')] unified workbench + wecom step finish" >> "$LOG/run.log"

# ── 第二段：会员画像全表扫描（原 23:50 独立任务，2026-11-06 并入本链）──
# 用友会员过滤器失效，只能全表扫描，约 40 分钟；只更新本地文件，次日 08:05 推送自然带上
if [ "${SKIP_PROFILE_FETCH:-0}" != "1" ]; then
  if "$WBPY" "$DIR/enrich_profiles.py" --fetch-only >> "$LOG/run.log" 2>&1; then
    "$PY" "$DIR/data_health.py" mark profiles ok "画像全表扫描完成"
  else
    "$PY" "$DIR/data_health.py" mark profiles fail "画像扫描失败，档案沿用上一轮"
    echo "[$(date '+%F %T')] WARN profile fetch failed (page will flag it)" >> "$LOG/run.log"
  fi
  echo "[$(date '+%F %T')] member profiles refreshed" >> "$LOG/run.log"
  "$WBPY" "$DIR/enrich_profiles.py" --merge-v1 >> "$LOG/run.log" 2>&1
  "$WBPY" "$DIR/build_xl_member.py" >> "$LOG/run.log" 2>&1
  # 最新画像同步给在线版（只改本机服务端数据，不推群、不推文件）
  "$PY" "$DIR/publish_workbench_online.py" >> "$LOG/run.log" 2>&1 || echo "[$(date '+%F %T')] WARN online republish failed" >> "$LOG/run.log"
  echo "[$(date '+%F %T')] workbench refreshed with latest profiles (local only, no push)" >> "$LOG/run.log"
fi
