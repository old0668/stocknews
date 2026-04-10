/**
 * 經濟情報站前端：讀取 data/*.json、繪製走勢、呼叫 /api/generate（純新聞清單）
 */

const DISPLAY_TZ = "Asia/Taipei";
const STORAGE_KEY = "news0407_history_overlay";
const LAST_ITEMS_FP_KEY = "news0407_last_items_fp";
/** 向伺服器重新讀取 data/*.json 的間隔（GitHub 約每小時推送新稿，5 分鐘內可跟上部署） */
const DATA_POLL_MS = 5 * 60 * 1000;

function nowDisplayStr() {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t)?.value || "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}`;
}

async function fetchJson(path) {
  const sep = path.includes("?") ? "&" : "?";
  const url = `${path}${sep}_=${Date.now()}`;
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

function mergeNewsForLlm(poolItems, todayData) {
  const byLink = new Map();
  for (const it of poolItems || []) {
    if (it && it.link) byLink.set(it.link, { ...it });
  }
  const news = todayData?.news || [];
  for (const it of news) {
    if (it && it.link) byLink.set(it.link, { ...it });
  }
  const out = Array.from(byLink.values());
  out.sort((a, b) => {
    const da = parseItemDt(a);
    const db = parseItemDt(b);
    return db - da;
  });
  return out.slice(0, 40);
}

/** 與 merge 邏輯相同，但列出較多則供頁面「最新抓取」區塊顯示 */
function mergeNewsForDisplay(poolItems, todayData, limit = 80) {
  const byLink = new Map();
  for (const it of poolItems || []) {
    if (it && it.link) byLink.set(it.link, { ...it });
  }
  const news = todayData?.news || [];
  for (const it of news) {
    if (it && it.link) byLink.set(it.link, { ...it });
  }
  const out = Array.from(byLink.values());
  out.sort((a, b) => parseItemDt(b) - parseItemDt(a));
  return out.slice(0, limit);
}

function escHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderRawNewsSection(items, todayDateStr) {
  const listEl = document.getElementById("rawNewsList");
  const metaEl = document.getElementById("rawNewsMeta");
  const emptyEl = document.getElementById("rawNewsEmpty");
  if (!listEl || !metaEl || !emptyEl) return;

  listEl.innerHTML = "";
  if (!items.length) {
    emptyEl.hidden = false;
    metaEl.textContent = "";
    return;
  }
  emptyEl.hidden = true;
  const dateHint = todayDateStr ? `今日累積日期：${todayDateStr} · ` : "";
  metaEl.textContent = `${dateHint}共 ${items.length} 則（合併今日累積與 48h 候選池，依時間新→舊）`;

  for (const it of items) {
    const title = escHtml(it.title || "（無標題）");
    const src = escHtml(it.source || "");
    const t = escHtml(it.display_time || "—");
    const url = it.link || "";
    const sum = (it.summary || "").replace(/\s+/g, " ").trim();
    const snippet = sum ? escHtml(sum.slice(0, 220)) + (sum.length > 220 ? "…" : "") : "";

    const li = document.createElement("li");
    li.className = "raw-news-item";
    const safeUrl = escHtml(url);
    const titlePart = url
      ? `<a class="title-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer">${title}</a>`
      : `<span class="title-link">${title}</span>`;
    li.innerHTML = `
      ${titlePart}
      <div class="raw-news-meta-line">${t} · ${src}</div>
      ${snippet ? `<div class="raw-news-snippet">${snippet}</div>` : ""}
    `;
    listEl.appendChild(li);
  }
}

function parseItemDt(item) {
  const pub = item.published;
  if (pub) {
    const t = Date.parse(pub);
    if (!Number.isNaN(t)) return t;
  }
  return 0;
}

function fingerprintItems(items) {
  const rows = (items || []).map((it) => [
    it.link || "",
    it.display_time || "",
    it.title || "",
    it.source || "",
  ]);
  rows.sort((a, b) => a[0].localeCompare(b[0]));
  return JSON.stringify(rows);
}

function ensureTodayNewsLineBreaks(summary) {
  const start = "#### 今日財經要聞";
  if (!summary.includes(start)) return summary;
  const si = summary.indexOf(start) + start.length;
  let ei = summary.length;
  for (const em of ["\n#### 核心動態分析", "\n#### 核心動態分析與情緒"]) {
    const pos = summary.indexOf(em, si);
    if (pos !== -1) ei = Math.min(ei, pos);
  }
  let body = summary.slice(si, ei);
  body = body.replace(
    /(?<=\])\s*(?=\s*(?:\*\*)?\s*\[(?:\d{2}\/\d{2}\s+\d{2}:\d{2}|\d{1,2}:\d{2})\])/g,
    "\n\n"
  );
  body = body.replace(/(?<!\n)\n(?=\s*\*\*\s*\[)/g, "\n\n");
  body = body.replace(/(?<!\n)\n(?=\s*\*\s*\[)/g, "\n\n");
  body = body.replace(/(?<!\n)\n(?=\s*\[)/g, "\n\n");
  body = body.replace(/\n\n\n+/g, "\n\n");
  return summary.slice(0, si) + body + summary.slice(ei);
}

function unwrapSentimentSpans(body) {
  return body
    .replace(/<span style="color:#EF4444;font-weight:600;">(\[[^\]]+\])<\/span>/gi, "$1")
    .replace(/<span style="color:#48BB78;font-weight:600;">(\[[^\]]+\])<\/span>/gi, "$1")
    .replace(/<span style="color:#A0AEC0;font-weight:600;">(\[[^\]]+\])<\/span>/gi, "$1");
}

function colorizeSentimentScoresInTodayNews(summary) {
  const start = "#### 今日財經要聞";
  if (!summary.includes(start)) return summary;
  const si = summary.indexOf(start) + start.length;
  let ei = summary.length;
  for (const em of ["\n#### 核心動態分析", "\n#### 核心動態分析與情緒"]) {
    const pos = summary.indexOf(em, si);
    if (pos !== -1) ei = Math.min(ei, pos);
  }
  let body = summary.slice(si, ei);
  body = unwrapSentimentSpans(body);
  body = body.replace(/\[\+\/-[^\]]+\]/g, (m) => `<span style="color:#A0AEC0;font-weight:600;">${m}</span>`);
  body = body.replace(/\[\+\s*\d+\.?\d*\]/g, (m) => `<span style="color:#EF4444;font-weight:600;">${m}</span>`);
  body = body.replace(/\[-\s*\d+\.?\d*\]/g, (m) => `<span style="color:#48BB78;font-weight:600;">${m}</span>`);
  return summary.slice(0, si) + body + summary.slice(ei);
}

function parseUpdateTime(summary) {
  const m = summary.match(/🕒 更新時間：([\d-]+\s[\d:]+)/);
  if (!m) return null;
  const d = new Date(m[1].replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? null : d;
}

function parseItemNewsDatetime(plain, refYear, updateDt) {
  const stripTags = plain.replace(/<[^>]+>/g, "");
  let m = stripTags.match(/\[(\d{2})\/(\d{2})\s+(\d{2}):(\d{2})\]/);
  if (m) {
    const mo = +m[1];
    const d = +m[2];
    const h = +m[3];
    const mi = +m[4];
    return new Date(refYear, mo - 1, d, h, mi).getTime();
  }
  m = stripTags.match(/\[(\d{1,2}):(\d{2})\]/);
  if (m) {
    const h = +m[1];
    const mi = +m[2];
    const refD = new Date(updateDt);
    const itemT = h * 3600000 + mi * 60000;
    const updT = refD.getHours() * 3600000 + refD.getMinutes() * 60000;
    let day = new Date(updateDt);
    if (itemT <= updT) {
      /* same day */
    } else if (refD.getHours() < 6 && h >= 12) {
      day = new Date(refD.getTime() - 86400000);
    }
    return new Date(day.getFullYear(), day.getMonth(), day.getDate(), h, mi).getTime();
  }
  return 0;
}

function sortTodayNewsSectionNewestFirst(summary) {
  const start = "#### 今日財經要聞";
  if (!summary.includes(start)) return summary;
  const updateDt = parseUpdateTime(summary) || new Date();
  const refYear = updateDt.getFullYear();
  const si = summary.indexOf(start) + start.length;
  let ei = summary.length;
  for (const em of ["\n#### 核心動態分析", "\n#### 核心動態分析與情緒"]) {
    const pos = summary.indexOf(em, si);
    if (pos !== -1) ei = Math.min(ei, pos);
  }
  const body = summary.slice(si, ei).trim();
  if (!body) return summary;
  const rawChunks = body.split(/\n\s*\n+/);
  const items = rawChunks.map((c) => c.trim()).filter(Boolean);
  if (items.length <= 1) return summary;
  const keyed = items.map((item) => {
    const plain = item.replace(/<[^>]+>/g, "");
    const dt = parseItemNewsDatetime(plain, refYear, updateDt.getTime());
    return { dt, item };
  });
  keyed.sort((a, b) => b.dt - a.dt);
  const newBody = keyed.map((x) => x.item).join("\n\n") + "\n\n";
  return summary.slice(0, si) + "\n\n" + newBody + summary.slice(ei);
}

function filterTodayNewsSection(summary, showAll) {
  if (showAll) return summary;
  const start = "#### 今日財經要聞";
  if (!summary.includes(start)) return summary;
  const updateDt = parseUpdateTime(summary) || new Date();
  const refYear = updateDt.getFullYear();
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(new Date());
  const g = (t) => parts.find((p) => p.type === t)?.value;
  const nowStr = `${g("year")}-${g("month")}-${g("day")} ${g("hour")}:${g("minute")}`;
  const now = new Date(nowStr.replace(" ", "T"));
  const cutoff = now.getTime() - 24 * 3600000;

  const si = summary.indexOf(start) + start.length;
  let ei = summary.length;
  for (const em of ["\n#### 核心動態分析", "\n#### 核心動態分析與情緒"]) {
    const pos = summary.indexOf(em, si);
    if (pos !== -1) ei = Math.min(ei, pos);
  }
  const body = summary.slice(si, ei).trim();
  if (!body) return summary;
  const rawChunks = body.split(/\n\s*\n+/);
  const items = rawChunks.map((c) => c.trim()).filter(Boolean);
  const kept = [];
  for (const item of items) {
    const plain = item.replace(/<[^>]+>/g, "");
    const itemDt = parseItemNewsDatetime(plain, refYear, updateDt.getTime());
    if (itemDt === 0 || itemDt >= cutoff) kept.push(item);
  }
  if (!kept.length) return summary;
  const newBody = kept.join("\n\n") + "\n\n";
  return summary.slice(0, si) + "\n\n" + newBody + summary.slice(ei);
}

function normWs(s) {
  return (s || "").replace(/\s+/g, " ").trim();
}

function matchLinkForChunk(plain, newsItems) {
  plain = plain.replace(/<[^>]+>/g, "").replace(/\*\*?/g, "").trim();
  let disp = null;
  let m = plain.match(/\[(\d{2}\/\d{2}\s+\d{2}:\d{2})\]/);
  if (m) disp = normWs(m[1]);
  else {
    m = plain.match(/\[(\d{1,2}:\d{2})\]/);
    if (m) disp = m[1];
  }
  let candidates = [];
  if (disp) {
    for (const n of newsItems) {
      const nd = normWs(n.display_time || "");
      if (nd === disp) candidates.push(n);
    }
  }
  if (candidates.length === 1) return candidates[0].link || null;
  if (!candidates.length) candidates = [...newsItems];
  let rest = plain;
  m = rest.match(/\[[^\]]+\]\s*/);
  if (m) rest = rest.slice(m.index + m[0].length);
  rest = rest.replace(/\s*\[\+\-[^\]]+\]\s*$/g, "").replace(/\s*\[\+\/[^\]]+\]\s*$/g, "").trim();
  const headline = normWs(rest).slice(0, 500);
  if (headline.length < 3) return null;
  let bestScore = 0;
  let bestUrl = null;
  for (const n of candidates) {
    const t = normWs(n.title || "").slice(0, 300);
    if (!t) continue;
    let score = 0;
    for (let i = 0; i < Math.min(headline.length, t.length); i++) {
      if (headline[i] === t[i]) score += 1;
    }
    score = score / Math.max(headline.length, t.length);
    const h35 = headline.slice(0, 35);
    const t35 = t.slice(0, 35);
    if (h35 && t35 && (t.includes(h35) || headline.includes(t35))) score = Math.max(score, 0.52);
    if (score > bestScore) {
      bestScore = score;
      bestUrl = n.link;
    }
  }
  if (bestScore < 0.24) return null;
  return bestUrl;
}

function splitSentimentTail(rest) {
  if (/<span/i.test(rest)) {
    const m = rest.match(/(\s*(?:<span[^>]*>[\s\S]*?<\/span>\s*)+)$/i);
    if (m) return [rest.slice(0, m.index).trimEnd(), m[1]];
  }
  let m2 = rest.match(/(\s*\[[+-]?\d+(?:\.\d+)?\]\s*)$/);
  if (m2) return [rest.slice(0, m2.index).trimEnd(), m2[1]];
  m2 = rest.match(/(\s*\[\+\/[^\]]+\]\s*)$/);
  if (m2) return [rest.slice(0, m2.index).trimEnd(), m2[1]];
  return [rest.trimEnd(), ""];
}

function wrapChunkWithLink(chunk, url) {
  if (chunk.includes("news-ext-link")) return chunk;
  if (/<a\s+[^>]*href\s*=/i.test(chunk)) return chunk;
  const esc = url.replace(/&/g, "&amp;").replace(/"/g, "&quot;");
  let start = null;
  let end = null;
  const re = /\[[^\]]+\]/g;
  let m;
  while ((m = re.exec(chunk)) !== null) {
    const inner = m[0].slice(1, -1).trim();
    if (/^\d{2}\/\d{2}\s+\d{2}:\d{2}$/.test(inner) || /^\d{1,2}:\d{2}$/.test(inner)) {
      start = m.index;
      end = m.index + m[0].length;
      break;
    }
  }
  if (start == null) return chunk;
  const pre = chunk.slice(0, start);
  const timeToken = chunk.slice(start, end);
  const rest = chunk.slice(end);
  const [head, tail] = splitSentimentTail(rest);
  if (!head.trim()) return chunk;
  const headEsc = head.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `${pre}${timeToken}${headEsc} <a href="${esc}" class="news-ext-link" target="_blank" rel="noopener noreferrer" title="開啟原文" aria-label="開啟原文連結">🔗</a>${tail}`;
}

function linkifyTodayNewsSection(summary, newsItems) {
  if (!newsItems || !newsItems.length) return summary;
  const start = "#### 今日財經要聞";
  if (!summary.includes(start)) return summary;
  const si = summary.indexOf(start) + start.length;
  let ei = summary.length;
  for (const em of ["\n#### 核心動態分析", "\n#### 核心動態分析與情緒"]) {
    const pos = summary.indexOf(em, si);
    if (pos !== -1) ei = Math.min(ei, pos);
  }
  const body = summary.slice(si, ei).trim();
  if (!body) return summary;
  const rawChunks = body.split(/\n\s*\n+/);
  const newChunks = [];
  for (const chunk of rawChunks) {
    const c = chunk.trim();
    if (!c) continue;
    const plain = c.replace(/<[^>]+>/g, "");
    const link = matchLinkForChunk(plain, newsItems);
    newChunks.push(link ? wrapChunkWithLink(c, link) : c);
  }
  const newBody = newChunks.join("\n\n") + "\n\n";
  return summary.slice(0, si) + "\n\n" + newBody + summary.slice(ei);
}

function extractConfidenceIndexForTrend(summary) {
  if (!summary) return null;
  let m = summary.match(/####\s*今日市場信心指數\s*/);
  let block;
  if (m) {
    const start = m.index + m[0].length;
    let ei = summary.length;
    for (const em of ["\n#### ", "\n---"]) {
      const p = summary.indexOf(em, start);
      if (p !== -1) ei = Math.min(ei, p);
    }
    block = summary.slice(start, ei);
  } else {
    const matches = [...summary.matchAll(/信心指數[：:為\s]*\**(\d+(?:\.\d+)?)\**/g)];
    if (matches.length) return parseFloat(matches[matches.length - 1][1]);
    return null;
  }
  let m2 = block.match(/信心指數[：:為\s]*\**(\d+(?:\.\d+)?)\**/);
  if (m2) return parseFloat(m2[1]);
  m2 = block.match(/[：:]\s*\**(\d+(?:\.\d+)?)\**/);
  if (m2) return parseFloat(m2[1]);
  const plain = block.replace(/<[^>]+>/g, "").trim();
  m2 = plain.match(/\b(\d{1,3}(?:\.\d+)?)\b/);
  if (m2) {
    const v = parseFloat(m2[1]);
    if (v >= 0 && v <= 100) return v;
  }
  return null;
}

function mdToSafeHtml(md) {
  const markedLib = globalThis.marked;
  const purify = globalThis.DOMPurify;
  if (!markedLib?.parse || !purify?.sanitize) {
    return md.replace(/</g, "&lt;").replace(/\n/g, "<br/>");
  }
  const raw = markedLib.parse(md, { async: false });
  return purify.sanitize(raw, {
    ADD_ATTR: ["target", "rel", "title", "aria-label", "class", "style"],
  });
}

function processHistoryMarkdown(raw, newsItems, showAll, mergeTime) {
  let hist = raw;
  if (mergeTime) {
    if (/<div class='update-time'>/.test(hist)) {
      hist = hist.replace(
        /<div class='update-time'>🕒 更新時間：[\d-]+\s[\d:]+<\/div>/,
        `<div class='update-time'>🕒 更新時間：${mergeTime}</div>`
      );
    } else if (/🕒 更新時間：[\d-]+\s[\d:]+/.test(hist)) {
      hist = hist.replace(/🕒 更新時間：[\d-]+\s[\d:]+/, `🕒 更新時間：${mergeTime}`);
    } else {
      hist = `<div class='update-time'>🕒 更新時間：${mergeTime}</div>\n\n` + hist;
    }
  }
  hist = ensureTodayNewsLineBreaks(hist);
  hist = sortTodayNewsSectionNewestFirst(hist);
  hist = filterTodayNewsSection(hist, showAll);
  hist = linkifyTodayNewsSection(hist, newsItems);
  hist = colorizeSentimentScoresInTodayNews(hist);
  return hist;
}

function renderHistoryHtmlFromMd(md) {
  const m = md.match(/^<div class='update-time'>[\s\S]*?<\/div>\s*/);
  let header = "";
  let body = md;
  if (m) {
    header = m[0];
    body = md.slice(m[0].length).trim();
  }
  const bodyHtml = mdToSafeHtml(body);
  return `<div class="summary-box">${header}<div class="md-body">${bodyHtml}</div></div>`;
}

function renderHistoryHtml(raw, newsItems, showAll, mergeTime) {
  const md = processHistoryMarkdown(raw, newsItems, showAll, mergeTime);
  return renderHistoryHtmlFromMd(md);
}

function parseSummaryUpdateTime(raw) {
  const m = String(raw || "").match(/更新時間：(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2})/);
  if (!m) return 0;
  const t = Date.parse(m[1].replace(" ", "T"));
  return Number.isNaN(t) ? 0 : t;
}

function loadOverlay() {
  try {
    const t = localStorage.getItem(STORAGE_KEY);
    return t ? JSON.parse(t) : [];
  } catch {
    return [];
  }
}

function saveOverlay(items) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, 10)));
  } catch {
    /* ignore */
  }
}

function normalizeTrendRows(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const ts = row.timestamp;
    const av = row.average_sentiment;
    if (ts == null || av == null) continue;
    const n = parseFloat(av);
    if (Number.isNaN(n)) continue;
    out.push({
      timestamp: ts,
      average_sentiment: n,
      news_count: parseInt(row.news_count || 0, 10) || 0,
    });
  }
  return out;
}

function aggregateTrendPlot(df, unit) {
  if (!df.length) return { rows: [], xFmt: "%H:%M" };
  const rows = df.map((r) => ({
    t: new Date(r.timestamp).getTime(),
    val:
      r.average_sentiment >= -1 && r.average_sentiment <= 1 && r.average_sentiment !== 0
        ? Math.round((r.average_sentiment + 1) * 50)
        : r.average_sentiment,
  }));
  rows.sort((a, b) => a.t - b.t);
  if (unit === "raw") return { rows, xFmt: "%m/%d %H:%M" };

  const bucketMs = unit === "hour" ? 3600000 : 86400000;
  const map = new Map();
  for (const r of rows) {
    const k = Math.floor(r.t / bucketMs) * bucketMs;
    const prev = map.get(k) || { sum: 0, n: 0 };
    prev.sum += r.val;
    prev.n += 1;
    map.set(k, prev);
  }
  const agg = [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, v]) => ({ t, val: v.sum / v.n }));
  return { rows: agg, xFmt: unit === "hour" ? "%m/%d %H:%M" : "%m/%d" };
}

function drawChart(plotRows, xFmt, latestVal) {
  const el = document.getElementById("chart");
  const empty = document.getElementById("chartEmpty");
  if (!plotRows.length) {
    el.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  const xs = plotRows.map((r) => new Date(r.t));
  const ys = plotRows.map((r) => r.val);
  if (latestVal != null && ys.length) ys[ys.length - 1] = latestVal;

  const trace = {
    x: xs,
    y: ys,
    mode: "lines+markers",
    fill: "tozeroy",
    fillcolor: "rgba(11, 197, 234, 0.15)",
    line: { width: 4, color: "#0BC5EA", shape: plotRows.length < 3 ? "linear" : "spline" },
    marker: { size: 8, color: "#0BC5EA", line: { width: 1.5, color: "#FFFFFF" } },
    connectgaps: true,
    name: "指數",
  };

  Plotly.newPlot(
    el,
    [trace],
    {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      margin: { l: 0, r: 0, t: 30, b: 0 },
      height: 300,
      showlegend: false,
      xaxis: {
        showgrid: true,
        gridcolor: "rgba(255,255,255,0.08)",
        tickfont: { size: 9, color: "#718096" },
        tickformat: xFmt,
        type: "date",
      },
      yaxis: {
        showgrid: true,
        gridcolor: "rgba(255,255,255,0.08)",
        tickfont: { size: 9, color: "#718096" },
        range: [0, 100],
        dtick: 25,
        zeroline: false,
        fixedrange: true,
      },
      dragmode: "pan",
      hovermode: "x unified",
    },
    { scrollZoom: true, displayModeBar: false, showTips: false }
  );
}

function updateMetric(latestVal, prevVal) {
  const metric = document.getElementById("metric");
  if (latestVal == null) {
    metric.hidden = true;
    return;
  }
  metric.hidden = false;
  const diff = latestVal - (prevVal ?? latestVal);
  const up = diff >= 0;
  metric.innerHTML = `
    <div class="metric-label">最新信心指數 (Live)</div>
    <div class="metric-value">${latestVal.toFixed(0)}</div>
    <div class="metric-delta ${up ? "up" : "down"}">${diff >= 0 ? "+" : ""}${diff.toFixed(0)} pts (相對前次)</div>
  `;
}

async function main() {
  const summaryList = document.getElementById("summaryList");
  const msg = document.getElementById("msg");
  const btnToggle = document.getElementById("btnToggleNews");
  const btnRefresh = document.getElementById("btnRefresh");
  const btnClearCache = document.getElementById("btnClearCache");

  let showPriorNews = true;
  let trendsCache = null;
  let manualDisplayTime = null;

  btnToggle.textContent = showPriorNews ? "24HR新聞" : "顯示全部新聞";

  async function loadAndRender() {
    let history = [];
    let trends = [];
    let todayData = { news: [] };

    try {
      history = await fetchJson("./data/history.json");
      if (!Array.isArray(history)) history = [];
    } catch {
      history = [];
    }
    try {
      trends = normalizeTrendRows(await fetchJson("./data/sentiment_trends.json"));
    } catch {
      trends = trendsCache || [];
    }
    if (trends.length) trendsCache = trends;
    try {
      todayData = await fetchJson("./data/today_news.json");
    } catch {
      todayData = { news: [] };
    }

    let poolData = [];
    try {
      poolData = await fetchJson("./data/recent_news_pool.json");
      if (!Array.isArray(poolData)) poolData = [];
    } catch {
      poolData = [];
    }
    const rawForDisplay = mergeNewsForDisplay(poolData, todayData, 80);
    renderRawNewsSection(rawForDisplay, todayData.date || "");

    const overlay = loadOverlay();
    const topServerTs = history.length ? parseSummaryUpdateTime(history[0]) : 0;
    const freshOverlay = overlay.filter((item) => parseSummaryUpdateTime(item) >= topServerTs);
    const mergedFirst = [...freshOverlay, ...history].slice(0, 5);

    const newsItems = todayData.news || [];

    summaryList.innerHTML = "";
    if (!mergedFirst.length) {
      summaryList.innerHTML =
        '<p class="empty">目前沒有可顯示的新聞清單。可按「立即更新」，或等待排程寫入 data/history.json。</p>';
    } else {
      mergedFirst.forEach((hist, idx) => {
        const mergeTime = idx === 0 ? manualDisplayTime : null;
        summaryList.insertAdjacentHTML(
          "beforeend",
          renderHistoryHtml(hist, newsItems, showPriorNews, mergeTime)
        );
      });
    }

    const unit = document.querySelector('input[name="unit"]:checked')?.value || "raw";
    let { rows: plotRows, xFmt } = aggregateTrendPlot(trends, unit);

    let summaryConf = null;
    let prevSummaryConf = null;
    if (mergedFirst.length) {
      const h0md = processHistoryMarkdown(mergedFirst[0], newsItems, showPriorNews, manualDisplayTime);
      summaryConf = extractConfidenceIndexForTrend(h0md);
      if (mergedFirst.length > 1) {
        const h1md = processHistoryMarkdown(mergedFirst[1], newsItems, showPriorNews, null);
        prevSummaryConf = extractConfidenceIndexForTrend(h1md);
      }
    }

    let latestVal = null;
    let prevVal = null;
    if (plotRows.length) {
      latestVal = plotRows[plotRows.length - 1].val;
      prevVal = plotRows.length > 1 ? plotRows[plotRows.length - 2].val : 50;
    }
    if (summaryConf != null) {
      latestVal = summaryConf;
      if (prevSummaryConf != null) prevVal = prevSummaryConf;
      else if (plotRows.length > 1) prevVal = plotRows[plotRows.length - 2].val;
    }

    updateMetric(latestVal, prevVal);

    if (plotRows.length) {
      const adj = plotRows.map((r) => ({ ...r }));
      if (summaryConf != null && adj.length) adj[adj.length - 1].val = summaryConf;
      drawChart(adj, xFmt, summaryConf);
    } else {
      drawChart([], xFmt, null);
    }

    const syncEl = document.getElementById("syncStatus");
    if (syncEl) {
      syncEl.textContent = `已載入 ${nowDisplayStr()}（台北）· 每 ${Math.round(DATA_POLL_MS / 60000)} 分鐘自動向伺服器讀取最新稿件 · 切回此分頁也會更新`;
    }
  }

  btnToggle.addEventListener("click", () => {
    showPriorNews = !showPriorNews;
    btnToggle.textContent = showPriorNews ? "24HR新聞" : "顯示全部新聞";
    loadAndRender();
  });

  document.querySelectorAll('input[name="unit"]').forEach((el) => {
    el.addEventListener("change", () => loadAndRender());
  });

  if (btnClearCache) {
    btnClearCache.addEventListener("click", () => {
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(LAST_ITEMS_FP_KEY);
      location.reload();
    });
  }

  btnRefresh.addEventListener("click", async () => {
    msg.hidden = true;
    const refreshIdleText = "立即更新";
    btnRefresh.textContent = "更新中...";
    let pool = [];
    let todayData = { news: [] };
    try {
      pool = await fetchJson("./data/recent_news_pool.json");
      if (!Array.isArray(pool)) pool = [];
    } catch {
      pool = [];
    }
    try {
      todayData = await fetchJson("./data/today_news.json");
    } catch {
      todayData = { news: [] };
    }
    const items = mergeNewsForLlm(pool, todayData);
    if (!items.length) {
      msg.textContent = "沒有可更新的稿件（請確認 data/recent_news_pool.json 或今日新聞）。";
      msg.hidden = false;
      return;
    }
    const currentFp = fingerprintItems(items);
    const lastFp = localStorage.getItem(LAST_ITEMS_FP_KEY) || "";
    if (currentFp && currentFp === lastFp) {
      msg.textContent = "新聞內容未變更，已沿用上次清單。";
      msg.hidden = false;
      return;
    }
    btnRefresh.disabled = true;
    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items, forceRefresh: true }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data?.detail ? `｜${String(data.detail).slice(0, 220)}` : "";
        throw new Error((data.error || res.statusText || "請求失敗") + detail);
      }
      const summary = data.summary;
      if (!summary) throw new Error("未收到摘要文字");

      manualDisplayTime = nowDisplayStr();
      const timestamp = manualDisplayTime;
      const webContent = `<div class='update-time'>🕒 更新時間：${timestamp}</div>\n\n${summary}\n\n---`;

      const overlay = loadOverlay();
      overlay.unshift(webContent);
      saveOverlay(overlay);
      localStorage.setItem(LAST_ITEMS_FP_KEY, currentFp);

      msg.textContent = "已更新新聞清單（已暫存於瀏覽器，與靜態檔合併顯示）。";
      msg.hidden = false;
      await loadAndRender();
    } catch (e) {
      msg.textContent = `更新失敗：${e.message || e}。`;
      msg.hidden = false;
    } finally {
      btnRefresh.disabled = false;
      btnRefresh.textContent = refreshIdleText;
    }
  });

  await loadAndRender();

  setInterval(() => {
    loadAndRender().catch(() => {});
  }, DATA_POLL_MS);

  let resumeDebounce = null;
  function reloadWhenUserReturns() {
    if (resumeDebounce) clearTimeout(resumeDebounce);
    resumeDebounce = setTimeout(() => {
      resumeDebounce = null;
      loadAndRender().catch(() => {});
    }, 600);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") reloadWhenUserReturns();
  });

  window.addEventListener("focus", reloadWhenUserReturns);
}

main();
