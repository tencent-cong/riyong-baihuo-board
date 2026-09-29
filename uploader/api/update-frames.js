// POST /api/update-frames
// body: { token, trackName, rank, videoUrl, sourceType, duration, frames: [{src, time}] }
import { createHash } from "node:crypto";
import {
  ENV, ghGetFile, ghCommitFiles, parseCreative, dumpCreative,
  setCors, readJsonBody, checkToken,
} from "./_lib.js";

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function frameFolder(trackName, rank, videoUrl) {
  const trackHash = createHash("sha1").update(trackName).digest("hex").slice(0, 8);
  const videoHash = createHash("sha1").update(videoUrl).digest("hex").slice(0, 10);
  return `site/assets/frames/upload-${trackHash}-${String(rank).padStart(2, "0")}-${videoHash}`;
}

export default async function handler(req, res) {
  setCors(res);
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "只支持 POST" });

  try {
    const body = await readJsonBody(req);
    const { token, trackName, rank, videoUrl, duration, frames } = body;
    if (!checkToken(token, ENV.UPLOAD_TOKEN))
      return res.status(401).json({ error: "上传口令错误" });
    if (!trackName || !rank || !videoUrl || !Array.isArray(frames))
      return res.status(400).json({ error: "关键帧参数不完整" });
    const sourceType = body.sourceType === "image" ? "image" : "video";
    if (sourceType === "image") {
      if (frames.length !== 1 || !/^https?:\/\//i.test(String(frames[0]?.src || "")))
        return res.status(400).json({ error: "图片素材必须提供一张有效原图" });
    } else {
      if (frames.length !== 5)
        return res.status(400).json({ error: "视频素材必须提供 5 张关键帧" });
      if (frames.some(frame => !String(frame.src || "").startsWith("data:image/jpeg;base64,")))
        return res.status(400).json({ error: "视频关键帧必须是 JPEG 图片" });
    }

    const folder = frameFolder(trackName, rank, videoUrl);
    const imageFiles = sourceType === "video" ? frames.map((frame, index) => ({
      path: `${folder}/frame-${String(index + 1).padStart(2, "0")}.jpg`,
      content: frame.src.split(",")[1],
      encoding: "base64",
      time: frame.time,
    })) : [];

    for (let attempt = 1; attempt <= 4; attempt++) {
      const current = await ghGetFile(ENV.CREATIVE_PATH);
      if (!current) return res.status(500).json({ error: "线上 creative.js 读取失败" });
      const data = parseCreative(current.text);
      const track = (data.tracks || []).find(item => item.name === trackName);
      const material = track?.topMaterials?.find(item => Number(item.rank) === Number(rank));
      if (!material) return res.status(409).json({ error: "素材已被新的上传替换" });
      if (String(material.videoUrl || "") !== String(videoUrl))
        return res.status(409).json({ error: "素材URL已更新，本次旧截图已跳过" });

      material.frames = sourceType === "image"
        ? [{ src: frames[0].src, time: "" }]
        : imageFiles.map(file => ({
          src: file.path.replace(/^site\//, ""),
          time: file.time,
        }));
      material.sourceType = sourceType;
      if (sourceType === "video" && duration) material.duration = `约${Math.round(Number(duration))}s`;
      if (sourceType === "image") material.duration = "图片素材";
      data.meta = data.meta || {};
      data.meta.frameUpdatedAt = new Date().toISOString();

      const files = imageFiles.map(({ path, content, encoding }) => ({ path, content, encoding }));
      files.push({ path: ENV.CREATIVE_PATH, content: dumpCreative(data), encoding: "utf-8" });
      try {
        await ghCommitFiles(files, `frames: 更新「${trackName}」第${rank}条${sourceType === "image" ? "图片素材" : "视频关键帧"}`);
        return res.status(200).json({ ok: true, rank });
      } catch (error) {
        if (error.code !== "GH_REF_CONFLICT" || attempt === 4) throw error;
        await sleep(attempt * 700);
      }
    }
  } catch (error) {
    return res.status(500).json({ error: String(error.message || error) });
  }
}