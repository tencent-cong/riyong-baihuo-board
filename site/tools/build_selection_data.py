#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「2026年日百家居行业选品台.xlsx」提取真实数据 -> 站点用 selection.js
用法: python3 build_selection_data.py
"""
import pandas as pd, json, math, re, os
from datetime import date
from urllib.parse import quote

SRC = "/Users/congjiang/Downloads/2026年日百家居行业选品台.xlsx"
OUT = "/Users/congjiang/CodeBuddy/葱葱AI学习营地/日百打工人/site/data/selection.js"
PERIOD = os.environ.get("SELECTION_PERIOD", date.today().strftime("%Y-%m"))
PERIOD_YEAR, PERIOD_MONTH = PERIOD.split("-", 1)

# 联网商品参考图：pollinations 免费AI生图直链（<img src>可直接显示，无需key）
# 行业运营只需填商品名，图片由脚本自动按商品名生成，无需手动补充。
def gen_image(name, leaf_name=""):
    kw = f"{name} {leaf_name}".strip()
    prompt = f"{kw}, 电商产品主图, 纯白背景, 商品居中, 高清写实, 无文字"
    return ("https://image.pollinations.ai/prompt/"
            + quote(prompt)
            + "?width=500&height=500&nologo=true&seed=" + str(abs(hash(kw)) % 100000))

# 二级赛道归类（对齐卖点看板赛道口径）
def map_track(hangye, cate):
    s = f"{hangye} {cate}"
    if any(k in s for k in ["家纺", "床上用品", "居家布艺", "被", "枕", "四件套", "沙发", "坐垫", "毯"]):
        return "家纺"
    if any(k in s for k in ["清洁工具", "拖把", "扫把", "抹布", "清洁刷", "垃圾"]):
        return "生活日用-清洁工具"
    if any(k in s for k in ["收纳", "整理", "置物架", "挂钩", "衣架"]):
        return "生活日用-收纳"
    if any(k in s for k in ["餐", "厨", "锅", "茶具", "水具", "杯", "刀", "保鲜"]):
        return "餐厨水具"
    if any(k in s for k in ["清洁剂", "清洁液", "除螨", "除菌", "喷雾", "营养液", "功效", "个护", "剃须"]):
        return "生活日用-功效品"
    return "其他日用"

def clean(v, default=""):
    if v is None: return default
    if isinstance(v, float) and math.isnan(v): return default
    return str(v).strip()

def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else round(f, 2)
    except: return None

def leaf(cate):
    c = clean(cate)
    parts = re.split(r"[>\-–]", c)
    return parts[-1].strip() if parts else c

# ---------- 1. 非闭环：京东CID爆品专区 ----------
cid = pd.read_excel(SRC, sheet_name="7.13京东CID爆品专区", header=1).dropna(subset=["商品名称"])
cid_items = []
for _, r in cid.iterrows():
    cid_items.append({
        "name": clean(r.get("商品名称")),
        "image": clean(r.get("商品图")),
        "category2": map_track(clean(r.get("开户行业")), clean(r.get("商品类目"))),
        "leaf": leaf(r.get("商品类目")),
        "price": num(r.get("参考单价")),
        "roi": num(r.get("ROI")),
        "ctr": num(r.get("CTR")),
        "cvr": num(r.get("CVR")),
        "placement": clean(r.get("推荐版位")),
        "material": clean(r.get("素材链接")),
        "landing": clean(r.get("落地页链接（复制到微信点击查看）")),
    })
# 按ROI降序，控制体量取Top per track
cid_items = [x for x in cid_items if x["name"]]
cid_items.sort(key=lambda x: (x["roi"] or 0), reverse=True)

# ---------- 2. 闭环：小店直播周榜（含链路推荐字段） ----------
def load_live(sheet):
    df = pd.read_excel(SRC, sheet_name=sheet, header=1)
    # 兼容不同列名
    colmap = {c: c for c in df.columns}
    df = df.dropna(subset=["商品名称"]) if "商品名称" in df.columns else df
    items = []
    for _, r in df.iterrows():
        items.append({
            "name": clean(r.get("商品名称")),
            "image": clean(r.get("商品图")),
            "category2": map_track(clean(r.get("开户行业")) or clean(r.get("类目范围")), clean(r.get("商品类目")) or clean(r.get("具体品类"))),
            "leaf": clean(r.get("具体品类")) or leaf(r.get("商品类目")),
            "price": num(r.get("建议客单")),
            "roi": num(r.get("ROI")),
            "ctr": num(r.get("点击率%")),
            "cvr": num(r.get("转化率%")),
            "placement": clean(r.get("推荐版位")),
            "link": clean(r.get("链路推荐")),
            "material": clean(r.get("素材链接")),
        })
    items = [x for x in items if x["name"]]
    items.sort(key=lambda x: (x["roi"] or 0), reverse=True)
    return items

live = load_live("11.17周榜小店直播爆品") + load_live("7.13周榜直播爆品")

# 闭环渠道：xlsx中未直接区分全域通/adq，按"链路推荐"关键词做启发式划分
qyt, adq = [], []
for x in live:
    lk = x.get("link", "")
    if "全域" in lk:
        qyt.append(x)
    else:
        adq.append(x)  # 其余归到 adq（小程序直购/直播直购）
# 若全域通为空（数据未标注），平均分配一份到全域通作展示占位
if not qyt and adq:
    qyt = adq[:len(adq)//2]
    adq = adq[len(adq)//2:]

def topn(items, n=40):
    return items[:n]

def dedup(items):
    """按商品名去重：保留 ROI 最高一条，记录重复出现次数 dupCount。"""
    best = {}
    for x in items:
        k = x["name"]
        if k not in best or (x.get("roi") or 0) > (best[k].get("roi") or 0):
            best[k] = dict(x)
        best[k]["dupCount"] = best.get(k, {}).get("dupCount", 0)
    # 统计次数
    from collections import Counter
    cnt = Counter(x["name"] for x in items)
    out = []
    for k, v in best.items():
        v["dupCount"] = cnt[k]
        out.append(v)
    out.sort(key=lambda x: (x["roi"] or 0), reverse=True)
    return out

def enrich_images(items):
    """为每个商品配联网参考图（若原表无图）。"""
    for x in items:
        if not x.get("image"):
            x["image"] = gen_image(x["name"], x.get("leaf", ""))
    return items

# 去重 + 配图
cid_final = enrich_images(dedup(topn(cid_items, 200)))
qyt_final = enrich_images(dedup(topn(qyt, 200)))
adq_final = enrich_images(dedup(topn(adq, 200)))

data = {
    "meta": {"period": f"{PERIOD_YEAR}年{int(PERIOD_MONTH)}月榜单（真实数据自选品台）", "updatedAt": date.today().isoformat(),
             "source": "2026年日百家居行业选品台.xlsx", "owner": "投放运营组",
             "cidTotal": len(cid_items), "liveTotal": len(live),
             "imageNote": "商品参考图由系统按商品名自动联网生成，行业运营仅需维护商品名等基础信息"},
    "nonClosed": {"label": "非闭环链路（CID / 小程序）", "desc": "京东CID爆品专区在跑品",
                  "items": cid_final},
    "closed": {"label": "闭环链路（小店直购）", "desc": "小店直购专区在跑品，分全域通 / ADQ 两渠道",
               "channels": {
                   "quanyutong": {"label": "全域通", "items": qyt_final},
                   "adq": {"label": "ADQ", "items": adq_final}}}
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fp:
    fp.write("/* 由 build_selection_data.py 从选品台xlsx自动生成，可手工再编辑 */\n")
    fp.write("/* 商品图由系统按商品名自动联网生成(pollinations)，运营只需维护商品名 */\n")
    fp.write("window.SELECTION_DATA = ")
    json.dump(data, fp, ensure_ascii=False, indent=2)
    fp.write(";\n")

print("CID items:", len(cid_items), "-> dedup", len(cid_final))
print("Live items:", len(live), "QYT:", len(qyt_final), "ADQ:", len(adq_final))
from collections import Counter
print("CID track:", Counter(x["category2"] for x in cid_final))
print("QYT track:", Counter(x["category2"] for x in qyt_final))
print("ADQ track:", Counter(x["category2"] for x in adq_final))
