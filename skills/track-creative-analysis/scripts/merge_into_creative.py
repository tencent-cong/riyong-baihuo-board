#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【AI 直写核心】把一个赛道的结构化 JSON 直接合并进 site/data/creative.js。

设计目标（对应「AI 直接写入 creative.js」的自动化路线）：
  赛道同学把原始数据交给 AI → AI 加工成 track JSON → 本脚本把它原地写进 creative.js
  → 本地刷新 / 推送即在线更新。全程无需人工组装、无需 Excel。

核心安全策略（与 sync_creative_from_excel.py 完全一致）：
  以现有 creative.js 为基底，【按赛道 name 匹配】只覆盖 skill 负责的字段
  （metrics / sellingWords / painWords / sellingContext / scripts / keyPoints），
  网页侧维护的字段（topMaterials / videoUrl / frames / golden5 等）【原样深度保留，绝不误删】。
  找不到同名赛道则【新增】一个赛道对象。

用法:
    # 合并单个赛道 JSON（相对/绝对路径均可）
    python3 scripts/merge_into_creative.py input.json
    # 合并 inbox/ 目录下所有 *.track.json
    python3 scripts/merge_into_creative.py --inbox
    # 合并后顺手推送到 GitHub（在线看板随之更新）
    python3 scripts/merge_into_creative.py input.json --push

input.json 结构见 references/schema.md 末尾（与 build_track_excel.py 完全同构）。
"""
import re, json, os, sys, subprocess, datetime, glob

# ---------- 路径推断：自动定位项目里的 site/data/creative.js ----------
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../track-creative-analysis
INBOX_DIR = os.path.join(SKILL_DIR, "inbox")


def find_creative_js():
    """从 skill 位置向上找项目根，再定位 site/data/creative.js。兼容 skill 放在项目内的情形。"""
    d = SKILL_DIR
    for _ in range(6):
        cand = os.path.join(d, "site", "data", "creative.js")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    # 兜底：环境变量指定
    env = os.environ.get("CREATIVE_JS")
    if env and os.path.exists(env):
        return env
    return None


# skill 负责的字段（只覆盖这些；其余网页侧字段保留）
OWNED_FIELDS = ["metrics", "sellingWords", "painWords",
                "sellingContext", "scripts", "keyPoints"]

VALID_TRACKS = {"家纺", "生活日用-清洁工具", "生活日用-功效品",
                "生活日用-收纳", "餐厨水具"}


def load_current(creative_js):
    """把 creative.js 里的 window.CREATIVE_DATA 解析成 python dict。"""
    txt = open(creative_js, encoding="utf-8").read()
    m = re.search(r"window\.CREATIVE_DATA\s*=\s*(\{.*\});", txt, re.S)
    if not m:
        raise RuntimeError("creative.js 里找不到 window.CREATIVE_DATA = {...};")
    body = m.group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)   # 去块注释
    body = re.sub(r"//.*", "", body)                     # 去行注释
    body = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', body)  # 键补引号
    body = re.sub(r",(\s*[}\]])", r"\1", body)           # 去尾逗号
    return json.loads(body)


def dump_js(data):
    lines = [
        "/* 卖点 & 创意分析数据层（Creative Board） */",
        "/* 由 track-creative-analysis skill 的 merge_into_creative.py 自动写入 */",
        "/* 赛道同学用 AI 产出后直写本文件；topMaterials 等网页侧字段自动保留 */",
        "window.CREATIVE_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";",
        "",
    ]
    return "\n".join(lines)


def merge_track(data, track):
    """把一个 track（dict）按 name 合并进 data['tracks']，返回 ('update'|'add', name)。"""
    name = (track.get("name") or "").strip()
    if not name:
        raise ValueError("track JSON 缺少 name 字段（赛道名）")

    # 只取 skill 负责的字段，避免脏字段污染
    payload = {k: track[k] for k in OWNED_FIELDS if k in track and track[k] not in (None, [], {})}

    for t in data.get("tracks", []):
        if t.get("name") == name:
            t.update(payload)                 # 深度保留：只覆盖 payload 里的键
            t["uploadedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if "owner" in track and track["owner"]:
                t["owner"] = track["owner"]
            return ("update", name)

    # 没有同名赛道 → 新增
    new_t = {"name": name, "uploadedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    key = track.get("key")
    if key:
        new_t["key"] = key
    if track.get("owner"):
        new_t["owner"] = track["owner"]
    new_t.update(payload)
    data.setdefault("tracks", []).append(new_t)
    return ("add", name)


def collect_inputs(args):
    """把命令行参数归一化为一批 track JSON 文件路径。"""
    if "--inbox" in args or not [a for a in args if not a.startswith("--")]:
        # 默认从 inbox/ 收 *.track.json 与 *.json
        if os.path.isdir(INBOX_DIR):
            files = sorted(glob.glob(os.path.join(INBOX_DIR, "*.json")))
            return [f for f in files if not os.path.basename(f).startswith("~")]
        return []
    files = []
    for a in args:
        if a.startswith("--"):
            continue
        if os.path.isdir(a):
            files.extend(sorted(glob.glob(os.path.join(a, "*.json"))))
        else:
            files.append(a)
    return files


def main():
    args = sys.argv[1:]
    do_push = "--push" in args

    creative_js = find_creative_js()
    if not creative_js:
        print("✗ 找不到 site/data/creative.js。请确认 skill 位于项目内，"
              "或设置环境变量 CREATIVE_JS 指向该文件。")
        sys.exit(1)
    proj_root = os.path.dirname(os.path.dirname(os.path.dirname(creative_js)))  # 项目根

    files = collect_inputs(args)
    files = [f for f in files if os.path.exists(f)]
    if not files:
        print("没有可合并的 track JSON。用法：")
        print("  python3 scripts/merge_into_creative.py input.json")
        print("  python3 scripts/merge_into_creative.py --inbox   # 收 inbox/ 下所有 *.json")
        sys.exit(1)

    data = load_current(creative_js)

    results = []
    for f in files:
        track = json.load(open(f, encoding="utf-8"))
        name = (track.get("name") or "").strip()
        if name and name not in VALID_TRACKS:
            print(f"  ⚠ 赛道名『{name}』不在标准列表 {sorted(VALID_TRACKS)}，将作为新赛道加入。")
        action, nm = merge_track(data, track)
        results.append((action, nm, os.path.basename(f)))
        print(f"  {'覆盖' if action == 'update' else '新增'}赛道『{nm}』 ← {os.path.basename(f)}")

    data.setdefault("meta", {})["dataUpdatedAt"] = datetime.date.today().isoformat()
    data["meta"].pop("updatedAt", None)
    open(creative_js, "w", encoding="utf-8").write(dump_js(data))
    print("✓ 已写入:", creative_js)
    print("✓ 涉及赛道:", sorted({nm for _, nm, _ in results}))

    if do_push:
        try:
            subprocess.run(["git", "-C", proj_root, "add", "-A"], check=True)
            subprocess.run(["git", "-C", proj_root,
                            "-c", "user.email=cccccccoingjiang77@users.noreply.github.com",
                            "-c", "user.name=cccccccoingjiang77",
                            "commit", "-m",
                            f"data: 更新赛道创意分析（{datetime.date.today()}）"], check=True)
            subprocess.run(["git", "-C", proj_root, "push"], check=True)
            print("✓ 已推送到 GitHub，在线看板将随之更新。")
        except subprocess.CalledProcessError as e:
            print("✗ 推送失败（可能无变更或需检查网络/凭证）:", e)


if __name__ == "__main__":
    main()
