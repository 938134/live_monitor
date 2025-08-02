#!/usr/bin/env python3
"""
check.py
读取 pt.json，检测所有频道，输出 ch.json（仅在线）
耗时：ffmpeg 检测
"""
import json, logging, time, subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

CFG = json.loads(Path("config.json").read_text())
PT_FILE  = CFG["files"]["pt_data"]
CH_FILE  = CFG["files"]["ch_data"]
WORKERS  = 30        # 并发线程
TIMEOUT  = 3         # ffmpeg -t

def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []

def check_one(channel):
    url = channel["address"]
    if url.endswith(".mp4"):
        return channel  # 录像直接保留
    try:
        subprocess.run(
            ["ffmpeg", "-i", url, "-t", "1", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT,
        )
        return channel
    except Exception:
        return None

def main():
    start = time.time()
    pt = load(PT_FILE)

    # 聚合所有频道
    channels = []
    for src in pt:
        for pf in src.get("pingtai", []):
            channels.extend(pf.get("zhubo", []))

    # 检测
    live = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for idx, fut in enumerate(ex.map(check_one, channels)):
            if fut:
                live.append(fut)
            if idx % 100 == 0:
                log(f"[CHECK] {idx+1}/{len(channels)}")

    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log(f"[CHECK] {len(live)}/{len(channels)} live, {time.time()-start:.2f}s")

if __name__ == "__main__":
    main()