#!/usr/bin/env python3
import asyncio, httpx, json, logging, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

CFG = json.loads(Path("config.json").read_text())
URLS    = CFG["url"]["default"]
IGNORE  = set(CFG["url"]["ignore"])
KEYS    = CFG["keys"]
SUFFIX  = CFG["url"]["suffix"]
PT_FILE = CFG["files"]["pt_data"]

# -------- 可调参数 --------
MAX_CONN   = 10          # 降低并发
POOL_LIMIT = 20          # 连接池上限
TIMEOUT    = 3           # 单请求超时
HEADERS    = {"User-Agent": "live-monitor/1.0"}

# 忽略平台黑名单
BLACKLIST_PLATFORM = {
    "jsonlongzhu.txt", "jsonyingke.txt",
    "jsonoumeiFEATURED.txt", "jsonoumeiFEMALE.txt",
    "jsonoumeiMALE.txt", "jsonoumeiCOUPLE.txt", "jsonoumeiTRANS.txt"
}

async def fetch(session, url):
    try:
        r = await session.get(url, timeout=TIMEOUT)
        return r.json()
    except Exception as e:
        log(f"❌ {url} {e}")
        return None

async def update_source(session, src_url):
    pt = {"address": src_url, "result": 0, KEYS["platform"]: []}
    data = await fetch(session, src_url + SUFFIX)
    if not data:
        return pt

    platforms = data.get(KEYS["platform"], [])
    tasks = []
    for pf in platforms:
        addr = pf.get(KEYS["address"])
        if addr in IGNORE or addr in BLACKLIST_PLATFORM:
            continue
        ch_url = src_url + addr
        tasks.append(fetch(session, ch_url))
        pf[KEYS["channel"]] = []  # 先占位

    # 并发拉频道
    ch_results = await asyncio.gather(*tasks)
    for pf, ch_data in zip(platforms, ch_results):
        pf[KEYS["channel"]] = ch_data.get(KEYS["channel"], []) if ch_data else []
        pf.setdefault(KEYS["result"], 1 if ch_data else 0)

    pt[KEYS["platform"]] = platforms
    pt["result"] = 1
    return pt

async def main():
    start = time.time()
    limits = httpx.Limits(max_keepalive_connections=POOL_LIMIT, max_connections=POOL_LIMIT)
    async with httpx.AsyncClient(limits=limits, headers=HEADERS) as client:
        sem = asyncio.Semaphore(MAX_CONN)
        tasks = [update_source(client, u) for u in URLS]
        results = await asyncio.gather(*tasks)
    Path(PT_FILE).write_text(json.dumps(results, ensure_ascii=False, indent=2))
    log(f"[FETCH] done {len(results)} sources in {time.time()-start:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())