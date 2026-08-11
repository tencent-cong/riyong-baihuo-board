#!/usr/bin/env python3
import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
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
USER_AGENT = "riri-baihuo-frame-worker/2.0"
FRAME_RATIOS = (0.06, 0.24, 0.43, 0.62, 0.82)
MATERIAL_TIMEOUT = int(os.environ.get("FRAME_MATERIAL_TIMEOUT", "240"))
MAX_WORKERS = int(os.environ.get("FRAME_MAX_WORKERS", "3"))


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


def normalized_url(url):
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def run(command, timeout=MATERIAL_TIMEOUT):
    return subprocess.run(command, check=True, timeout=timeout, text=True, capture_output=True)


def probe_duration(source):
    result = run([
        "ffprobe", "-v", "error", "-rw_timeout", "30000000",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(source),
    ], timeout=45)
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise RuntimeError("视频时长无效")
    return duration


def extract_video_frames(source, output_dir, duration):
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, ratio in enumerate(FRAME_RATIOS, 1):
        seconds = max(0.3, min(duration - 0.1, duration * ratio))
        destination = output_dir / f"frame-{index:02d}.jpg"
        destination.unlink(missing_ok=True)
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-rw_timeout", "30000000", "-ss", f"{seconds:.3f}",
            "-i", str(source), "-frames:v", "1",
            "-vf", "scale='min(480,iw)':-2", "-q:v", "3", str(destination),
        ], timeout=60)
        if not destination.exists() or destination.stat().st_size < 1024:
            raise RuntimeError(f"第 {index} 帧生成失败")
        frames.append({"filename": destination.name, "time": f"{seconds:.1f}s"})
    return frames


def download(url, destination):
    run([
        "curl", "--fail", "--location", "--silent", "--show-error",
        "--connect-timeout", "15", "--max-time", "120",
        "--user-agent", USER_AGENT, "--output", str(destination), normalized_url(url),
    ], timeout=130)


def process_material(track_name, material, work_dir):
    url = str(material.get("videoUrl") or "").strip()
    material_id = material.get("materialId") or stable_material_id(track_name, material)
    item_dir = work_dir / material_id
    item_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    sources = [("range", normalized_url(url))]
    suffix = Path(urlparse(url).path).suffix.lower() or ".bin"
    local_source = item_dir / f"source{suffix}"
    for mode, source in sources:
        try:
            duration = probe_duration(source)
            frames = extract_video_frames(source, item_dir, duration)
            return {
                "ok": True, "materialId": material_id, "videoUrl": url,
                "duration": duration, "frames": frames, "mode": mode,
            }
        except Exception as error:
            errors.append(f"{mode}: {error}")
    try:
        local_source.unlink(missing_ok=True)
        download(url, local_source)
        duration = probe_duration(local_source)
        frames = extract_video_frames(local_source, item_dir, duration)
        local_source.unlink(missing_ok=True)
        return {
            "ok": True, "materialId": material_id, "videoUrl": url,
            "duration": duration, "frames": frames, "mode": "download",
        }
    except Exception as error:
        errors.append(f"download: {error}")
        local_source.unlink(missing_ok=True)
        return {
            "ok": False, "materialId": material_id, "videoUrl": url,
            "error": " | ".join(errors)[-500:],
        }


def has_complete_local_frames(material):
    frames = material.get("frames") or []
    if len(frames) != 5:
        return False
    return all(
        frame.get("src") and not re.match(r"^https?://", frame["src"], re.I)
        and (ROOT / "site" / frame["src"]).is_file()
        for frame in frames
    )


def pending_materials(track_name, track):
    result = []
    for material in track.get("topMaterials", []):
        url = str(material.get("videoUrl") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            continue
        if has_complete_local_frames(material):
            continue
        result.append(material)
    return result


def prepare(track_name, work_dir):
    data = parse_creative()
    if track_name:
        tracks = [find_track(data, track_name)]
    else:
        tracks = data.get("tracks", [])
    work_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        (track.get("name", ""), material)
        for track in tracks
        for material in pending_materials(track.get("name", ""), track)
    ]
    jobs = []
    failures = []
    if targets:
        workers = max(1, min(MAX_WORKERS, len(targets)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(process_material, name, material, work_dir): (name, material)
                for name, material in targets
            }
            for future in concurrent.futures.as_completed(futures):
                name, material = futures[future]
                try:
                    result = future.result(timeout=MATERIAL_TIMEOUT + 30)
                except Exception as error:
                    result = {
                        "ok": False,
                        "materialId": material.get("materialId") or stable_material_id(name, material),
                        "videoUrl": str(material.get("videoUrl") or ""),
                        "error": str(error),
                    }
                result["trackName"] = name
                if result.pop("ok"):
                    jobs.append(result)
                    print(f"OK {name} #{material.get('rank')} via {result.get('mode')}", flush=True)
                else:
                    failures.append(result)
                    print(f"FAIL {name} #{material.get('rank')}: {result['error']}", file=sys.stderr, flush=True)
    manifest = {"trackName": track_name, "jobs": jobs, "failures": failures}
    (work_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {len(jobs)} success, {len(failures)} failure, {len(targets)} target(s)")


def match_material(materials, track_name, item):
    return next((material for material in materials if (
        (material.get("materialId") or stable_material_id(track_name, material)) == item["materialId"]
        and str(material.get("videoUrl") or "") == item["videoUrl"]
    )), None)


def apply(track_name, work_dir):
    manifest = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("trackName", "") != track_name:
        raise RuntimeError("关键帧清单与处理范围不匹配")
    data = parse_creative()
    applied = 0
    marked_failed = 0
    for job in manifest.get("jobs", []):
        name = job["trackName"]
        track = find_track(data, name)
        material = match_material(track.get("topMaterials", []), name, job)
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
            stored.append({"src": destination.relative_to(ROOT / "site").as_posix(), "time": frame["time"]})
        material.update({
            "materialId": job["materialId"], "sourceType": "video",
            "duration": f"约{round(job['duration'])}s", "frames": stored,
            "frameStatus": "ready",
        })
        material.pop("frameError", None)
        material.pop("frameUpdatedAt", None)
        applied += 1
    for failure in manifest.get("failures", []):
        name = failure["trackName"]
        track = find_track(data, name)
        material = match_material(track.get("topMaterials", []), name, failure)
        if material and not has_complete_local_frames(material):
            material["materialId"] = failure["materialId"]
            material["frameStatus"] = "failed"
            material["frameError"] = failure["error"][:240]
            material["frameUpdatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            marked_failed += 1
    if applied or marked_failed:
        data.setdefault("meta", {})["updatedAt"] = datetime.date.today().isoformat()
        CREATIVE_PATH.write_text(dump_creative(data), encoding="utf-8")
    print(f"Applied {applied}, marked failed {marked_failed}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "apply"])
    parser.add_argument("--track", default="")
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.track.strip(), args.work_dir)
    else:
        apply(args.track.strip(), args.work_dir)


if __name__ == "__main__":
    main()
