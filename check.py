#!/usr/bin/env python3
"""
check.py  极速版（≈10–15 s / 2k 频道）
低误判：HTTP GET Range + RTMP 握手
"""
import asyncio, httpx, json, logging, time, struct
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.info

PT_FILE   = "pt.json"
CH_FILE   = "ch.json"
WORKERS   = 200            # GitHub Actions 安全
TIMEOUT   = 2.0            # 单条超时
FF_PROBE  = False          # True=二次精筛，耗时 +8~10 s
HEADERS   = {"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0"}

def load(p): return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else []

# ---------- HTTP ----------
async def probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as cli:
            r = await cli.get(url, follow_redirects=True)
            return r.status_code < 400
    except Exception:
        return False

# ---------- RTMP ----------
async def probe_rtmp(url: str) -> bool:
    try:
        host, *rest = url[7:].split("/", 1)          # rtmp://host[:port]/app/stream
        host, port = (host.split(":") + ["1935"])[:2]
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), TIMEOUT
        )
        # C0/C1 握手
        writer.write(b"\x03" + struct.pack(">I", 0)[1:] + b"\x00" * 1528)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

# ---------- 可选 FFmpeg 二次精筛 ----------
async def ff_probe(url: str) -> bool:
    if not FF_PROBE:
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-i", url, "-t", "1", "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), 3)
        return proc.returncode == 0
    except Exception:
        return False

# ---------- 单条检测 ----------
async def check_one(channel: dict) -> dict | None:
    url  = channel["address"]
    name = channel.get("title", "unknown")

    # 录像直接保留
    if url.endswith(".mp4"):
        log("[KEEP] %s | %s", name, url)
        return channel

    ok = False
    if url.startswith("rtmp://"):
        ok = await probe_rtmp(url)
    else:
        ok = await probe_http(url)

    if ok and FF_PROBE:
        ok = await ff_probe(url)

    status = "✅" if ok else "❌"
    log("%s %s | %s", status, name, url)
    return channel if ok else None

# ---------- 主流程 ----------
async def main():
    start = time.time()
    pt = load(PT_FILE)

    # 1. result=1 且去重
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

    # 2. 并发检测
    sem = asyncio.Semaphore(WORKERS)
    async def safe(ch):
        async with sem:
            return await check_one(ch)
    tasks = [safe(c) for c in channels]
    live  = [c for c in await asyncio.gather(*tasks) if c]

    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log("[SUMMARY] 在线 %d / %d，耗时 %.2fs", len(live), len(channels), time.time() - start)

if __name__ == "__main__":
    asyncio.run(main())