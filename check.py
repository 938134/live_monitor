#!/usr/bin/env python3
"""
check.py（极速异步版 + 实时日志）
1. 只取 pt.json 中 result=1 的平台
2. URL 去重
3. HTTP/RTMP 轻量级探活
4. 实时输出每一路检测结果
"""

import asyncio, httpx, json, logging, time
from pathlib import Path

# 日志格式：时间 + 状态 + 名称 + URL
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.info

PT_FILE   = "pt.json"
CH_FILE   = "ch.json"
WORKERS   = 200               # 并发协程数
TIMEOUT   = 2.0               # 单条超时(秒)

# ---------- 加载 ----------
def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else []

# ---------- 探活 ----------
async def probe_http(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.head(url)
            return r.status_code < 400
    except Exception as e:
        return False

async def probe_rtmp(host: str, port: int = 1935) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

async def check_one(channel: dict) -> dict | None:
    url  = channel["address"]
    name = channel.get("title", "unknown")

    # 录像文件直接放行
    if url.endswith(".mp4"):
        log("[KEEP] %s | %s", name, url)
        return channel

    ok = False
    if url.startswith("rtmp://"):
        host = url.split("/")[2].split(":")[0]
        ok   = await probe_rtmp(host)
    else:
        ok = await probe_http(url)

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

    # 2. 并发检测（带并发上限）
    sem = asyncio.Semaphore(WORKERS)
    async def safe(ch):
        async with sem:
            return await check_one(ch)

    tasks = [safe(c) for c in channels]
    live  = [c for c in await asyncio.gather(*tasks) if c]

    # 3. 写结果
    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    log("[SUMMARY] 在线 %d / %d，耗时 %.2fs", len(live), len(channels), time.time() - start)

if __name__ == "__main__":
    asyncio.run(main())