#!/usr/bin/env python3
"""
check.py
- 非 RTMP：保持 ffmpeg.probe
- RTMP  ：TCP 握手 → FFmpeg 精筛（各 1 s）
"""
import json, logging, time, subprocess, asyncio
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.info

PT_FILE   = "pt.json"
CH_FILE   = "ch.json"
WORKERS   = 50          # 并发
FF_TIMEOUT = 1.0        # ffmpeg 精筛 1 s
TCP_TIMEOUT = 1.0       # TCP 握手 1 s

def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else []

# ---------- 非 RTMP ----------
def _check_other(url: str) -> bool:
    try:
        import ffmpeg
        probe = ffmpeg.probe(url, timeout=FF_TIMEOUT)
        return "streams" in probe and len(probe["streams"]) > 0
    except Exception:
        return False

# ---------- RTMP ----------
async def _rtmp_tcp_handshake(url: str) -> bool:
    try:
        host, *rest = url[7:].split("/", 1)           # rtmp://host[:port]/...
        host, port = (host.split(":") + ["1935"])[:2]
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), TCP_TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

async def _rtmp_ff_probe(url: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-i", url, "-t", "1", "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), FF_TIMEOUT + 0.5)
        return proc.returncode == 0
    except Exception:
        return False

# ---------- 统一检查 ----------
async def check_one(channel: dict) -> dict | None:
    url  = channel["address"]
    name = channel.get("title", "unknown")

    # 录像直接保留
    if url.endswith(".mp4"):
        log("[KEEP] %s | %s", name, url)
        return channel

    ok = False
    if url.startswith("rtmp://"):
        # 1) TCP 握手
        ok = await _rtmp_tcp_handshake(url)
        if ok:
            # 2) FFmpeg 精筛
            ok = await _rtmp_ff_probe(url)
            status = "✅-2" if ok else "❌-2"
        else:
            status = "❌-1"
    else:
        ok = _check_other(url)
        status = "✅" if ok else "❌"

    log("%s %s | %s", status, name, url)
    return channel if ok else None

# ---------- 主 ----------
async def main():
    start = time.time()
    pt = load(PT_FILE)

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

    sem = asyncio.Semaphore(WORKERS)
    async def safe(ch):
        async with sem:
            return await check_one(ch)

    tasks = [safe(ch) for ch in channels]
    live = [c for c in await asyncio.gather(*tasks) if c]

    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log("[SUMMARY] 在线 %d / %d，耗时 %.2fs", len(live), len(channels), time.time() - start)

if __name__ == "__main__":
    asyncio.run(main())