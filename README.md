# 浏览器/搜索入口高热度词条采集（hotwords）

按源配置声明式组织的热榜采集骨架。**V3**：
5 个源（百度 4 榜 + B站 + 微博 + 抖音 + 头条）、**跨源共现**、
**持续上榜追踪**、**趋势看板（含搜索筛选）**，实测 **280 条/轮、status=ok**。

参考 Scrapling 的能力边界设计，但**只用它的 `Fetcher`**（TLS 指纹 + 统一 API），
不引入 StealthyFetcher / adaptive —— 本项目的源几乎全是 JSON 接口，
反爬对抗与 DOM 自适应都用不上（详见"选型说明"）。

![趋势看板](docs/dashboard.png)

---

## 快速开始

```bash
# 1. 安装依赖（建议先建虚拟环境）
pip install -r requirements.txt

# 2. 常用命令
python -m hotwords sources    # 列出已配置的源
python -m hotwords once       # 跑一轮采集（自动更新持续追踪）
python -m hotwords track      # 持续上榜榜（"真热度"核心）
python -m hotwords cross      # 跨源共现榜（默认聚合全部轮次）
python -m hotwords trend      # 生成趋势看板 dashboard.html（默认全量）
python -m hotwords report     # 数据概览
python -m hotwords run        # 常驻调度：每 N 分钟跑一轮全量
python -m hotwords retrack    # 从快照全量重建持续上榜数据
python -m hotwords alerts     # 查看告警

python tests/test_basic.py    # 11/11 通过
```

`cross` / `trend` 支持 `--last N` 只看最近 N 轮（默认 `0` = 全部）。

Windows 下双击 `run.bat` 会出现交互菜单（选 1~6），
也支持 `run.bat once / track / cross / trend / report / retrack / test`。
选 6 挂机会同时把日志追加写入 `logs/scheduler.log`，便于次日回溯。

`run.bat` 按以下优先级挑解释器
（挑错会报 `ModuleNotFoundError: No module named 'yaml'`）：

```
%HOTWORDS_PY%  →  <项目>\.venv\Scripts\python.exe  →  <项目>\venv\...  →  PATH 上的 python
```

**推荐在项目里建 `.venv`**：依赖隔离、且双击即可用，不必记路径。
跑完菜单会打印实际使用的解释器，便于排查。

更详细的使用说明、典型场景与排查手册见 **[docs/USAGE.md](docs/USAGE.md)**。

---

## 三个"热度"视角（别混淆）

| 视角 | 问题 | 方法 | 抗噪性 |
|---|---|---|---|
| **单源排名** | 这个词在这个平台排第几 | 直接读 rank | ❌ 平台各自为政 |
| **跨源共现** | 多个平台是否同时上榜 | 源内分位 × `1+log2(源数)` | ⚠️ 单轮可能是巧合 |
| **持续上榜** ⭐ | 是否**连续多轮**都在榜 | `persistence_score` | ✅ **最强信号** |

**为什么持续度最强**：某词"刚好两源同时出现"可能是一次巧合，
但**连续 5 轮都在榜**几乎不可能是噪声。

`persistence_score`（0~150）权重设计：
连续轮数 35% + 历史最长连续 20%（抗短期中断）+ 上榜占比 20%
+ 最佳名次 15% + 跨源广度 10%。

`track_persistence()` 在每轮采集后**增量更新**（不全表重算）：
- 本轮上榜 → `total_rounds+1`，若上轮也在榜则 `streak+1`
- 本轮未上榜 → `streak` 归零（**断档检测**）
- 与上轮间隔 > `MAX_GAP_MINUTES`（默认 180 分钟）→ 视为**中断**，`streak` 重置
  （否则"停一晚再挂"会被误算成连续，见坑 14）

历史数据可用 `python -m hotwords retrack` 从快照全量重放重建。

### ⚠️ 运营推广位必须排除（`track_exclude`）

**持续度算法有个天然缺陷**：它的两个高权重项（连续轮数 35%、最佳名次 15%）
恰好是**平台运营位最强的项** —— 钉子榜永远在榜、永远排第一。

实测（2026-10-09~10，42 轮 / 6.5 小时）：

| 榜单 | #1 保持时长 |
|---|---|
| 百度 `movie` | 「功夫女足」**39/39 轮**纹丝不动 |
| 百度 `novel` | 「绍宋」**39/39 轮** |
| 百度 `teleplay` | 「九门」**39/39 轮** |
| 百度 `realtime` | 只保持 **20/39 轮**（真实热搜换得勤） |

不排除的后果：这些运营位会以接近满分的持续度**霸占榜单前十几位**，
把真实跨源热点（如台湾 3 源共现的「林诗栋退出亚锦赛男单混双」）挤出榜单。

**解决**：在 `sources.yaml` 里给源加 `track_exclude` 标记：

```yaml
- id: baidu_hot
  tabs: [realtime, teleplay, movie, novel]
  track_exclude: [teleplay, movie, novel]   # 不参与持续上榜统计
```

注意：**数据仍照常采集入库**，只是不参与 `word_track` 统计 ——
快照层保留完整证据，分析层不被污染。改完记得 `retrack` 重建：

```
python -m hotwords retrack     # 会打印本次排除的榜单
```

---

## 趋势看板（`dashboard.html`）

自包含 HTML（内联 CSS/JS，无 CDN，**离线可用**），暗色主题：

| 视图 | 内容 |
|---|---|
| **持续霸榜 Top** | 连续轮数条形，绿≥3轮 / 黄=2轮 / 蓝=1轮 |
| 跨源共现榜 | 绿=多源共现 / 蓝=单源 |
| 排名走势 | Top 词条各轮最佳名次折线 |
| 各源采集量 | 堆叠柱，健康度监控 |

顶部工具栏：**词条搜索框**（实时过滤）+ 「只看跨源共现」/「只看连续上榜」勾选。

默认聚合**全部轮次**（`trend --last N` 可改为只看最近 N 轮；`--last 0` = 全量）。
> 早期版本固定只看最近 20 轮，长跑后顶部"累计词条"会远小于实际
> （实测 42 轮的库显示 531，而全库 932），属于误导性数字，已改为默认全量。

---

## 架构

```
调度层   scheduler.py    APScheduler；**整轮全量**、max_instances=1 禁止并发
                         （"每源一个 job"会毁掉 streak，见坑 13）
   ↓
采集层   collector.py    Fetcher 优先级：scrapling > curl_cffi > httpx > requests
                         指数退避重试；403/401 不重试
   ↓
解析层   parsers/        每源一个 Parser。只做 "原始 -> HotItem"，不发请求
   ↓
断言层   assertions.py   结构哨兵：条目数 / 必填字段 / 空词条
   ↓
分析层   analysis.py     跨源共现 + 持续上榜追踪（增量更新）
   ↓
存储层   storage/        snapshot + word + word_track + alert + 指纹（6 张表）
   ↓
展示层   render.py       趋势看板 HTML
   ↓
告警层   notify.py       解析失效 / 字段漂移 -> 日志 / alert 表
```

**Fetcher 档位**（auto 优先级）：
- `scrapling` — 完整框架，TLS 指纹 + 浏览器能力（依赖重）
- `curl_cffi` — **轻量 TLS 指纹，仅几 MB，无浏览器**（大多数场景够用）
- `httpx` / `requests` — 无指纹伪装，纯兜底

**关键解耦**：Parser 不碰网络，Collector 不碰解析。
→ 将来把 `static_json` 换成浏览器渲染，解析逻辑一行不用改。

---

## 源配置（`hotwords/config/sources.yaml`）

新增一个源 = **改配置 + 注册 Parser**，主流程零改动。

| 源 | 状态 | 实测条目 | 备注 |
|---|---|---|---|
| `baidu_hot` | ✓ | 101（4 榜） | realtime 无热度值 |
| `bilibili_hot` | ✓ | 30 | 最稳最快，0.2s |
| `weibo_hot` | ✓ | 50 | 需 Referer + 手机 UA |
| `douyin_hot` | ✓ | 50 | — |
| `toutiao_hot` | ✓ | 50 | 字段首字母大写 |
| `zhihu_hot` | ✗ | — | 401 需登录态 |

---

## 实测踩坑记录（2026-10-09 / 10-10）

这一节是最有产出的部分 —— 都是真机跑出来的，不是推测。

### 坑 1 · 百度接口多套一层 `content`
真实路径 `data.cards[0].content[0].content[]`，
**直接取 `data.cards[0].content` 会拿到 0 条**。

### 坑 2 · 同接口不同榜单，字段名不同 ⭐
| 榜单 | 字段 |
|---|---|
| `realtime` | `word` |
| `teleplay`/`movie`/`novel` | `title` |

同一 URL、同一层级，字段名居然不一样。**这是 adaptive 完全救不了的**
（它只处理 DOM 结构漂移，不处理 JSON 字段改名）。
解法：Parser 字段兼容 + 断言 `required_fields_any`。

### 坑 3 · 百度热度值是 dict 不是数字
`hotScore = {"type":0,"value":"95471","desc":"热搜指数"}`，直接 `int()` 丢数据。

### 坑 4 · 百度 `realtime` 榜压根没有热度值
只有 `word`/`url`/`isTop`。**这不是 bug，是数据事实**。

### 坑 5 · `normalize_hot_value` 对 dict 静默产错值
`str({'value':'1'})` 里的 `1` 被正则匹配 → 返回 1。
加类型防护 + 改 `fullmatch`。**靠自写测试才发现**。

### 坑 6 · 漂移检测误报
百度各榜字段天然不同，按"整个源"记指纹必然全报漂移。
改成按 `source/category` 维度记录。

### 坑 7 · 百度接口偶发超时返回空
手动测成功、批量跑却空 —— 网络抖动，**重试机制的价值所在**。

### 坑 8 · 头条字段名首字母大写，断言也要对上
`Title`/`HotValue`/`Url`。断言查的是**原始 raw 字典**，配置必须写
`required_fields: [Title]`。另有 `fixed_top_data` 置顶、`Label` 英文枚举需映射。

### 坑 9 ⭐ · UNIQUE 约束导致**静默数据丢失**（最严重）
原约束 `UNIQUE(source, category, rank, fetched_at)` ——
**同一秒内跑两轮，第二轮数据被 `INSERT OR IGNORE` 静默吞掉**，
表现为"词条 streak 永远涨不上去"。
→ 改为 `UNIQUE(run_id, source, category, rank, word)`。
**`run_id` 才是"哪一次采集"的权威标识，不能用时间戳代替。**

### 坑 10 · `ORDER BY started_at` 排序退化
同一秒内多轮时时间戳相同，`prev_run` 会选错轮次 → streak 断档判断失效。
→ 统一改用 `rowid`（插入顺序），不依赖时间戳。

### 坑 11 · `list - set` TypeError
`sorted(...)` 返回 list，却直接做 set 差集运算。必须先差集再排序。

### 坑 12 ⭐ · 挂机时 SQLite 跨线程 + 异常被吞成"成功"（2026-10-10）
APScheduler 在**线程池**里执行 job，而 SQLite 连接建在主线程 ——
默认 `check_same_thread=True` 时第一轮就抛 `ProgrammingError`。

**真正阴的是表现形式**：异常被 job 内的 `try/except` 吃掉后，
APScheduler 照样打出 `Job executed successfully`，而数据库一轮都没涨。
**"库不涨、日志说成功"，不实测根本发现不了。**

→ 两处修正：`sqlite3.connect(..., check_same_thread=False)`
（安全性依据：单 job、`max_instances=1`，同一时刻只有一个线程在写）；
job 内**不捕获异常**，交给 APScheduler 记录 traceback 并继续调度。

### 坑 13 ⭐ · "每源一个 job"会毁掉持续上榜数据（2026-10-10）
原设计每个源各自建一个 run。但 `track_persistence(run_id)` 的语义是
"用**这一轮 run 的全部快照**比对上一轮，本轮没出现的词判为断档"。

于是：run A 只有百度 → 微博/抖音/头条的词全被清 `streak`；
下一轮 run B 只有 B站 → 百度的词又被清。**跑几轮数据就废了。**

而且每源独立 job 时根本**没有"轮"的边界**，`track_persistence` 无处触发 ——
挂一夜，`word_track` 一轮都不会更新。

→ 调度改为**整轮全量**：一个 job、一个 run、覆盖全部源。
整轮间隔取各源 `interval_min` 的**最大值**（默认 10 分钟），
即不对任何源加速（微博/抖音/头条当初就是为合规主动降频到 10min 的）；
需要更快用 `--interval N` 覆盖。

### 坑 14 ⭐ · 跨天被误判为"连续"（2026-10-10）
`track_persistence` 只按 `rowid` 找上一轮，**不看时间间隔**。
实测两轮相隔 **19 小时**仍被当作相邻轮次，`streak` 从 6 涨到 7 ——
而"白天关机上课、晚上再挂"正是常态，这会让挂机产出的核心指标
（连续上榜）整体虚高。

→ 引入 `MAX_GAP_MINUTES`（默认 180）：相邻两轮间隔超过该值即视为中断，
`streak` 重置；时间戳缺失/无法解析时**按中断处理**（宁可重置，
也不让坏时间戳撑出虚高记录）。

配套 `python -m hotwords retrack`：从 snapshot 全量重放重建 `word_track`，
用于修正历史脏数据或调整算法后重算。

### 坑 15 ⭐ · 平台运营位污染持续上榜榜（2026-10-10）
`persistence_score` 的两个高权重项（连续 35% + 最佳名次 15%）
恰好是运营位最强的项 —— 钉子榜永远在榜、永远第 1。

实测：百度 `movie`/`novel`/`teleplay` 三个垂类榜的 #1 在 **39/39 轮**
纹丝不动（功夫女足 / 绍宋 / 九门），而 `realtime` 榜 #1 只保持 20/39 轮。
结果 **Top20 里 15 个是运营位**，占据了 #1~#18，真实跨源热点被挤出榜单。

→ 引入 `track_exclude`（源配置里的 tab 黑名单），只排除**统计**不排除**采集**：
```
baidu_hot: track_exclude: [teleplay, movie, novel]
```
重建后榜单立刻恢复信息量（跨 2~3 源的真实热点排到前面）。
`track_persistence` / `rebuild_track` 新增 `exclude` 参数，
`pipeline` 自动从配置注入，`retrack` 会打印本次排除项。

### 坑 16 · 看板只统计最近 20 轮（2026-10-10）
`render_dashboard(last_n=20)` 与 `load_snapshot_over_runs(last_n)` 默认
只取最近 N 轮，长跑后看板顶部"累计词条"远小于实际
（42 轮的库显示 **531**，全库 **932**），属于误导性数字。

→ `last_n <= 0` 语义改为**全量**，并把 `trend`/`cross` 的默认值改成 0。
现在看板 KPI 为 929 词条 / 26 条跨源共现 / 11,810 行 / 42 轮，
与数据库逐项对得上。

---

## 选型说明（为什么只用 Scrapling 的 Fetcher）

| Scrapling 能力 | 本项目用不用 | 原因 |
|---|---|---|
| `Fetcher`（TLS 指纹） | ✅ 用 | 微博要的 Referer+UA 它天生支持 |
| `FetcherSession` | ✅ 可扩展 | 会话保持 |
| `DynamicFetcher` | ⚠️ 备用 | 仅知乎等 JS 站可能需要 |
| `StealthyFetcher` | ❌ 不用 | 现有源**没有一个**上 Cloudflare |
| **adaptive 自适应** | ❌ 不用 | 源是 JSON 接口，失效表现为"字段改名/接口换掉"，adaptive 无效 |

**真正防失效的是**：结构断言 + 字段漂移告警 + 配置化 Parser。
坑 2 是最直接的实证 —— adaptive 对"同接口不同榜字段不同"毫无办法。

> 本机现状：已装 `curl_cffi`（轻量 TLS 指纹），未装 scrapling。
> 当前实际使用 `curl_cffi`，微博实测 200 / 50 条。

---

## 合规留档

| 源 | robots 探测（2026-10-09） |
|---|---|
| 百度 | `top.baidu.com/robots.txt` **404**；`www.baidu.com/robots.txt` 只约束 `/baidu`、`/s?`，**未覆盖本接口** |
| B站 / 微博 / 抖音 / 头条 | 公开接口 |

红线：不绕技术措施、不高频并发、不商用替代原站。
频率设为单源 5–10 分钟、串行、全局 1–3 QPS。**不引入代理池。**

---

## 下一步（V4）

1. 告警外发到 Agent Mail（`notify.make_mail_notifier` 已留口子）
2. 词条分类打标（先评估用途，别急着上模型）
3. 持续上榜的**趋势预警**：新词突入 Top / 老词掉榜提醒


