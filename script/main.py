#!/usr/bin/env python3
"""
live-monitor
自动采集并检测直播源可用性
"""
import os
import json
import asyncio
import httpx
import time
import subprocess
import concurrent.futures
import pathlib
import hashlib
import ffmpeg
from pypinyin import lazy_pinyin
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# ---------- 配置 ----------
CONFIG = {
    'files': {
        'pt_data': 'data/pt.json',
        'ch_data': 'data/ch.json'
    },
    'timeout': {
        'web_request': 3,      # 单次 http 超时
        'stream_check': 3      # ffmpeg 探测超时
    },
    'concurrency': {
        'max': 10,
        'channel_check_multiplier': 3
    },
    'keys': {
        'platform': "pingtai",
        'channel': "zhubo",
        'result': "result",
        'address': "address",
        'name': "title"
    },
    'ignore': [
        "jsonweishizhibo.txt",
        "jsonlongzhu.txt",
        "jsonoumeiFEATURED.txt",
        "jsonoumeiFEMALE.txt",
        "jsonoumeiMALE.txt",
        "jsonoumeiCOUPLE.txt",
        "jsonoumeiTRANS.txt"
    ]
}

CACHE_DIR = pathlib.Path(__file__).with_name('..') / 'cache'
CACHE_DIR.mkdir(exist_ok=True)
IGNORE_SET = set(CONFIG['ignore'])

# ---------- 工具 ----------
def _cache_path(url: str) -> pathlib.Path:
    return CACHE_DIR / (hashlib.md5(url.encode()).hexdigest() + '.json')

def _file_age_sec(path: pathlib.Path) -> float:
    return time.time() - path.stat().st_mtime if path.exists() else 999999

class DataProcessor:
    @staticmethod
    def log(msg):
        print(f"{datetime.now():%F %T}: {msg}")

    @staticmethod
    def save(file, data):
        pathlib.Path(file).parent.mkdir(parents=True, exist_ok=True)
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load(file):
        try:
            with open(file, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []

    @staticmethod
    def sync(old, new_urls):
        addr_key = CONFIG['keys']['address']
        old_map = {o[addr_key]: o for o in old}
        for u in new_urls:
            if u not in old_map:
                old.append({addr_key: u, CONFIG['keys']['result']: 0})
        return [o for o in old if o[addr_key] in new_urls]

# ---------- 平台发现 ----------
class DataUpdater:
    @staticmethod
    async def discover_platforms(session, root_url):
        url = f"{root_url.rstrip('/')}/platforms.json"
        try:
            r = await session.get(url, timeout=CONFIG['timeout']['web_request'])
            r.raise_for_status()
            data = r.json()
            platforms = data.get("platforms", [])
            return [f"{root_url.rstrip('/')}/{p}" for p in platforms
                    if p.endswith('.json') and p not in IGNORE_SET]
        except Exception as e:
            DataProcessor.log(f"【发现失败】{url} | {e}")
            return []

    @staticmethod
    async def fetch_webpage(semaphore, url):
        async with semaphore:
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.get(url, timeout=CONFIG['timeout']['web_request'])
                    r.raise_for_status()
                except Exception as e:
                    DataProcessor.log(f"【采集失败·网络】{url} | {e}")
                    return None

                body = r.text
                body_hash = hashlib.md5(body.encode()).hexdigest()
                cache_file = _cache_path(url)

                # 内容未变且缓存时间 ≥6h
                if cache_file.exists():
                    old_body = cache_file.read_text(encoding='utf-8')
                    if hashlib.md5(old_body.encode()).hexdigest() == body_hash \
                            and _file_age_sec(cache_file) >= 6 * 3600:
                        DataProcessor.log(f"【采集失败·内容过期】{url}")
                        return None

                cache_file.write_text(body, encoding='utf-8')
                return json.loads(body)

    @staticmethod
    async def update_platforms(pt_data):
        sem = asyncio.Semaphore(CONFIG['concurrency']['max'])
        tasks = [DataUpdater.update_data(sem, p, CONFIG['keys']['platform'])
                 for p in pt_data]
        await asyncio.gather(*tasks)

    @staticmethod
    async def update_channels(pt_data):
        sem = asyncio.Semaphore(CONFIG['concurrency']['max'] * 3)
        tasks = []
        for p in pt_data:
            if p.get(CONFIG['keys']['result'], 0) > 0:
                for ch in p.get(CONFIG['keys']['platform'], []):
                    tasks.append(DataUpdater.update_data(sem, ch, CONFIG['keys']['channel']))
        await asyncio.gather(*tasks)
        DataProcessor.save(CONFIG['files']['pt_data'], pt_data)

    @staticmethod
    async def update_data(sem, item, section_key):
        keys = CONFIG['keys']
        item.setdefault(section_key, [])
        url = item[keys['address']]
        new_data = await DataUpdater.fetch_webpage(sem, url)
        if new_data is None:
            item[keys['result']] = 0
            return
        try:
            new_list = new_data.get(section_key, [])
            # 过滤 ignore
            new_list = [x for x in new_list
                        if x.get(keys['address'], '').split('/')[-1] not in IGNORE_SET]
            # 按拼音排序
            if new_list and keys['name'] in new_list[0]:
                new_list.sort(key=lambda x: [p for p in lazy_pinyin(x[keys['name']]) if not p.isdigit()])
            # 更新
            item[keys][section_key] = new_list
            item[keys['result']] = 1
        except Exception as e:
            item[keys['result']] = 0
            DataProcessor.log(f"【解析失败】{url} | {e}")

# ---------- 频道检测 ----------
class ChannelChecker:
    @staticmethod
    def _log(status, url, err=None):
        msg = f"【{status}】{url}"
        if err:
            msg += f" | {err}"
        DataProcessor.log(msg)

    @staticmethod
    def _check_stream(url):
        if url.endswith('.mp4'):
            return True, "录像"
        try:
            if url.startswith('rtmp://'):
                res = subprocess.run(
                    ['ffmpeg', '-i', url, '-t', '3', '-f', 'null', '-'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
                )
                ok = "Stream #0:" in res.stderr.decode('utf-8', errors='ignore')
            else:
                probe = ffmpeg.probe(url, timeout=CONFIG['timeout']['stream_check'])
                ok = bool(probe.get('streams'))
            return ok, "在线" if ok else "离线"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def check_single(ch):
        url = ch[CONFIG['keys']['address']]
        ok, info = ChannelChecker._check_stream(url)
        ChannelChecker._log("在线" if ok else "离线", url, None if ok else info)
        return ch if ok else None

    @staticmethod
    async def check_all(pt_data):
        channels = []
        keys = CONFIG['keys']
        for p in pt_data:
            if p.get(keys['result'], 0) > 0:
                for pl in p.get(keys['platform'], []):
                    if pl.get(keys['result'], 0) > 0:
                        channels.extend(pl.get(keys['channel'], []))
        DataProcessor.log(f"【开始检测】{len(channels)} 个频道")
        with ThreadPoolExecutor(
            max_workers=CONFIG['concurrency']['max'] * CONFIG['concurrency']['channel_check_multiplier']
        ) as ex:
            futures = [ex.submit(ChannelChecker.check_single, c) for c in channels]
            live = [f.result() for f in concurrent.futures.as_completed(futures) if f.result()]
        DataProcessor.save(CONFIG['files']['ch_data'], live)
        DataProcessor.log(f"【检测完成】可用 {len(live)}/{len(channels)}")

# ---------- 主流程 ----------
async def main():
    start = time.time()
    DataProcessor.log("【任务开始】")

    # 1. 动态发现 platform URL
    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(CONFIG['concurrency']['max'])
        root_file = pathlib.Path(__file__).with_name('..') / 'urls' / 'default.txt'
        roots = [r.strip() for r in root_file.read_text().splitlines() if r.strip()]
        tasks = [DataUpdater.discover_platforms(client, r) for r in roots]
        platform_urls = [u for lst in await asyncio.gather(*tasks) for u in lst]

    # 2. 初始化数据
    pt_data = DataProcessor.load(CONFIG['files']['pt_data'])
    pt_data = DataProcessor.sync(pt_data, platform_urls)
    DataProcessor.save(CONFIG['files']['pt_data'], pt_data)

    # 3. 更新平台 & 频道
    await DataUpdater.update_platforms(pt_data)
    await DataUpdater.update_channels(pt_data)

    # 4. 检测可用频道
    await ChannelChecker.check_all(pt_data)

    DataProcessor.log(f"【任务完成】耗时 {time.time() - start:.1f}s")

if __name__ == "__main__":
    asyncio.run(main())
