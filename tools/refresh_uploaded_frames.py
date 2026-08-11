#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CREATIVE_PATH = ROOT / "site/data/creative.js"
FRAME_ROOT = ROOT / "site/assets/frames"
USER_AGENT = "riri-baihuo-frame-worker/1.0"


def parse_creative(path=CREATIVE_PATH):
    text = path.read_text(encoding="utf-8")
    match = re.search(r"window\.CREATIVE_DATA\s*=\s*(\{[\s\S]*\});", text)
    if not match:
        raise RuntimeError("creative.js 中找不到 CREATIVE_DATA")
    return json.loads(match.group(1))


def dump_creative(data):
    return "\n".join([
        "/* 卖点 & 创意分析数据层（Creative Board） */",
        "/* 由在线上传后端自动写入；关键帧由后台任务生成并校验后替换 */",
        "window.CREATIVE_DATA = " + json.dumps(data, ensure_ascii=False, indent=2) + ";",
        "",
    ])


def find_track(data, name):
    track = next((item for item in data.get("tracks", []) if item.get("name") == name), None)
    if not track:
        raise RuntimeError(f"找不到赛道：{name}")
    return track


def stable_material_id(track_name, material):
    raw = "\n".join([
        track_name,
        str(material.get("videoUrl") or ""),
        str(material.get("sourceId") or ""),
        str(material.get("product") or material.get("title") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def download(url, destination):
    request = Request(url.replace("http://", "https://", 1), headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=180) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def probe_duration(source):
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(source),
    ]
    value = subprocess.check_output(command, text=True).strip()
    duration = float(value)
    if duration <= 0:
        raise RuntimeError("视频时长无效")
    return duration


def extract_video_frames(source, output_dir, duration):
    ratios = [0.06, 0.24, 0.43, 0.62, 0.82]
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, ratio in enumerate(ratios, 1):
        seconds = max(0.3, min(duration - 0.1, duration * ratio))
        destination = output_dir / f"frame-{index:02d}.jpg"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{seconds:.3f}", "-i", str(source), "-frames:v", "1",
            "-vf", "scale='min(480,iw)':-2", "-q:v", "3", str(destination),
        ]
        subprocess.run(command, check=True, timeout=180)
        if not destination.exists() or destination.stat().st_size < 1024:
            raise RuntimeError(f"第 {index} 帧生成失败")
        frames.append({"filename": destination.name, "time": f"{seconds:.1f}s"})
    return frames


def has_complete_local_frames(material):
    frames = material.get("frames") or []
    if len(frames) != 5:
        return False
    return all(
        frame.get("src") and not re.match(r"^https?://", frame["src"], re.I)
        and (ROOT / "site" / frame["src"]).is_file()
        for frame in frames
    )


def prepare(track_name, work_dir):
    data = parse_creative()
    track = find_track(data, track_name)
    work_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    failures = []
    skipped = 0
    for material in track.get("topMaterials", []):
        url = str(material.get("videoUrl") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            continue
        if has_complete_local_frames(material):
            skipped += 1
            print(f"SKIP ready {material.get('rank')}: {material.get('title')}")
            continue
        material_id = material.get("materialId") or stable_material_id(track_name, material)
        item_dir = work_dir / material_id
        item_dir.mkdir(parents=True, exist_ok=True)
        last_error = None
        for attempt in range(1, 4):
            try:
                suffix = Path(urlparse(url).path).suffix.lower()
                source = item_dir / ("source" + (suffix if suffix else ".bin"))
                source.unlink(missing_ok=True)
                download(url, source)
                duration = probe_duration(source)
                frames = extract_video_frames(source, item_dir, duration)
                jobs.append({
                    "materialId": material_id,
                    "videoUrl": url,
                    "duration": duration,
                    "frames": frames,
                })
                source.unlink(missing_ok=True)
                print(f"OK {material.get('rank')}: {material.get('title')}")
                last_error = None
                break
            except Exception as error:
                last_error = error
                print(f"RETRY {attempt}/3 {material.get('rank')}: {error}", file=sys.stderr)
        if last_error:
            failures.append({"materialId": material_id, "videoUrl": url, "error": str(last_error)})
            print(f"FAIL {material.get('rank')}: {last_error}", file=sys.stderr)
    manifest = {"trackName": track_name, "jobs": jobs, "failures": failures}
    (work_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        print(f"{len(failures)} 个素材处理失败，将保留其线上旧关键帧", file=sys.stderr)
    if failures and not jobs:
        raise RuntimeError("所有待处理素材均未能生成关键帧")
    if skipped and not jobs and not failures:
        print("所有素材关键帧均已就绪")


def apply(track_name, work_dir):
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("trackName") != track_name:
        raise RuntimeError("关键帧清单与赛道不匹配")
    data = parse_creative()
    track = find_track(data, track_name)
    materials = track.get("topMaterials", [])
    applied = 0
    for job in manifest.get("jobs", []):
        material = next((item for item in materials if (
            (item.get("materialId") or stable_material_id(track_name, item)) == job["materialId"]
            and str(item.get("videoUrl") or "") == job["videoUrl"]
        )), None)
        if not material:
            print(f"SKIP stale material {job['materialId']}")
            continue
        folder = FRAME_ROOT / f"upload-{job['materialId']}"
        folder.mkdir(parents=True, exist_ok=True)
        stored = []
        for frame in job["frames"]:
            source = work_dir / job["materialId"] / frame["filename"]
            destination = folder / frame["filename"]
            shutil.copy2(source, destination)
            stored.append({
                "src": destination.relative_to(ROOT / "site").as_posix(),
                "time": frame["time"],
            })
        material["materialId"] = job["materialId"]
        material["sourceType"] = "video"
        material["duration"] = f"约{round(job['duration'])}s"
        material["frames"] = stored
        material["frameStatus"] = "ready"
        material.pop("frameError", None)
        applied += 1
    for failure in manifest.get("failures", []):
        material = next((item for item in materials if (
            (item.get("materialId") or stable_material_id(track_name, item)) == failure["materialId"]
            and str(item.get("videoUrl") or "") == failure["videoUrl"]
        )), None)
        if material:
            material["materialId"] = failure["materialId"]
            material["frameStatus"] = "failed"
            material["frameError"] = failure["error"][:240]
    if applied:
        data.setdefault("meta", {})["updatedAt"] = __import__("datetime").date.today().isoformat()
        CREATIVE_PATH.write_text(dump_creative(data), encoding="utf-8")
    print(f"Applied {applied} material(s)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "apply"])
    parser.add_argument("--track", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.track, args.work_dir)
    else:
        apply(args.track, args.work_dir)


if __name__ == "__main__":
    main()
