#!/bin/bash
# 会员运营工作台 · 每日构建后推送企业微信群
# 方式：群机器人 Webhook 直推（upload_media -> webhook/send）
# 仅使用晨哥指定的 Webhook 地址，不依赖 aibot「消息」权限
# 失败/未配置 -> 安全跳过，不阻断调用方每日构建
# 2026-11-05 晨哥拍板「只要一个文件」：群里只推下面这一个主工作台 HTML。
# 会员查询页已是 .build/ 内部中间产物（srcdoc 内联在本页），禁止再加第二个推送。
set -u
# 失败必须返回非零：调用方（run_film_sprint.sh）据此判断群推送是否真的成功，
# 不能上传/发送失败还 exit 0 让主链记「已推送」（审计 2026-11-06 修复）
DIR="/Users/mac/.local/share/TeleAgent/TeleAgent的工作空间/film_sprint"
LOG="$DIR/logs"
FILE="${1:-/Users/mac/WorkBuddy/2026-09-11-21-21-56/会员管理工作台.html}"
WEBHOOK_KEY="fe438f09-4124-47b9-9ff8-4a72783d55a0"
UPLOAD_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key=${WEBHOOK_KEY}&type=file"
SEND_URL="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${WEBHOOK_KEY}"
TS="$(date '+%F %T')"
mkdir -p "$LOG"
echo "[$TS] wecom webhook send start" >> "$LOG/run.log"

[ -f "$FILE" ] || { echo "[$TS] wecom send FAIL: 文件不存在 $FILE" >> "$LOG/run.log"; exit 1; }

# 1) 上传文件 -> media_id
UP="$(curl -s --max-time 60 -F "media=@${FILE};filename=$(basename "$FILE");type=text/html" "$UPLOAD_URL")"
MEDIA_ID="$(printf '%s' "$UP" | sed -n 's/.*"media_id"[ ]*:[ ]*"\([^"]*\)".*/\1/p')"
if [ -z "$MEDIA_ID" ]; then
  echo "[$TS] wecom send FAIL: 上传失败 -> $UP" >> "$LOG/run.log"; exit 1
fi

# 2) 发送文件到群
RESP="$(curl -s --max-time 30 -H "Content-Type: application/json" \
  -d "{\"msgtype\":\"file\",\"file\":{\"media_id\":\"$MEDIA_ID\"}}" "$SEND_URL")"
ERR="$(printf '%s' "$RESP" | sed -n 's/.*"errcode"[ ]*:[ ]*\([0-9]*\).*/\1/p')"
if [ "$ERR" = "0" ]; then
  echo "[$TS] wecom send done (file: $(basename "$FILE"))" >> "$LOG/run.log"
else
  echo "[$TS] wecom send FAIL: 群发送失败 -> $RESP" >> "$LOG/run.log"; exit 1
fi
