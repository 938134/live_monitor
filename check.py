#!/usr/bin/env python3
"""
check.py
1. 只取 pt.json 中 result=1 的平台
2. URL 去重
3. 区分 rtmp / 非 rtmp 检测
"""

import json, logging, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

PT_FILE   = "pt.json"
CH_FILE   = "ch.json"
WORKERS   = 20
FF_TIMEOUT = 3               # ffmpeg -t 秒数

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else []

def _check_rtmp(url):
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", url, "-t", str(FF_TIMEOUT), "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=FF_TIMEOUT + 2,
        )
        return "Stream #0:" in result.stderr.decode("utf-8", errors="ignore")
    except Exception:
        return False

def _check_other(url):
    try:
        import ffmpeg
        probe = ffmpeg.probe(url, timeout=FF_TIMEOUT)
        return "streams" in probe and len(probe["streams"]) > 0
    except Exception:
        return False

def check_one(channel):
    url = channel["address"]
    name = channel.get("title", "unknown")
    if url.endswith(".mp4"):
        log(f"[KEEP] 录像 {name} {url}")
        return channel
    if url.startswith("rtmp://"):
        ok = _check_rtmp(url)
    else:
        ok = _check_other(url)
    status = "✅ 在线" if ok else "❌ 离线"
    log(f"{status} {name} {url}")
    return channel if ok else None

def main():
    start = time.time()
    pt = load(PT_FILE)

    # 1. result=1 且去重
    channels = []
    seen = set()
    for src in pt:
        if src.get("result") != 1:
            continue
        for pf in src.get("pingtai", []):
            for ch in pf.get("zhubo", []):
                url = ch.get("address")
                if url and url not in seen:
                    seen.add(url)
                    channels.append(ch)

    # 2. 并发检测
    live = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for ch in ex.map(check_one, channels):
            if ch:
                live.append(ch)

    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log(f"[CHECK] 在线 {len(live)}/{len(channels)}，耗时 {time.time()-start:.2f}s")

if __name__ == "__main__":
    main()