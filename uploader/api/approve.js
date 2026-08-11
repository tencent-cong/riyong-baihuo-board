// POST /api/approve
// body: { token, id, track? }   token=ADMIN_TOKEN；track 可选（管理员在审核页微调后的最终版）
// 流程：读 creative.js → 合并该 track → 写回（触发 Pages 重建）→ 删除待审记录
import {
  ENV, ghGetFile, ghPutFile, ghDeleteFile, ghDispatchFrameWorkflow,
  parseCreative, dumpCreative, mergeTrack,
  SELECTION_PATH, parseSelection, dumpSelection, mergeSelection, updateSelectionPeriod,
  setCors, readJsonBody, checkToken,
} from "./_lib.js";

export default async function handler(req, res) {
  setCors(res);
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "只支持 POST" });

  try {
    const body = await readJsonBody(req);
    const { token, id } = body;
    if (!checkToken(token, ENV.ADMIN_TOKEN))
      return res.status(401).json({ error: "审核口令错误" });
    if (!id) return res.status(400).json({ error: "缺少待审核记录 id" });

    // 1) 取待审核记录
    const pendingPath = `${ENV.PENDING_DIR}/${id}.json`;
    const pend = await ghGetFile(pendingPath);
    if (!pend) return res.status(404).json({ error: "待审核记录不存在（可能已处理）" });
    const record = JSON.parse(pend.text);

    let finalMessage = "";

    // 2) 分流处理：商品选品 OR 创意卖点
    if (record.type === "selection") {
      // 选品数据更新
      const cur = await ghGetFile(SELECTION_PATH);
      if (!cur) return res.status(500).json({ error: "线上 selection.js 读取失败" });
      
      const data = parseSelection(cur.text);
      const items = body.items || record.items; // 支持微调或默认
      const stats = mergeSelection(data, record.trackName, items);
      updateSelectionPeriod(data, [...(items.nonClosed || []), ...(items.quanyutong || []), ...(items.adq || [])], record.selectionPeriod);
      
      const newText = dumpSelection(data);
      await ghPutFile(
        SELECTION_PATH, newText,
        `data: 审核通过·更新赛道「${record.trackName}」选品数据 (${id})`,
        cur.sha
      );

      finalMessage = `已审核更新「${record.trackName}」选品：非闭环 ${stats.nonClosedCount} 款，闭环 ${stats.closedCount} 款，线上看板正在重新编译。`;

    } else {
      // 创意卖点更新
      const track = body.track || record.track;
      track.name = record.trackName; // 保底对齐

      const cur = await ghGetFile(ENV.CREATIVE_PATH);
      if (!cur) return res.status(500).json({ error: "线上 creative.js 读取失败" });
      
      const data = parseCreative(cur.text);
      const { action, name } = mergeTrack(data, track);
      
      data.meta = data.meta || {};
      data.meta.updatedAt = new Date().toISOString().slice(0, 10);
      
      const newText = dumpCreative(data);
      await ghPutFile(
        ENV.CREATIVE_PATH, newText,
        `data: 审核通过·${action === "update" ? "更新" : "新增"}赛道「${name}」创意分析 (${id})`,
        cur.sha
      );

      const hasMedia = (track.topMaterials || []).some(item => /^https?:\/\//i.test(String(item.videoUrl || "")));
      if (hasMedia) await ghDispatchFrameWorkflow(name);
      finalMessage = `已${action === "update" ? "更新" : "新增"}赛道「${name}」创意分析并推送上线${hasMedia ? "，关键帧后台任务已启动" : ""}。`;
    }

    // 3) 删除待审记录
    try { await ghDeleteFile(pendingPath, `pending: 已审核通过并清除 ${id}`, pend.sha); } catch {}

    return res.status(200).json({
      ok: true,
      message: finalMessage,
    });
  } catch (e) {
    return res.status(500).json({ error: String(e.message || e) });
  }
}
