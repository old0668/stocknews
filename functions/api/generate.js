export async function onRequestPost(context) {
  let body;
  try {
    body = await context.request.json();
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const items = Array.isArray(body.items) ? body.items : [];
  if (items.length === 0) {
    return Response.json({ error: "items 不可為空" }, { status: 400 });
  }

  const lines = ["#### 今日財經要聞", ""];
  for (const item of items) {
    const timeInfo = `[${item?.display_time || "今日"}]`;
    const title = String(item?.title || "").replace(/\s+/g, " ").trim() || "（無標題）";
    const source = String(item?.source || "").trim();
    lines.push(source ? `${timeInfo} ${title}（${source}）` : `${timeInfo} ${title}`);
    lines.push("");
  }

  return Response.json({ summary: lines.join("\n").trim(), model: "no-ai" });
}
