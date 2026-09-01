#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「商品选品表格模板-CID」xlsx 更新 selection.js 的非闭环(CID)链路选品。
- 有投流数据(消耗/ROI/CTR/CVR 任一非空)的商品 -> 归入 8 个标准赛道
- 两率(CTR/CVR)无数据的商品 -> 归入「外部趋势机会品推荐」（大盘趋势品）
- 单个标准赛道最多 10 个（按消耗降序），趋势品补足至链路 60 个 quota
用法: python3 tools/update_cid_selection.py <xlsx路径> [期次标签]
"""
import pandas as pd, json, math, re, sys, os
from datetime import date
from urllib.parse import quote

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEL_JS = os.path.join(REPO, "site", "data", "selection.js")
ASSETS_JS = os.path.join(REPO, "site", "data", "product-assets.js")

SRC = sys.argv[1]
PERIOD = sys.argv[2] if len(sys.argv) > 2 else "2026年8月榜单 · 0828期（真实数据自选品台）"

QUOTA_TOTAL = 60
QUOTA_PER_TRACK = 10
TREND_CAT = "外部趋势机会品推荐"

def clean(v, default=""):
    if v is None: return default
    if isinstance(v, float) and math.isnan(v): return default
    return str(v).strip()

def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else round(f, 2)
    except Exception:
        return None

def leaf(cate):
    c = clean(cate)
    parts = re.split(r"[＞>\-–]", c)
    return parts[-1].strip() if parts else c

def map_track(cate, name=""):
    s = f"{cate} {name}"
    if any(k in s for k in ["家居饰品", "装饰摆件", "装饰画", "挂画", "工艺品", "摆件", "贴纸", "字画"]):
        return "家居家纺-家居工艺品"
    if any(k in s for k in ["家纺", "床上用品", "被", "枕", "四件套", "毯", "凉席", "床垫", "蚊帐"]):
        return "家居家纺-家纺"
    if any(k in s for k in ["餐饮用具", "餐具", "水具", "杯", "碗", "筷", "碟", "壶", "保温", "吸管"]):
        return "餐厨水具-餐具水具"
    if any(k in s for k in ["厨房", "烹饪", "锅", "刀", "砧板", "菜板", "保鲜", "蒸", "勺", "铲"]):
        return "餐厨水具-厨具"
    if any(k in s for k in ["清洁工具", "拖把", "扫把", "抹布", "清洁刷", "垃圾", "擦窗", "掸"]):
        return "生活日用-清洁工具"
    if any(k in s for k in ["收纳", "整理", "置物架", "挂钩", "衣架", "压缩袋", "储物"]):
        return "生活日用-收纳用品"
    if any(k in s for k in ["除螨", "除菌", "除臭", "清洁剂", "喷雾", "足贴", "膏", "医药", "洗护", "驱蚊", "防水贴", "功效"]):
        return "生活日用-功效品"
    return "生活日用-其他"

def gen_image(name, leaf_name=""):
    kw = f"{name} {leaf_name}".strip()
    prompt = f"{kw}, 电商产品主图, 纯白背景, 商品居中, 高清写实, 无文字"
    return ("https://image.pollinations.ai/prompt/" + quote(prompt)
            + "?width=500&height=500&nologo=true&seed=" + str(abs(hash(kw)) % 100000))

# 本地商品图对照表（按商品名匹配）
with open(ASSETS_JS, encoding="utf-8") as f:
    m = re.search(r'"images":({.*?})[,}]', f.read(), re.S)
local_images = set(json.loads(m.group(1)).keys()) if m else set()

df = pd.read_excel(SRC).dropna(subset=["商品名称"])
metric_items, trend_items, seen = [], [], set()

def build(r, is_trend):
    name = clean(r.get("商品名称"))
    lf = leaf(r.get("商品类目"))
    item = {
        "name": name,
        "industry": clean(r.get("开户行业")) or "居家日用",
        "image": "",  # 统一走 product-assets.js 图库按商品名匹配；未命中另行补图，禁用AI随机生图
        "leaf": clean(r.get("商品类目")),
        "price": num(r.get("参考单价")),
        "spend": num(r.get("消耗(元)")),
        "roi": num(r.get("ROI")),
        "ctr": num(r.get("CTR")),
        "cvr": num(r.get("CVR")),
        "placement": clean(r.get("推荐版位")),
        "link": "",
        "material": clean(r.get("素材链接")),
        "landing": clean(r.get("落地页链接（复制到微信点击查看）")),
        "createdAt": clean(r.get("创建日期")),
        "category2": TREND_CAT if is_trend else map_track(clean(r.get("商品类目")), name),
        "dupCount": 1,
    }
    if is_trend:
        item["trend"] = True
    return item

for _, r in df.iterrows():
    has_metric = any(num(r.get(c)) is not None for c in ["CTR", "CVR"])  # 两率有数据才算站内投流品
    (metric_items if has_metric else trend_items).append(build(r, not has_metric))

# 去重：投流品按消耗保留最高；趋势品保留首条；趋势品与投流品重名时剔除
best = {}
for x in metric_items:
    k = x["name"]
    if k not in best or (x.get("spend") or 0) > (best[k].get("spend") or 0):
        best[k] = x
metric_items = sorted(best.values(), key=lambda x: (x.get("spend") or 0), reverse=True)
seen = set(best.keys())
trend_dedup = []
for x in trend_items:
    if x["name"] not in seen:
        seen.add(x["name"])
        trend_dedup.append(x)

# 单赛道最多 10 个
by_track, picked = {}, []
for x in metric_items:
    arr = by_track.setdefault(x["category2"], [])
    if len(arr) < QUOTA_PER_TRACK:
        arr.append(x)
        picked.append(x)
# 趋势品补足至 60
remain = max(0, QUOTA_TOTAL - len(picked))
final_items = picked + trend_dedup[:remain]

# 写回 selection.js（仅替换 nonClosed 与 meta 期次信息，closed/cycles 原样保留）
with open(SEL_JS, encoding="utf-8") as f:
    src = f.read()
data = json.loads(src[src.index("{"):].rstrip().rstrip(";"))
data["meta"]["period"] = PERIOD
data["meta"]["updatedAt"] = date.today().isoformat()
data["meta"]["source"] = os.path.basename(SRC)
data["meta"]["cidTotal"] = len(df)
data["nonClosed"]["items"] = final_items
data["nonClosed"]["desc"] = "京东CID爆品专区在跑品 + 大盘外部趋势机会品"

with open(SEL_JS, "w", encoding="utf-8") as fp:
    fp.write("/* 由在线上传+审核后端自动写入 */\n")
    fp.write("/* 商品图优先使用运营商品图库；页面按商品名自动匹配本地静态资源 */\n")
    fp.write("window.SELECTION_DATA = ")
    json.dump(data, fp, ensure_ascii=False, indent=2)
    fp.write(";\n")

from collections import Counter
print("总行数:", len(df), "| 投流品(去重):", len(metric_items), "| 趋势品(去重):", len(trend_dedup))
print("最终上架:", len(final_items), "= 投流", len(picked), "+ 趋势", len(final_items) - len(picked))
print("赛道分布:", dict(Counter(x["category2"] for x in final_items)))
miss=[x["name"] for x in final_items if x["name"] not in local_images]
print("图库未命中(需补图):", len(miss), miss)
