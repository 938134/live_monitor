#!/usr/bin/env python3
import asyncio, httpx, json, logging, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

CFG = json.loads(Path("config.json").read_text(encoding="utf-8"))
PT_FILE   = CFG["files"]["pt_data"]
CH_FILE   = CFG["files"]["ch_data"]
URLS      = CFG["url"]["default"]
IGNORE    = set(CFG["url"]["ignore"])
KEYS      = CFG["keys"]
THRESHOLD = timedelta(hours=CFG["timeout"]["update_threshold"])
MAX_CONN  = CFG["concurrency"]["max"]

# ---------- 工具 ----------
def log(msg): logging.info(msg)
def save(path, data): Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))
def load(path):
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        return json.loads(text) if text else []
    except Exception:
        return []
# ---------- 更新 ----------
async def fetch(session, url):
    try:
        r = await session.get(url, timeout=CFG["timeout"]["web_request"])
        last = r.headers.get("Last-Modified")
        if last:
            mod = datetime.strptime(last, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - mod > THRESHOLD:
                return None
        return r.json()
    except Exception as e:
        log(f"❌ fetch {url} {e}")
        return None

async def update_platforms(pt):
    sem = asyncio.Semaphore(MAX_CONN)
    async with httpx.AsyncClient() as session:
        tasks = [update_one(sem, session, p) for p in pt]
        await asyncio.gather(*tasks)

async def update_one(sem, session, src):
    async with sem:
        # 1) 拉取平台索引
        index_url = src[KEYS["address"]] + CFG["url"]["suffix"]
        data = await fetch(session, index_url)
        if not data:
            src[KEYS["result"]] = 0
            return
        platforms = data.get(KEYS["platform"], [])
        src[KEYS["platform"]] = platforms
        src[KEYS["result"]] = 1

        # 2) 拉取每个平台的频道
        for pf in platforms:
            channel_url = src[KEYS["address"]] + pf[KEYS["address"]]
            ch_data = await fetch(session, channel_url)
            if not ch_data:
                pf[KEYS["result"]] = 0
                continue
            channels = ch_data.get(KEYS["channel"], [])
            # 过滤 ignore 列表
            channels = [
                c for c in channels
                if c.get(KEYS["address"]) and c[KEYS["address"]] not in IGNORE
            ]
            pf[KEYS["channel"]] = channels
            pf[KEYS["result"]] = 1

# ---------- 频道可用检测 ----------
def check_stream(url: str, timeout: int = 5) -> bool:
    """使用 ffmpeg 检测流是否在线"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", url, "-t", "1", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.returncode == 0
    except Exception:
        return False

def check_channels(channels):
    """多线程检测"""
    live = []
    total = len(channels)
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [(ch, pool.submit(check_stream, ch[KEYS["address"]])) for ch in channels]
        for ch, fut in futures:
            ok = fut.result()
            log(f"{'✅' if ok else '❌'} {ch[KEYS['name']]}")
            if ok:
                live.append(ch)
    log(f"检测完成：{len(live)}/{total}")
    return live

# ---------- 主 ----------
async def main():
    log("🚀 start")
    pt = load(PT_FILE)

    # 同步 URL
    new_urls = set(URLS)
    old_urls = {x[KEYS["address"]] for x in pt}
    for u in new_urls - old_urls:
        pt.append({KEYS["address"]: u, KEYS["result"]: 0})

    await update_platforms(pt)
    save(PT_FILE, pt)

    # 收集全部频道
    all_channels = []
    for src in pt:
        if not src.get(KEYS["result"]):
            continue
        for pf in src.get(KEYS["platform"], []):
            if not pf.get(KEYS["result"]):
                continue
            all_channels.extend(pf.get(KEYS["channel"], []))

    # 检测可用
    live_channels = check_channels(all_channels)
    save(CH_FILE, live_channels)
    log(f"📦 saved {len(live_channels)} live channels")

if __name__ == "__main__":
    asyncio.run(main())