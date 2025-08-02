#!/usr/bin/env python3
"""
fetch_channels.py
一步完成：
1. 拉平台索引
2. 拉每个平台对应的频道列表
3. 6 小时过期检查
输出 pt.json
"""

import asyncio, httpx, json, logging, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

CFG = json.loads(Path("config.json").read_text())
URLS    = CFG["url"]["default"]
SUFFIX  = CFG["url"]["suffix"]
IGNORE  = set(CFG["url"]["ignore"])
KEYS    = CFG["keys"]
PT_FILE = CFG["files"]["pt_data"]
TIMEOUT = CFG["timeout"]["web_request"]
MAX_CONN = CFG["concurrency"]["max"]
EXPIRE_H   = CFG["timeout"]["update_threshold"]

def is_expired(last_mod: str) -> bool:
    if not last_mod:
        return True
    try:
        mod = datetime.strptime(last_mod, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - mod > timedelta(hours=EXPIRE_H)
    except Exception:
        return True

async def fetch(session, url):
    try:
        r = await session.get(url, timeout=TIMEOUT)
        last = r.headers.get("Last-Modified", "")
        if is_expired(last):
            return None
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
        if addr in IGNORE:
            continue
        ch_url = src_url + addr
        tasks.append(fetch(session, ch_url))

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
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(MAX_CONN)
        tasks = [update_source(client, u) for u in URLS]
        pt_list = await asyncio.gather(*tasks)
    Path(PT_FILE).write_text(json.dumps(pt_list, ensure_ascii=False, indent=2))
    log(f"[FETCH] done {len(pt_list)} sources, {time.time()-start:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())