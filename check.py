#!/usr/bin/env python3
"""
check.py
1. 只处理 pt.json 中 result=1 的平台
2. 去重频道 url
3. 快速检测
"""

import json, logging, subprocess, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

PT_FILE   = "pt.json"
CH_FILE   = "ch.json"
WORKERS   = 80
FF_TIMEOUT = 0.5

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else []

def check_one(ch):
    url = ch["address"]
    if url.endswith(".mp4"):
        return ch
    try:
        subprocess.run(
            ["ffmpeg", "-i", url, "-t", str(FF_TIMEOUT), "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return ch
    except Exception:
        return None

def main():
    start = time.time()
    pt = load(PT_FILE)

    # 1. 只取 result=1 的平台
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

    # 2. 检测
    live = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for idx, res in enumerate(ex.map(check_one, channels)):
            if idx % 500 == 0:
                log(f"[CHECK] {idx}/{len(channels)}")
            if res:
                live.append(res)

    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log(f"[CHECK] 在线 {len(live)}/{len(channels)}，耗时 {time.time()-start:.2f}s")

if __name__ == "__main__":
    main()