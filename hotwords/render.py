"""趋势可视化：生成自包含 HTML（内联 CSS/JS，无外部依赖）。

设计：
  - 暗色主题（适配用户 IDE dark theme）
  - 三个视图：
      1) 跨源共现榜（横向条形，热度指数）
      2) 词条排名走势（多轮折线，Top N 词条）
      3) 源健康度（各源每轮条目数）
  - 纯手写 SVG + 少量原生 JS，不引 CDN（离线可用）
"""
from __future__ import annotations

import html
import json
from collections import OrderedDict, defaultdict
from pathlib import Path

from hotwords.analysis import compute_cross_source, load_snapshot_over_runs

# 源配色（暗色下可读）
SOURCE_COLORS = {
    "baidu_hot": "#4e8cff",
    "bilibili_hot": "#fb7299",
    "weibo_hot": "#ff8200",
    "douyin_hot": "#25f4ee",
    "toutiao_hot": "#f04142",
    "zhihu_hot": "#0084ff",
}

SOURCE_NAMES = {
    "baidu_hot": "百度",
    "bilibili_hot": "B站",
    "weibo_hot": "微博",
    "douyin_hot": "抖音",
    "toutiao_hot": "头条",
    "zhihu_hot": "知乎",
}


def _q1(v: float) -> str:
    return f"{v:.1f}"


def render_dashboard(repo, out_path: str = "dashboard.html", last_n: int = 0) -> str:
    """生成看板。

    last_n <= 0（默认）= 聚合全部轮次。
    早期默认只看最近 20 轮，长跑后看板顶部的"累计词条"远小于实际
    （实测 42 轮的库显示 531，而全库是 932），容易误读。
    """
    rows = load_snapshot_over_runs(repo, last_n)

    # ---- 跨源共现榜 ----
    words = compute_cross_source(rows)
    top_cross = words[:15]

    # ---- 逐轮趋势 ----
    # last_n <= 0 时取全部轮次；否则只取最近 N 轮
    _limit_sql = "LIMIT ?" if (last_n and last_n > 0) else ""
    _limit_p = (last_n,) if (last_n and last_n > 0) else ()
    runs = repo.conn.execute(
        "SELECT run_id, started_at, status FROM run WHERE status IN ('ok','partial') "
        f"ORDER BY started_at ASC {_limit_sql}",
        _limit_p,
    ).fetchall()
    run_labels = [r["started_at"][11:19] for r in runs]
    run_ids = [r["run_id"] for r in runs]
    # 若整体时间跨度过小（如测试期密集跑，或日常高频采集），
    # 时间标签会挤成一团看不出趋势 —— 改用轮次序号。
    if len(run_ids) > 1:
        first, last = runs[0]["started_at"], runs[-1]["started_at"]
        try:
            from datetime import datetime as _dt
            span_min = (_dt.fromisoformat(last) - _dt.fromisoformat(first)).total_seconds() / 60
        except (ValueError, TypeError):
            span_min = 999
        # 平均每轮间隔不足 3 分钟，说明时间戳区分度不够
        if span_min / max(len(run_ids) - 1, 1) < 3 or len(run_ids) > 12:
            run_labels = [f"#{i+1}" for i in range(len(run_ids))]

    # 每轮每源条目数
    timeline: dict[str, list[int]] = defaultdict(lambda: [0] * len(run_ids))
    idx_of = {rid: i for i, rid in enumerate(run_ids)}
    for r in repo.conn.execute(
        "SELECT run_id, source, COUNT(*) n FROM snapshot GROUP BY run_id, source"
    ).fetchall():
        i = idx_of.get(r["run_id"])
        if i is not None:
            timeline[r["source"]][i] += r["n"]

    # 排名走势：取跨源 top 词条在各轮的最佳名次
    trend_words = [w.word for w in top_cross[:8]]
    trend_series: OrderedDict[str, list[int | None]] = OrderedDict()
    for w in trend_words:
        series: list[int | None] = [None] * len(run_ids)
        for rid, i in idx_of.items():
            row = repo.conn.execute(
                "SELECT MIN(rank) r FROM snapshot WHERE run_id=? AND word=? AND rank>0",
                (rid, w),
            ).fetchone()
            if row and row["r"] is not None:
                series[i] = row["r"]
        trend_series[w] = series

    # ---- 持续上榜榜 ----
    from hotwords.analysis import load_persistence

    persist = load_persistence(repo, limit=15, min_streak=1)
    persist_data = [p.to_dict() for p in persist]

    # ---- 位置 / 性质标记词条（广告位、置顶、活动话题）----
    # 这些词条在持续度榜上排名往往靠后（广告位是平台轮播的，streak 短），
    # 单看 Top15 会漏掉，所以单列一张表：它们的证据价值在于"性质"而非"热度"。
    marked = repo.conn.execute(
        """SELECT canonical_word, sources, total_rounds, current_streak,
                  max_streak, is_ad, is_top
           FROM word_track WHERE is_ad=1 OR is_top=1
           ORDER BY (is_ad=1) DESC, total_rounds DESC, max_streak DESC LIMIT 30"""
    ).fetchall()
    marked_data = [dict(r) for r in marked]

    # 活动话题（抖音「挑战」）单独从 snapshot 取：它们【已被排除出 word_track】，
    # 在持续度榜里根本不存在，只能回快照层找。这也正是"只排除统计、不排除采集"
    # 的价值 —— 统计层干净了，证据层仍然完整。
    from hotwords.labels import DOUYIN_TOPIC_BADGES

    if DOUYIN_TOPIC_BADGES:
        ids = ",".join(str(i) for i in sorted(DOUYIN_TOPIC_BADGES))
        topic_rows = repo.conn.execute(
            f"""SELECT word AS canonical_word,
                       GROUP_CONCAT(DISTINCT source) AS sources,
                       COUNT(DISTINCT run_id) AS total_rounds,
                       MAX(label) AS label
                FROM snapshot WHERE label_id IN ({ids})
                GROUP BY word ORDER BY total_rounds DESC LIMIT 12"""
        ).fetchall()
        for r in topic_rows:
            d = dict(r)
            d.update({"is_ad": 0, "is_top": 0, "current_streak": 0,
                      "max_streak": 0, "is_topic": 1})
            marked_data.append(d)

    # ---- 统计卡 ----
    total_rows = len(rows)
    multi = sum(1 for w in words if w.cross_source_count >= 2)
    active_sources = len({r["source"] for r in rows})
    total_runs = repo.conn.execute(
        "SELECT COUNT(*) c FROM run WHERE status IN ('ok','partial')"
    ).fetchone()["c"]
    max_streak = max((p.current_streak for p in persist), default=0)
    ad_words = sum(1 for w in words if w.is_ad)

    payload = {
        "cross": [w.to_dict() for w in top_cross],
        "persist": persist_data,
        "marked": marked_data,
        "runLabels": run_labels,
        "timeline": {k: v for k, v in timeline.items()},
        "trendSeries": trend_series,
        "sourceColors": SOURCE_COLORS,
        "sourceNames": SOURCE_NAMES,
        "stats": {
            "words": len(words),
            "multi": multi,
            "sources": active_sources,
            "rows": total_rows,
            "runs": total_runs,
            "maxStreak": max_streak,
            "ads": ad_words,
        },
    }

    doc = _HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    out = Path(out_path)
    out.write_text(doc, encoding="utf-8")
    return str(out.resolve())


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>高热度词条 · 趋势看板</title>
<style>
  :root{
    --bg:#0d1117; --panel:#161b22; --panel2:#1c2333; --border:#2d3748;
    --text:#e6edf3; --dim:#8b949e; --accent:#58a6ff; --warn:#f0883e;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
  h1{font-size:20px;font-weight:600;margin-bottom:4px}
  .sub{color:var(--dim);font-size:12px;margin-bottom:20px}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}
  .stat{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
  .stat .v{font-size:26px;font-weight:700;color:var(--accent);line-height:1.2}
  .stat .k{color:var(--dim);font-size:12px;margin-top:2px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  @media(max-width:980px){.grid{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;overflow:hidden}
  .card h2{font-size:15px;font-weight:600;margin-bottom:14px;color:var(--text)}
  .card h2 span{color:var(--dim);font-weight:400;font-size:12px;margin-left:6px}
  .bar-row{display:flex;align-items:center;gap:10px;margin-bottom:9px}
  .bar-label{width:240px;font-size:13px;display:flex;align-items:center;gap:4px;flex-shrink:0;min-width:0}
  .bar-label .w{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
  .bar-track{flex:1;background:#21262d;border-radius:4px;height:20px;position:relative;min-width:60px}
  .bar-fill{height:100%;border-radius:4px;transition:width .6s ease}
  .bar-val{width:64px;text-align:right;font-size:12px;color:var(--dim);flex-shrink:0}
  .tags{display:flex;gap:4px;width:56px;flex-shrink:0}
  .tag{font-size:10px;padding:1px 5px;border-radius:3px;background:#21262d;color:var(--dim)}
  .lbl{display:inline-block;font-size:10px;padding:0 4px;border-radius:3px;
    background:#21262d;color:var(--dim);vertical-align:middle;flex-shrink:0}
  .lbl-置顶{background:#5a3a1a;color:#f0b86e}
  .lbl-广告{background:#5a2a1a;color:#ffa657}
  .lbl-热{background:#5a1a1a;color:#ff7b72}
  .lbl-新{background:#1a3a5a;color:#79c0ff}
  .lbl-爆{background:#5a1a3a;color:#ff7bd0}
  .lbl-沸{background:#5a1a2a;color:#ff9bb0}
  .lbl-辟谣{background:#1a4a3a;color:#7ee2b8}
  .lbl-热议{background:#2a2a4a;color:#b0a8ff}
  /* 抖音徽标（枚举语义见 labels.py）：挑战是活动话题，配色刻意与真实热点区分 */
  .lbl-首发{background:#4a2a1a;color:#ffb87b}
  .lbl-独家{background:#3a1a4a;color:#d0a0ff}
  .lbl-挑战{background:#1a4a2a;color:#7ee29b}
  .lbl-剧集{background:#2a3a4a;color:#9fc4e8}
  .cat{display:inline-block;font-size:10px;padding:0 4px;border-radius:3px;
    background:#21262d;color:var(--dim);vertical-align:middle;flex-shrink:0}
  svg{display:block;width:100%;height:auto;overflow:visible}
  .axis{stroke:#30363d;stroke-width:1}
  .axis-text{fill:var(--dim);font-size:11px}
  .legend{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px;font-size:12px;color:var(--dim)}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
  .empty{color:var(--dim);text-align:center;padding:30px 0;font-size:13px}
  .toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
  .search{flex:1;min-width:200px;max-width:340px;background:var(--panel);border:1px solid var(--border);
    color:var(--text);border-radius:8px;padding:8px 12px;font-size:13px;outline:none}
  .search:focus{border-color:var(--accent)}
  .chk{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--dim);cursor:pointer;user-select:none}
  .chk input{accent-color:var(--accent);cursor:pointer}
  .streak-badge{display:inline-block;min-width:34px;text-align:center;font-size:11px;font-weight:600;
    padding:2px 6px;border-radius:4px;background:#1f6feb33;color:#79c0ff}
  .rank-move{font-size:11px;margin-left:4px}
  .up{color:#3fb950} .down{color:#ff7b72}
</style>
</head>
<body>
<h1>浏览器 / 搜索入口 · 高热度词条趋势看板</h1>
<div class="sub" id="sub"></div>

<div class="stats" id="stats"></div>

<div class="toolbar">
  <input class="search" id="q" type="search" placeholder="筛选词条…（输入即过滤）">
  <label class="chk"><input type="checkbox" id="onlyMulti"> 只看跨源共现</label>
  <label class="chk"><input type="checkbox" id="onlyStreak"> 只看连续上榜</label>
</div>

<div class="grid">
  <div class="card" style="grid-column:1/-1">
    <h2>持续霸榜 Top<span>条形宽度 = 上榜占比（连续轮数并列时靠它区分）；右侧数字为连续轮数</span></h2>
    <div id="persist"></div>
  </div>

  <div class="card" style="grid-column:1/-1">
    <h2>跨源共现榜<span>热度指数 = 源内分位 × 跨源权重</span></h2>
    <div id="cross"></div>
  </div>

  <div class="card" style="grid-column:1/-1">
    <h2>位置与性质标记<span>广告位 / 置顶 / 活动话题，取自平台自身标记，非算法推断</span></h2>
    <div id="marked"></div>
  </div>

  <div class="card">
    <h2>排名走势<span>Top 词条在各轮的最佳名次（y 轴自适应范围）</span></h2>
    <div id="trend"></div>
  </div>

  <div class="card">
    <h2>各源每轮采集量<span>健康度监控</span></h2>
    <div id="health"></div>
  </div>
</div>

<script>
const D = __PAYLOAD__;

document.getElementById('sub').textContent =
  '数据轮次 ' + D.stats.runs + ' · 快照条目 ' + D.stats.rows +
  ' · 生成时间 ' + new Date().toLocaleString('zh-CN');

// 统计卡
const stats = [
  ['词条去重', D.stats.words],
  ['跨源共现', D.stats.multi],
  ['广告位', D.stats.ads],
  ['最长连续', D.stats.maxStreak + '轮'],
  ['数据轮次', D.stats.runs],
  ['活跃源', D.stats.sources],
];
document.getElementById('stats').innerHTML = stats.map(([k,v]) =>
  `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

// —— 检索状态 ——
const state = { q:'', onlyMulti:false, onlyStreak:false };

// —— 标记徽章 ——
// 广告位 / 置顶是"结构性事实"（库里独立成列），平台标签与内容分类是"展示文案"。
// 前者用高饱和底色突出，后者用低对比度，避免抢主线信息的注意力。
function badge(t, cls){ return `<span class="lbl ${cls}">${t}</span>`; }
function markPersist(p){
  let h='';
  if(p.is_ad)  h += badge('广告', 'lbl-广告');
  if(p.is_top) h += badge('置顶', 'lbl-置顶');
  // 平台徽标（新/热/首发/独家/剧集/辟谣/热议）：取该词累计出现过的第一个，
  // 置顶/广告已单独成列，不在这里重复显示
  const lb = (p.labels||[]).filter(x => x && x !== '置顶' && x !== '广告')[0];
  if(lb) h += badge(lb, 'lbl-' + lb);
  return h;
}
function markCross(w){
  let h='';
  if(w.is_ad) h += badge('广告', 'lbl-广告');
  const lb = (w.labels||[]).filter(x => x && x !== '置顶')[0];
  if(lb) h += badge(lb, 'lbl-' + lb);
  const tc = (w.topic_categories||[])[0];
  if(tc) h += `<span class="cat">${tc}</span>`;
  return h;
}

// 持续霸榜
// 条形宽度用【上榜占比】而不是连续轮数，也不是持续度得分。
// 原因：长跑到近百轮后，榜首十来个词条的连续轮数会全部并列（都是 55 轮），
// 拿连续轮数当宽度会让它们全是 100% 满格，图表直接失去信息量。
// 换个轴试过两次：
//   - 持续度得分：这些词本来就是近乎并列的真平局，得分只差 4%
//     （56.4 vs 54.0），画出来还是几乎一样宽，等于没修；
//   - 上榜占比（累计轮数 / 总轮数）：差距 56%~78%，是真实且有意义的区分度
//     —— "在全部 98 轮里有多少轮在榜"本身就是一个独立的强信号。
// 于是：宽度 = 上榜占比，右侧数字仍显示连续轮数（排序依据），
// 其余（累计/最佳/得分）放 tooltip。
function renderPersist(){
  const el = document.getElementById('persist');
  let list = D.persist.slice();
  if(state.q) list = list.filter(p=>p.word.includes(state.q));
  if(state.onlyStreak) list = list.filter(p=>p.current_streak>=2);
  if(!list.length){ el.innerHTML='<div class="empty">无匹配词条</div>'; return; }
  const runs = D.stats.runs || Math.max(...list.map(p=>p.total_rounds), 1);
  el.innerHTML = list.map(p=>{
    const pct=(p.total_rounds/runs*100).toFixed(1);
    const tags=p.sources.map(s=>`<span class="tag">${D.sourceNames[s]||s}</span>`).join('');
    const color = p.current_streak>=3 ? '#3fb950' : (p.current_streak>=2 ? '#d29922' : '#58a6ff');
    return `<div class="bar-row">
      <div class="bar-label" title="${p.word}"><span class="w">${p.word}</span>${markPersist(p)}</div>
      <div class="tags">${tags}</div>
      <div class="bar-track" title="上榜 ${p.total_rounds}/${runs} 轮（占比 ${pct}%）/ 连续 ${p.current_streak} 轮 / 最佳第 ${p.best_rank} 名 / 持续度 ${p.persistence_score}">
        <div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="bar-val"><span class="streak-badge" title="连续轮数">${p.current_streak}轮</span></div>
    </div>`;
  }).join('');
}

// 跨源共现榜
function renderCross(){
  const el = document.getElementById('cross');
  let list = D.cross.slice();
  if(state.q) list = list.filter(w=>w.word.includes(state.q));
  if(state.onlyMulti) list = list.filter(w=>w.cross_source_count>=2);
  if(!list.length){ el.innerHTML='<div class="empty">无匹配词条</div>'; return; }
  const max = Math.max(...list.map(w=>w.heat_index)) || 1;
  el.innerHTML = list.map(w=>{
    const pct = (w.heat_index/max*100).toFixed(1);
    const color = w.cross_source_count>=2 ? '#3fb950' : '#58a6ff';
    const tags = w.sources.map(s=>
      `<span class="tag" title="${s}">${D.sourceNames[s]||s}</span>`).join('');
    const tip = [w.word, (w.labels||[]).join('/'), (w.topic_categories||[]).join('/')]
      .filter(Boolean).join(' · ');
    return `<div class="bar-row">
      <div class="bar-label" title="${tip}"><span class="w">${w.word}</span>${markCross(w)}</div>
      <div class="tags">${tags}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="bar-val">${w.heat_index.toFixed(1)}</div>
    </div>`;
  }).join('');
}

// 位置与性质标记词条：证据价值在"性质"而非"热度"
function renderMarked(){
  const el = document.getElementById('marked');
  let list = (D.marked || []).slice();
  if(state.q) list = list.filter(m=>m.canonical_word.includes(state.q));
  if(!list.length){ el.innerHTML='<div class="empty">未发现被平台标记的词条</div>'; return; }
  const maxR = Math.max(...list.map(m=>m.total_rounds), 1);
  el.innerHTML = list.map(m=>{
    const mk = (m.is_ad ? badge('广告','lbl-广告') : '')
             + (m.is_top ? badge('置顶','lbl-置顶') : '')
             + (m.is_topic ? badge('挑战','lbl-挑战') : '');
    const srcs = (m.sources||'').split(',').filter(Boolean)
      .map(s=>`<span class="tag">${D.sourceNames[s]||s}</span>`).join('');
    const pct = (m.total_rounds/maxR*100).toFixed(1);
    const tip = `累计上榜 ${m.total_rounds} 轮` + (m.is_topic
      ? '（活动话题，已排除出持续度统计）'
      : ` / 最长连续 ${m.max_streak} 轮`);
    return `<div class="bar-row">
      <div class="bar-label" title="${m.canonical_word}"><span class="w">${m.canonical_word}</span>${mk}</div>
      <div class="tags">${srcs}</div>
      <div class="bar-track" title="${tip}">
        <div class="bar-fill" style="width:${pct}%;background:${m.is_topic?'#3fb950':'#f0883e'}"></div></div>
      <div class="bar-val">${m.total_rounds}轮</div>
    </div>`;
  }).join('');
}

// 搜索/筛选联动
['q','onlyMulti','onlyStreak'].forEach(id=>{
  const el=document.getElementById(id);
  el.addEventListener('input', ()=>{
    state.q = document.getElementById('q').value.trim();
    state.onlyMulti = document.getElementById('onlyMulti').checked;
    state.onlyStreak = document.getElementById('onlyStreak').checked;
    renderPersist(); renderCross(); renderMarked();
  });
});

renderPersist();
renderCross();
renderMarked();

// 排名走势（折线）
(function(){
  const el = document.getElementById('trend');
  const labels = D.runLabels, series = D.trendSeries, words = Object.keys(series);
  if(!labels.length || !words.length){ el.innerHTML='<div class="empty">需要多轮数据才能显示走势</div>'; return; }

  const W=560,H=260,PL=44,PR=16,PT=16,PB=34;
  const iw=W-PL-PR, ih=H-PT-PB;
  const allRanks=[].concat(...words.map(w=>series[w].filter(v=>v!=null)));
  // y 轴范围自适应：数据集中在 1..N 时不要强行从 1 拉到 10，
  // 否则所有折线会挤在顶部无法分辨（实测踩到）
  const dataMax=Math.max(...allRanks, 2);
  const dataMin=Math.min(...allRanks, 1);
  const pad=Math.max(1, Math.ceil((dataMax-dataMin)*0.15));
  const maxR=dataMax + pad, minR=Math.max(1, dataMin - pad);
  const x=i=>PL+(labels.length===1?iw/2:iw*i/(labels.length-1));
  const y=r=>PT+ih*(r-minR)/Math.max(maxR-minR,1);

  const palette=['#58a6ff','#3fb950','#f0883e','#ff7b72','#d2a8ff','#79c0ff','#56d364','#ffa657'];
  let svg=`<svg viewBox="0 0 ${W} ${H}">`;
  // y 轴网格：等分 4 段，展示真实刻度
  const steps=4;
  for(let s=0;s<=steps;s++){
    const t=Math.round(minR+(maxR-minR)*s/steps);
    svg+=`<line class="axis" x1="${PL}" y1="${y(t)}" x2="${W-PR}" y2="${y(t)}"/>`;
    svg+=`<text class="axis-text" x="${PL-8}" y="${y(t)+4}" text-anchor="end">${t}</text>`;
  }
  // x 轴标签（轮次）
  labels.forEach((lb,i)=>{
    if(labels.length>8 && i%2!==0 && i!==labels.length-1) return;
    svg+=`<text class="axis-text" x="${x(i)}" y="${H-PB+18}" text-anchor="middle">${lb.slice(0,5)}</text>`;
  });
  // 折线
  words.forEach((w,wi)=>{
    const c=palette[wi%palette.length];
    let d='', started=false;
    series[w].forEach((r,i)=>{
      if(r==null){started=false;return;}
      d += (started?' L':' M')+x(i)+' '+y(r);
      started=true;
    });
    if(d) svg+=`<path d="${d.trim()}" fill="none" stroke="${c}" stroke-width="2" stroke-linejoin="round"/>`;
    series[w].forEach((r,i)=>{
      if(r!=null) svg+=`<circle cx="${x(i)}" cy="${y(r)}" r="2.6" fill="${c}"/>`;
    });
  });
  svg+='</svg>';
  const legend = words.map((w,i)=>
    `<span><i style="background:${palette[i%palette.length]}"></i>${w.slice(0,14)}</span>`).join('');
  el.innerHTML = svg + `<div class="legend">${legend}</div>`;
})();

// 各源每轮采集量（堆叠柱）
(function(){
  const el = document.getElementById('health');
  const labels=D.runLabels, tl=D.timeline, sources=Object.keys(tl);
  if(!labels.length || !sources.length){ el.innerHTML='<div class="empty">暂无数据</div>'; return; }

  const W=560,H=260,PL=44,PR=16,PT=16,PB=34;
  const iw=W-PL-PR, ih=H-PT-PB;
  const n=labels.length;
  const totals=labels.map((_,i)=>sources.reduce((s,src)=>s+(tl[src][i]||0),0));
  const maxT=Math.max(...totals,1);
  const bw=Math.min(34, iw/Math.max(n,1)*0.6);
  const xc=i=>PL+iw*(i+0.5)/n;
  const yv=v=>PT+ih*(1-v/maxT);

  let svg=`<svg viewBox="0 0 ${W} ${H}">`;
  [0,maxT].forEach(t=>{
    svg+=`<line class="axis" x1="${PL}" y1="${yv(t)}" x2="${W-PR}" y2="${yv(t)}"/>`;
    svg+=`<text class="axis-text" x="${PL-8}" y="${yv(t)+4}" text-anchor="end">${t}</text>`;
  });
  labels.forEach((lb,i)=>{
    if(n>8 && i%2!==0 && i!==n-1) return;
    svg+=`<text class="axis-text" x="${xc(i)}" y="${H-PB+18}" text-anchor="middle">${lb.slice(0,5)}</text>`;
  });
  for(let i=0;i<n;i++){
    let acc=0;
    sources.forEach(src=>{
      const v=tl[src][i]||0;
      if(!v) return;
      const h=ih*v/maxT;
      svg+=`<rect x="${xc(i)-bw/2}" y="${yv(acc+v)}" width="${bw}" height="${h}" `
         + `fill="${D.sourceColors[src]||'#8b949e'}" rx="2"><title>${D.sourceNames[src]||src}: ${v}</title></rect>`;
      acc+=v;
    });
  }
  svg+='</svg>';
  const legend=sources.map(s=>
    `<span><i style="background:${D.sourceColors[s]||'#8b949e'}"></i>${D.sourceNames[s]||s}</span>`).join('');
  el.innerHTML = svg + `<div class="legend">${legend}</div>`;
})();
</script>
</body>
</html>
"""
