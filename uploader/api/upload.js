// POST /api/upload
// body: { token, trackName, notes, filename, fileBase64, compression? }
// 流程：解析 Excel / CSV → 调 AI 产出 track JSON → 直接合并上线（跳过审批）
import * as XLSX from "xlsx";
import { gunzipSync } from "node:zlib";
import {
  ENV, ghGetFile, ghPutFile, callAI, extractJson, setCors, readJsonBody, checkToken,
  parseCreative, dumpCreative, mergeTrack, ghDispatchFrameWorkflow,
} from "./_lib.js";
import { SYSTEM_PROMPT, buildUserPrompt } from "./_prompt.js";

// 把 Excel / CSV 全量扫描后压缩成分析摘要（避免只截取文件开头）
const FIELD_ALIASES = {
  customer: ["客户简称", "客户名称"],
  chain: ["商品消费链路", "消费链路"],
  dpaName: ["DPA商品名称", "dpa商品名称"],
  spuId: ["SPUid(及名称)", "SPUID(及名称)", "spuid"],
  spuName: ["SPUid(及名称)(翻译后)", "SPUID(及名称)(翻译后)", "SPU名称"],
  copy: ["创意文案", "素材文案", "口播文案", "字幕"],
  // 素材创意 URL 统一以源文件的翻译后预览字段为准，避免误用其他 URL 列。
  materialUrl: ["素材MD5示意(预览)(翻译后)"],
  duration: ["时长", "素材时长", "视频时长"],
  spend: ["消耗(元)", "消耗", "总消耗"],
};

const TRACK_KEYS = {
  "生活日用-功效品": "daily-effect",
  "生活日用-清洁工具": "clean",
  "生活日用-收纳用品": "daily-storage",
  "生活日用-其他": "daily-other",
  "餐厨水具-厨具": "kitchen-cookware",
  "餐厨水具-餐具水具": "kitchen-tableware",
  "家居家纺-家纺": "home-textile",
  "家居家纺-家居工艺品": "home-crafts",
};

function pick(row, names) {
  for (const name of names) {
    const value = row[name];
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return "";
}

function num(value) {
  const n = Number(String(value || "").replace(/[,%￥¥,]/g, ""));
  return Number.isFinite(n) ? n : 0;
}

// 素材预览 URL 是否有效：空、占位「空」「-」「/」或非 http(s) 链接都视为无效
// 无效行不进入 Top 素材候选，页面不再出现空图位的 missing_source 卡片
function hasValidMaterialUrl(value) {
  const url = String(value || "").trim();
  if (!url) return false;
  if (/^(空|无|null|undefined|-|—|\/|N\/A)$/i.test(url)) return false;
  return /^https?:\/\/\S+$/i.test(url);
}

function clip(value, max = 220) {
  const text = String(value || "").trim();
  return text.length > max ? text.slice(0, max) + "…" : text;
}

function cleanProductName(value) {
  let text = String(value || "").trim();
  text = text.replace(/^[^·|｜]{2,40}(?:有限责任公司|股份有限公司|有限公司|信息科技|科技公司|电子商务|商贸公司|贸易公司|日用品公司|商行|旗舰店|专营店|专卖店)[·|｜\s:：-]*/g, "");
  text = text.replace(/(?:有限责任公司|股份有限公司|有限公司|信息科技有限公司|电子商务有限公司|商贸有限公司|贸易有限公司|日用品有限公司|商行)$/g, "");
  if (text.includes("/")) text = text.split("/").pop();
  text = text.replace(/^[A-Za-z][A-Za-z0-9 ._-]{1,24}\s*/g, "");
  text = text.replace(/^(?:品牌|官方|正品|新款|爆款|网红|家用|专用|多功能|高档|高端)+/g, "");
  text = text.replace(/\s*(?:\d+(?:\.\d+)?(?:cm|mm|ml|L|g|kg|只|个|件|支|片|包|盒|套)|【.*?】|\[.*?\]).*$/gi, "");
  return text.trim() || "未识别商品";
}

function displayProductName(row) {
  const translated = pick(row, FIELD_ALIASES.spuName);
  const dpa = pick(row, FIELD_ALIASES.dpaName);
  const invalidSource = /有限责任公司|股份有限公司|有限公司|信息科技|科技公司|电子商务|商贸|贸易|商行|旗舰店|专营店|专卖店/;
  const genericName = /^(清洁工具|生活日用|功效品|收纳|餐厨水具|家纺|浴室用品|其他|未知|未分类|未识别商品)$/;
  const translatedName = cleanProductName(translated);
  if (translated && !invalidSource.test(translated) && !genericName.test(translatedName)) return translatedName;
  return cleanProductName(dpa || translated);
}

function excelToText(buf) {
  const wb = XLSX.read(buf, { type: "buffer" });
  const sections = [];
  const sourceRows = new Map();
  for (const [sheetIndex, sheetName] of wb.SheetNames.entries()) {
    const ws = wb.Sheets[sheetName];
    const rows = XLSX.utils.sheet_to_json(ws, { defval: "", raw: false });
    if (!rows.length) continue;

    const productMap = new Map();
    for (const row of rows) {
      const name = displayProductName(row);
      const spuId = pick(row, FIELD_ALIASES.spuId);
      if (name === "未识别商品" && !spuId) continue;
      const key = spuId || name;
      const prev = productMap.get(key) || { name, spuId, spend: 0, rows: 0 };
      prev.spend += num(pick(row, FIELD_ALIASES.spend));
      prev.rows += 1;
      productMap.set(key, prev);
    }

    const overall = rows.filter(row => pick(row, FIELD_ALIASES.customer) === "整体").slice(0, 10);
    const topProducts = [...productMap.values()].sort((a, b) => b.spend - a.spend).slice(0, 60);
    // 只有带有效素材预览 URL 的行才能进 Top 素材候选：无预览的行直接剔除，
    // 由后续消耗更低但有预览的行按消耗降序补位。
    const sortedRows = [...rows]
      .filter(row => displayProductName(row) !== "未识别商品")
      .filter(row => hasValidMaterialUrl(pick(row, FIELD_ALIASES.materialUrl)))
      .sort((a, b) => num(pick(b, FIELD_ALIASES.spend)) - num(pick(a, FIELD_ALIASES.spend)));
    const selected = [];
    const seenProducts = new Set();
    for (const row of sortedRows) {
      const product = displayProductName(row);
      if (seenProducts.has(product)) continue;
      selected.push(row);
      seenProducts.add(product);
      if (selected.length >= 60) break;
    }
    const selectedSet = new Set(selected);
    for (const row of sortedRows) {
      if (selected.length >= 100) break;
      if (!selectedSet.has(row)) selected.push(row);
    }
    const topRows = selected;

    const compactRows = topRows.map((row, rowIndex) => {
      const sourceId = `S${sheetIndex + 1}R${rowIndex + 1}`;
      const compact = {
        sourceId,
        客户: pick(row, FIELD_ALIASES.customer),
        链路: pick(row, FIELD_ALIASES.chain),
        商品名称: clip(displayProductName(row), 80),
        SPUid: pick(row, FIELD_ALIASES.spuId),
        DPA商品名称: clip(pick(row, FIELD_ALIASES.dpaName), 120),
        创意文案: clip(pick(row, FIELD_ALIASES.copy), 260),
        素材URL: pick(row, FIELD_ALIASES.materialUrl),
        时长: pick(row, FIELD_ALIASES.duration),
        消耗: pick(row, FIELD_ALIASES.spend),
        CTR: row["ctr(%)"] || row.CTR || "",
        CVR: row["浅层cvr(%)"] || row.CVR || "",
        CPM: row["竞价CPM(元)"] || row.CPM || "",
        三秒快滑率: row["3s快滑率(%)"] || "",
      };
      sourceRows.set(sourceId, compact);
      return compact;
    });

    sections.push([
      `### Sheet: ${sheetName}`,
      `全量扫描行数: ${rows.length}；商品聚合数: ${productMap.size}`,
      `字段: ${Object.keys(rows[0]).join(" | ")}`,
      `客户简称=整体的指标行（赛道均值优先采用）:\n${JSON.stringify(overall)}`,
      `按全量数据聚合的高消耗商品（名称优先SPU翻译名，其次DPA商品名）:\n${JSON.stringify(topProducts)}`,
      `高消耗代表素材与文案（从全部行排序抽取）:\n${JSON.stringify(compactRows)}`,
    ].join("\n"));
  }
  return { text: sections.join("\n\n"), sourceRows };
}

function hydrateMaterialFromSource(material, sourceRows) {
  let source = sourceRows.get(String(material.sourceId || ""));
  if (!source) {
    const customer = String(material.customer || "").trim();
    const product = cleanProductName(material.product || material.title);
    source = [...sourceRows.values()].find(row =>
      (!customer || row.客户 === customer) && cleanProductName(row.商品名称) === product
    );
  }
  if (!source) return { ...material, videoUrl: "", frames: [] };
  return {
    ...material,
    sourceId: source.sourceId,
    customer: source.客户,
    chain: source.链路,
    spend: num(source.消耗),
    ctr: num(source.CTR),
    cvr: num(source.CVR),
    cpm: num(source.CPM),
    duration: source.时长 || material.duration || "未提供",
    videoUrl: hasValidMaterialUrl(source.素材URL) ? source.素材URL : "",
    frames: [],
  };
}

// 把候选行直接转成 Top 素材条目，用于补位（无五段式，页面照常展示素材与指标）
function materialFromSourceRow(source) {
  const product = cleanProductName(source.商品名称);
  return {
    sourceId: source.sourceId,
    title: product,
    product,
    customer: source.客户,
    chain: source.链路,
    tag: "高消耗代表素材",
    spend: num(source.消耗),
    ctr: num(source.CTR),
    cvr: num(source.CVR),
    cpm: num(source.CPM),
    duration: source.时长 || "未提供",
    videoUrl: source.素材URL,
    frames: [],
  };
}

// 剔除无有效预览 URL 的素材后，从候选池按消耗降序补足到目标条数
function backfillMaterials(materials, sourceRows, target) {
  const used = new Set(materials.map(item => String(item.sourceId || "")));
  const usedProducts = new Set(materials.map(item => cleanProductName(item.product || item.title)));
  const pool = [...sourceRows.values()]
    .filter(row => hasValidMaterialUrl(row.素材URL))
    .filter(row => !used.has(String(row.sourceId)))
    .sort((a, b) => num(b.消耗) - num(a.消耗));
  const result = [...materials];
  // 先补不同商品，保持商品多样性；仍不足时才允许重复商品
  for (const pass of [true, false]) {
    for (const row of pool) {
      if (result.length >= target) break;
      if (used.has(String(row.sourceId))) continue;
      const product = cleanProductName(row.商品名称);
      if (pass && usedProducts.has(product)) continue;
      result.push(materialFromSourceRow(row));
      used.add(String(row.sourceId));
      usedProducts.add(product);
    }
    if (result.length >= target) break;
  }
  return result;
}

export default async function handler(req, res) {
  setCors(res);
  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") return res.status(405).json({ error: "只支持 POST" });

  try {
    const body = await readJsonBody(req);
    const { token, trackName, notes, filename, fileBase64, compression } = body;

    if (!checkToken(token, ENV.UPLOAD_TOKEN))
      return res.status(401).json({ error: "上传口令错误" });
    if (!trackName || !trackName.trim())
      return res.status(400).json({ error: "请填写赛道名" });
    if (!fileBase64)
      return res.status(400).json({ error: "缺少 Excel 文件" });

    // 1) 解析 Excel / CSV（大文件由浏览器先 gzip，降低 Vercel 请求体体积）
    const encoded = Buffer.from(fileBase64, "base64");
    const buf = compression === "gzip" ? gunzipSync(encoded) : encoded;
    const analysis = excelToText(buf);
    if (!analysis.text.trim())
      return res.status(400).json({ error: "Excel 内容为空或无法解析" });

    // 2) 调 AI 产出 track JSON；若输出超长被截断，自动改用精简体量再试一次
    const name0 = trackName.trim();
    let track;
    try {
      const aiRaw = await callAI(SYSTEM_PROMPT, buildUserPrompt(name0, analysis.text, notes));
      track = extractJson(aiRaw);
    } catch (firstError) {
      const truncated = firstError.code === "AI_TRUNCATED" || /截断|可解析的 JSON/.test(String(firstError.message));
      if (!truncated) throw firstError;
      console.warn("AI 首次输出不完整，改用精简模式重试:", firstError.message);
      const aiRetry = await callAI(SYSTEM_PROMPT, buildUserPrompt(name0, analysis.text, notes, { compact: true }));
      try {
        track = extractJson(aiRetry);
      } catch (secondError) {
        throw new Error(`AI 两次输出均不完整。首次：${firstError.message}；精简重试：${secondError.message}`);
      }
    }
    track.name = trackName.trim(); // 强制对齐赛道名
    if (TRACK_KEYS[track.name]) track.key = TRACK_KEYS[track.name];
    if (Array.isArray(track.topMaterials)) {
      const wanted = Math.max(10, track.topMaterials.length);
      const hydratedList = track.topMaterials
        .map(material => {
          const hydrated = hydrateMaterialFromSource(material, analysis.sourceRows);
          const cleanedProduct = cleanProductName(hydrated.product);
          const product = cleanedProduct === "未识别商品"
            ? cleanProductName(hydrated.title)
            : cleanedProduct;
          const rawTitle = String(hydrated.title || "").replace(String(hydrated.product || ""), "").replace(/^[·|｜\s:：-]+/, "");
          return {
            ...hydrated,
            product,
            title: rawTitle ? `${product}·${rawTitle}` : product,
            frames: [],
          };
        })
        // 素材预览为空的条目直接剔除，不在页面留空图位
        .filter(item => hasValidMaterialUrl(item.videoUrl));
      // 剔除后若不足条数，从候选池按消耗降序补位，再统一按消耗降序排名
      track.topMaterials = backfillMaterials(hydratedList, analysis.sourceRows, wanted)
        .sort((a, b) => num(b.spend) - num(a.spend))
        .map((material, index) => ({ ...material, rank: index + 1 }));
    }

    // 3) 直接合并到 creative.js 上线（跳过审批）
    const cur = await ghGetFile(ENV.CREATIVE_PATH);
    if (!cur) return res.status(500).json({ error: "线上 creative.js 读取失败" });
    const data = parseCreative(cur.text);
    const { action, name } = mergeTrack(data, track);
    data.meta = data.meta || {};
    data.meta.dataUpdatedAt = new Date().toISOString().slice(0, 10);
    delete data.meta.updatedAt;
    const newText = dumpCreative(data);
    await ghPutFile(
      ENV.CREATIVE_PATH, newText,
      `data: 直传上线·${action === "update" ? "更新" : "新增"}赛道「${name}」创意分析`,
      cur.sha
    );

    const materials = Array.isArray(track.topMaterials) ? track.topMaterials : [];
    const framesQueued = materials.some(item => item.videoUrl && item.videoUrl !== "空");
    let frameMessage = "；未识别到有效素材URL，关键帧暂不生成";
    let framesBackground = false;
    if (framesQueued) {
      try {
        await ghDispatchFrameWorkflow(name);
        framesBackground = true;
        frameMessage = "；关键帧已排队自动生成，通常几分钟后展示。本次只需上传一次，请勿重复提交";
      } catch (error) {
        console.error("关键帧后台任务触发失败", error);
        frameMessage = "；数据已上线，但关键帧任务未启动，系统定时任务会自动补齐，无需重复上传";
      }
    }
    return res.status(200).json({
      ok: true,
      id: null,
      track,
      framesQueued,
      framesBackground,
      message: `✅ 已${action === "update" ? "更新" : "新增"}赛道「${name}」创意分析并直接上线${frameMessage}`,
    });
  } catch (e) {
    return res.status(500).json({ error: String(e.message || e) });
  }
}