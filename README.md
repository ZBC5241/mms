# 会员管理工作台（mms）

李家村门店会员管理工作台构建源码。源模板 + 构建脚本，**不含任何真实会员数据**。

## 红线
- `data/` 目录存放真实会员 PII（姓名/电话/消费/券），**已 gitignore，绝不入库、绝不推公开仓库**。
- 成品 `会员管理工作台.html`（6.8MB 内嵌会员明细）只在本机生成与分发，绝不上公开 GitHub / 静态网址。

## 构建链路（每日 08:00 由 launchd `com.lijiacun.dashboard.filmsprint` 触发）
1. `pull_retail_history.py` — 拉用友零售/会员数据
2. `build_unified.py` — 聚合生成统一缓存
3. `enrich_profiles.py` — 补全会员画像
4. `build_xl_member.py` — 渲染外层单文件 HTML（`template_unified.html`）
5. `publish_workbench_online.py` — 拷贝到 storyos 售票厅 `server/data/workbench.html`，供在线版 `/workbench` 验票下发

## 在线访问
- 隧道（与 storyos 同一条）：`https://lijiacun-board.serveousercontent.com/mms` — 仅一行状态字，不放数据
- 工作台在线版：`https://lijiacun-board.serveousercontent.com/workbench`（登录后由本机服务下发，同源复用 storyos 登录页）
