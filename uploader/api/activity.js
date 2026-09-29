// GET /api/activity?token=ADMIN_TOKEN
// POST /api/activity  body: { token, activity }
import { ENV, ghGetFile, ghPutFile, setCors, readJsonBody, checkToken } from "./_lib.js";

const ACTIVITY_PATH = "site/data/activity.js";

function parseActivity(text) {
  const match = text.match(/window\.ACTIVITY_DATA\s*=\s*(\{[\s\S]*\});?/);
  if (!match) throw new Error("activity.js 格式不正确");
  return Function(`"use strict";return (${match[1]});`)();
}

function dumpActivity(activity) {
  return `window.ACTIVITY_DATA = ${JSON.stringify(activity, null, 2)};\n`;
}

function validateActivity(input) {
  const activity = {
    enabled: input?.enabled !== false,
    title: String(input?.title || "平台限时活动").trim(),
    image: String(input?.image || "").trim(),
    text: String(input?.text || "").trim(),
    link: String(input?.link || "").trim(),
    startAt: String(input?.startAt || "").trim(),
    endAt: String(input?.endAt || "").trim(),
    updatedAt: new Date().toISOString().slice(0, 10),
  };
  if (!activity.title) throw new Error("活动标题不能为空");
  if (!activity.image) throw new Error("Banner 图片地址不能为空");
  if (!activity.text) throw new Error("活动文案不能为空");
  if (!/^https:\/\//i.test(activity.link)) throw new Error("跳转链接必须是 HTTPS 地址");
  const start = new Date(activity.startAt);
  const end = new Date(activity.endAt);
  if (!activity.startAt || Number.isNaN(start.getTime())) throw new Error("活动开始时间无效");
  if (!activity.endAt || Number.isNaN(end.getTime())) throw new Error("活动结束时间无效");
  if (end <= start) throw new Error("活动结束时间必须晚于开始时间");
  return activity;
}

export default async function handler(req, res) {
  setCors(res);
  if (req.method === "OPTIONS") return res.status(200).end();

  try {
    if (req.method !== "GET" && req.method !== "POST")
      return res.status(405).json({ error: "只支持 GET / POST" });

    const body = req.method === "POST" ? await readJsonBody(req) : null;
    const token = req.method === "GET"
      ? (req.query?.token || req.headers["x-token"])
      : body?.token;
    if (!ENV.ADMIN_TOKEN)
      return res.status(503).json({ error: "管理员口令尚未配置，请先在 Vercel 设置 ADMIN_TOKEN" });
    if (!checkToken(token, ENV.ADMIN_TOKEN))
      return res.status(401).json({ error: "审核口令错误" });

    const current = await ghGetFile(ACTIVITY_PATH);
    if (req.method === "GET") {
      return res.status(200).json({
        ok: true,
        activity: current ? parseActivity(current.text) : null,
      });
    }

    const activity = validateActivity(body?.activity);
    await ghPutFile(
      ACTIVITY_PATH,
      dumpActivity(activity),
      `content: 管理员更新平台限时活动「${activity.title}」`,
      current?.sha
    );
    return res.status(200).json({ ok: true, activity, message: "活动配置已保存，首页将在几十秒后更新。" });
  } catch (e) {
    return res.status(500).json({ error: String(e.message || e) });
  }
}
