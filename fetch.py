#!/usr/bin/env python3
"""
fetch.py
从源站拉平台→频道，保存 pt.json
耗时：网络 + 解析
"""
import asyncio, httpx, json, logging, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

CFG = json.loads(Path("config.json").read_text())
URLS       = CFG["url"]["default"]
IGNORE     = set(CFG["url"]["ignore"])
KEYS       = CFG["keys"]
SUFFIX     = CFG["url"]["suffix"]
TIMEOUT    = CFG["timeout"]["web_request"]
MAX_CONN   = CFG["concurrency"]["max"]
PT_FILE    = CFG["files"]["pt_data"]

async def fetch(session, url):
    try:
        r = await session.get(url, timeout=TIMEOUT)
        return r.json()
    except Exception as e:
        log(f"❌ fetch {url} {e}")
        return None

async def update_source(src_url):
    pt = {"address": src_url, "result": 0, KEYS["platform"]: []}
    data = await fetch(httpx.AsyncClient(), src_url + SUFFIX)
    if not data:
        return pt
    platforms = data.get(KEYS["platform"], [])
    for pf in platforms:
        if pf.get(KEYS["address"]) in IGNORE:
            continue
        # 拉取频道
        ch_url = src_url + pf[KEYS["address"]]
        ch_data = await fetch(httpx.AsyncClient(), ch_url)
        pf[KEYS["channel"]] = ch_data.get(KEYS["channel"], []) if ch_data else []
        pt[KEYS["platform"]].append(pf)
    pt["result"] = 1
    return pt

async def main():
    start = time.time()
    sem = asyncio.Semaphore(MAX_CONN)
    async with httpx.AsyncClient() as client:
        tasks = [update_source(u) for u in URLS]
        pt_list = await asyncio.gather(*tasks)
    Path(PT_FILE).write_text(json.dumps(pt_list, ensure_ascii=False, indent=2))
    log(f"[FETCH] done {len(pt_list)} sources, {time.time()-start:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())