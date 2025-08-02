import os
import json
import asyncio
import httpx
import time
import subprocess
import concurrent.futures
import ffmpeg
import argparse
import logging
from pypinyin import lazy_pinyin
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 加载配置文件
def load_config(config_path):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"配置文件读取失败: {e}")
        sys.exit(1)

# 设置日志
def setup_logging(log_file=None):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=handlers
    )

# 命令行参数
def parse_args():
    parser = argparse.ArgumentParser(description="IPTV Channel Checker")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--log", default=None, help="日志文件路径")
    return parser.parse_args()

# 全局变量
CONFIG = {}
args = parse_args()
setup_logging(args.log)
CONFIG = load_config(args.config)

config_urls_set = set(CONFIG['url']['default'])
config_ignore_urls_set = set(CONFIG['url']['ignore'])

class DataProcessor:
    """数据处理工具类"""
    @staticmethod
    def get_current_time():
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    @staticmethod
    def log(message):
        logging.info(message)

    @staticmethod
    def sorted_unique_channels(pt_data):
        unique_channels = {}
        pinyin_cache = {}
        keys = CONFIG['keys']
        for url in pt_data:
            if url.get(keys['result'], 0) > 0:
                for platform in url.get(keys['platform'], []):
                    if platform.get(keys['result'], 0) > 0:
                        for ch in platform.get(keys['channel'], []):
                            addr = ch.get(keys['address'], "")
                            if addr and addr not in unique_channels:
                                name = ch[keys['name']]
                                if name not in pinyin_cache:
                                    pinyin_cache[name] = [
                                        pin for pin in lazy_pinyin(name) if not pin.isdigit()
                                    ]
                                unique_channels[addr] = ch
        return sorted(unique_channels.values(), key=lambda x: pinyin_cache[x[keys['name']]])

    @staticmethod
    def save_data(file_path, data):
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    @staticmethod
    def read_data(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []

    @staticmethod
    def sync_urls(pt_data, config_urls):
        start_time = time.time()
        pt_urls = {item[CONFIG['keys']['address']]: item for item in pt_data}
        config_urls_set = set(config_urls)
        for url in config_urls_set - set(pt_urls.keys()):
            pt_data.append({
                CONFIG['keys']['address']: url,
                CONFIG['keys']['result']: 0,
            })
        result = [item for item in pt_data if item[CONFIG['keys']['address']] in config_urls_set]
        elapsed = time.time() - start_time
        DataProcessor.log(f"【数据同步完成】耗时: {elapsed:.2f}秒")
        return result

class DataUpdater:
    """数据更新器"""
    @staticmethod
    async def fetch_webpage(semaphore, url):
        async with semaphore:
            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(url, timeout=CONFIG['timeout']['web_request'])
                    last_modified = response.headers.get('Last-Modified')
                    if last_modified:
                        modify_time = datetime.strptime(last_modified, '%a, %d %b %Y %H:%M:%S %Z')
                        if datetime.now() - modify_time < timedelta(hours=CONFIG['timeout']['update_threshold']):
                            return json.loads(response.text)
                except Exception as e:
                    DataProcessor.log(f"【采集失败】{url} | 错误: {str(e)}")
                return None

    @staticmethod
    async def update_data(semaphore, old_data, section_key):
        keys = CONFIG['keys']
        old_data.setdefault(section_key, [])
        full_url = old_data[keys['address']] + CONFIG['url']['suffix'] if section_key == keys['platform'] else old_data[keys['address']]
        new_data = await DataUpdater.fetch_webpage(semaphore, full_url)
        if new_data is None:
            old_data[keys['result']] = 0
            return
        try:
            new_data[section_key] = [
                item for item in new_data[section_key]
                if item[keys['address']] not in config_ignore_urls_set
            ]
            if keys['name'] in new_data[section_key][0]:
                new_data[section_key] = sorted(
                    new_data[section_key],
                    key=lambda x: [pin for pin in lazy_pinyin(x[keys['name']]) if not pin.isdigit()]
                )
            for item in new_data[section_key]:
                item.pop(keys['pep'], None)
                if section_key == keys['platform']:
                    item[keys['address']] = old_data[keys['address']] + item[keys['address']]
                    item[keys['result']] = item.get(keys['result'], 0)
            old_dict = {item[keys['address']]: item for item in old_data[section_key]}
            new_dict = {item[keys['address']]: item for item in new_data[section_key]}
            added = {k: v for k, v in new_dict.items() if k not in old_dict}
            removed = {k: v for k, v in old_dict.items() if k not in new_dict}
            updated = {
                k: {'old': old_dict[k], 'new': v}
                for k, v in new_dict.items()
                if k in old_dict and v != old_dict[k]
            }
            for item in added.values():
                old_data[section_key].append(item)
            for item in removed.values():
                old_data[section_key].remove(item)
            for addr, changes in updated.items():
                for item in old_data[section_key]:
                    if item[keys['address']] == addr:
                        item.update(changes['new'])
                        break
            if added or updated or removed:
                old_data[keys['result']] = 1
                DataProcessor.log(f"【采集成功】{full_url} | 新增: {len(added)} 删除: {len(removed)} 更新: {len(updated)}")
            else:
                old_data[keys['result']] = (old_data.get(keys['result'], 0) + 1) % 100
        except Exception as e:
            old_data[keys['result']] = 0
            DataProcessor.log(f"【采集失败】{full_url} | 错误: {str(e)}")

    @staticmethod
    async def update_platforms(pt_data):
        start_time = time.time()
        semaphore = asyncio.Semaphore(CONFIG['concurrency']['max'])
        tasks = [asyncio.create_task(DataUpdater.update_data(semaphore, item, CONFIG['keys']['platform'])) for item in pt_data]
        await asyncio.gather(*tasks)
        elapsed = time.time() - start_time
        DataProcessor.log(f"【平台级更新完成】耗时: {elapsed:.2f}秒")

    @staticmethod
    async def update_channels(pt_data):
        start_time = time.time()
        semaphore = asyncio.Semaphore(CONFIG['concurrency']['max'] * CONFIG['concurrency']['channel_check_multiplier'])
        tasks = []
        for item in pt_data:
            if item.get(CONFIG['keys']['result'], 0) > 0:
                for platform in item.get(CONFIG['keys']['platform'], []):
                    tasks.append(asyncio.create_task(DataUpdater.update_data(semaphore, platform, CONFIG['keys']['channel'])))
        await asyncio.gather(*tasks)
        DataProcessor.save_data(CONFIG['files']['pt_data'], pt_data)
        elapsed = time.time() - start_time
        DataProcessor.log(f"【频道级更新完成】耗时: {elapsed:.2f}秒")

class ChannelChecker:
    """频道检查器"""
    @staticmethod
    def _log(status, url, error_msg=None):
        message = f"【{status}】{url}"
        if error_msg:
            message += f" | 错误: {error_msg}"
        DataProcessor.log(message)

    @staticmethod
    def _check_rtmp_stream(url):
        try:
            result = subprocess.run(
                ['ffmpeg', '-i', url, '-t', '3', '-f', 'null', '-'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )
            return "Stream #0:" in result.stderr.decode('utf-8')
        except:
            return False

    @staticmethod
    def _check_non_rtmp_stream(url):
        try:
            probe = ffmpeg.probe(url, timeout=CONFIG['timeout']['stream_check'])
            return 'streams' in probe and len(probe['streams']) > 0
        except:
            return False

    @staticmethod
    def get_stream_status(url):
        if url.endswith('.mp4'):
            return "录像", None
        try:
            if url.startswith('rtmp://'):
                is_live = ChannelChecker._check_rtmp_stream(url)
            else:
                is_live = ChannelChecker._check_non_rtmp_stream(url)
            return "在线" if is_live else "离线", None
        except Exception as e:
            return "错误", str(e)

    @staticmethod
    def check_single_channel(channel):
        url = channel[CONFIG['keys']['address']]
        status, error_msg = ChannelChecker.get_stream_status(url)
        ChannelChecker._log(status, url, error_msg)
        return channel if status in ("在线", "录像") else None

    @staticmethod
    async def check_available_channels(pt_data):
        start_time = time.time()
        channels = DataProcessor.sorted_unique_channels(pt_data)
        DataProcessor.log(f"【开始检测】共 {len(channels)} 个频道")
        with ThreadPoolExecutor(
            max_workers=CONFIG['concurrency']['max'] * CONFIG['concurrency']['channel_check_multiplier']
        ) as executor:
            futures = [executor.submit(ChannelChecker.check_single_channel, ch) for ch in channels]
            live_channels = [f.result() for f in concurrent.futures.as_completed(futures) if f.result()]
        elapsed = time.time() - start_time
        DataProcessor.log(f"【检测完成】可用: {len(live_channels)}/总数: {len(channels)} | 耗时: {elapsed:.1f}秒")
        DataProcessor.save_data(CONFIG['files']['ch_data'], live_channels)
        return live_channels

async def main():
    total_start = time.time()
    DataProcessor.log("【任务开始】...")
    pt_data = DataProcessor.read_data(CONFIG['files']['pt_data'])
    pt_data = DataProcessor.sync_urls(pt_data, CONFIG['url']['default'])
    DataProcessor.save_data(CONFIG['files']['pt_data'], pt_data)
    DataProcessor.log("【数据初始化完成】")
    await DataUpdater.update_platforms(pt_data)
    await DataUpdater.update_channels(pt_data)
    await ChannelChecker.check_available_channels(pt_data)
    total_elapsed = time.time() - total_start
    DataProcessor.log(f"【任务完成】总耗时: {total_elapsed:.2f}秒")

if __name__ == "__main__":
    asyncio.run(main())