#!/usr/bin/env python3
"""
check.py（防呆限时版）
"""
import asyncio, httpx, json, logging, time, struct
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.info

PT_FILE = "pt.json"
CH_FILE = "ch.json"
WORKERS = 50          # 并发降到 50
TOTAL_TIMEOUT = 3.0   # 单条硬性 3 秒

def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else []

# ---------- 探活 ----------
async def probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as cli:
            r = await cli.head(url, follow_redirects=True)
            return r.status_code < 400
    except Exception as e:
        return False

async def probe_rtmp(url: str) -> bool:
    try:
        host, *rest = url[7:].split("/", 1)
        host, port = (host.split(":") + ["1935"])[:2]
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), 1.5
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

# ---------- 单条检测（带硬性超时） ----------
async def check_one(channel: dict) -> dict | None:
    url  = channel["address"]
    name = channel.get("title", "unknown")

    if url.endswith(".mp4"):
        log("[KEEP] %s | %s", name, url)
        return channel

    try:
        if url.startswith("rtmp://"):
            ok = await asyncio.wait_for(probe_rtmp(url), TOTAL_TIMEOUT)
        else:
            ok = await asyncio.wait_for(probe_http(url), TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        log("[TIMEOUT] %s | %s", name, url)
        return None
    except Exception as e:
        log("[ERROR] %s | %s | %s", name, url, e)
        return None

    status = "✅" if ok else "❌"
    log("%s %s | %s", status, name, url)
    return channel if ok else None

# ---------- 分批并发 ----------
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

    # 分批 500 条，防止协程爆炸
    batch_size = 500
    live = []
    for i in range(0, len(channels), batch_size):
        batch = channels[i : i + batch_size]
        tasks = [safe(ch) for ch in batch]
        results = await asyncio.gather(*tasks)
        live.extend([r for r in results if r])
        log("[BATCH] 已处理 %d/%d", min(i + batch_size, len(channels)), len(channels))

    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log("[SUMMARY] 在线 %d / %d，耗时 %.2fs", len(live), len(channels), time.time() - start)

if __name__ == "__main__":
    asyncio.run(main())