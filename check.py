#!/usr/bin/env python3
"""
check.py
两轮检测：
1) 轻量级探活：HTTP Range + RTMP 握手
2) FFmpeg 精筛：仅对第1轮“在线”再测1秒
"""
import asyncio, httpx, json, logging, time, struct, subprocess
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.info

PT_FILE   = "pt.json"
CH_FILE   = "ch.json"
WORKERS   = 50
TIMEOUT1  = 2.0        # 第1轮超时
TIMEOUT2  = 1.0        # FFmpeg 精筛超时
HEADERS   = {"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0"}

def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else []

# ---------- 第1轮：轻量级 ----------
async def probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT1) as cli:
            r = await cli.get(url, follow_redirects=True, headers=HEADERS)
            return r.status_code < 400
    except Exception:
        return False

async def probe_rtmp(url: str) -> bool:
    try:
        host, *rest = url[7:].split("/", 1)
        host, port = (host.split(":") + ["1935"])[:2]
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), TIMEOUT1
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

async def check_light(channel: dict) -> dict | None:
    url  = channel["address"]
    name = channel.get("title", "unknown")
    if url.endswith(".mp4"):
        log("[KEEP] %s | %s", name, url)
        return channel
    ok = await (probe_rtmp(url) if url.startswith("rtmp://") else probe_http(url))
    status = "✅" if ok else "❌"
    log("%s-1 %s | %s", status, name, url)
    return channel if ok else None

# ---------- 第2轮：FFmpeg ----------
async def ff_probe(url: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-i", url, "-t", "1", "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), TIMEOUT2)
        return proc.returncode == 0
    except Exception:
        return False

async def check_final(channel: dict) -> dict | None:
    url  = channel["address"]
    name = channel.get("title", "unknown")
    ok   = await asyncio.wait_for(ff_probe(url), TIMEOUT2)
    status = "✅" if ok else "❌"
    log("%s-2 %s | %s", status, name, url)
    return channel if ok else None

# ---------- 主流程 ----------
async def main():
    start = time.time()
    pt = load(PT_FILE)

    # 1. 取 result=1 并去重
    channels, seen = [], set()
    for src in pt:
        if src.get("result") != 1:
            continue
        for pf in src.get("pingtai", []):
            for ch in pf.get("zhubo", []):
                url = ch.get("address")
                if url and url not in seen:
                    seen.add(url)
                    channels.append(ch)

    # 2. 第1轮：轻量级并发
    sem = asyncio.Semaphore(WORKERS)
    async def safe(fn, ch):
        async with sem:
            return await fn(ch)

    tasks1 = [safe(check_light, ch) for ch in channels]
    light_ok = [c for c in await asyncio.gather(*tasks1) if c]

    # 3. 第2轮：FFmpeg 精筛
    tasks2 = [safe(check_final, ch) for ch in light_ok]
    final_ok = [c for c in await asyncio.gather(*tasks2) if c]

    Path(CH_FILE).write_text(json.dumps(final_ok, ensure_ascii=False, indent=2))
    log("[SUMMARY] 在线 %d / %d，两轮共耗时 %.2fs", len(final_ok), len(channels), time.time() - start)

if __name__ == "__main__":
    asyncio.run(main())