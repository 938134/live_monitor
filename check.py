#!/usr/bin/env python3
"""
check.py - 优化版RTMP流检测工具
1. 只取 pt.json 中 result=1 的平台
2. URL 去重
3. 区分 rtmp / 非 rtmp 检测
4. 使用高效RTMP握手检测替代FFmpeg完整检测
"""

import json, logging, socket, struct, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import urllib.parse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.info

PT_FILE = "pt.json"
CH_FILE = "ch.json"
WORKERS = 200
RTMP_TIMEOUT = 2  # RTMP握手超时时间(秒)
FFMPEG_TIMEOUT = 5  # ffmpeg探测超时时间(秒)

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else []

def parse_rtmp_url(url):
    """解析RTMP URL，返回(host, port, app, stream)"""
    if not url.startswith("rtmp://"):
        return None, None, None, None
    
    # 移除协议头
    url = url[7:]
    
    # 分割主机和路径
    if '/' not in url:
        return url, 1935, "", ""
    
    host_part, path_part = url.split('/', 1)
    
    # 处理主机和端口
    if ':' in host_part:
        host, port_str = host_part.split(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 1935
    else:
        host = host_part
        port = 1935
    
    # 处理应用和流路径
    if '/' in path_part:
        app, stream = path_part.split('/', 1)
    else:
        app = path_part
        stream = ""
    
    # 移除查询参数
    if '?' in stream:
        stream = stream.split('?', 1)[0]
    
    return host, port, app, stream

def rtmp_handshake(host, port, timeout=RTMP_TIMEOUT):
    """执行RTMP握手协议检测"""
    try:
        # 创建TCP套接字
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        # 连接到服务器
        sock.connect((host, port))
        
        # 发送握手请求
        # C0: 协议版本 (1字节)
        # C1: 时间戳 (4字节) + 零 (4字节) + 随机数据 (1528字节)
        handshake = b'\x03' + struct.pack('>I', int(time.time())) + b'\x00'*4 + os.urandom(1528)
        sock.sendall(handshake)
        
        # 接收S0: 协议版本 (1字节)
        response = sock.recv(1)
        
        # 关闭连接
        sock.close()
        
        return response == b'\x03'
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False
    except Exception as e:
        log(f"RTMP握手错误: {str(e)}")
        return False

def _check_rtmp(url):
    """优化版RTMP流检测 - 使用握手协议"""
    host, port, app, stream = parse_rtmp_url(url)
    
    if host is None:
        log(f"[ERROR] 无效RTMP URL: {url}")
        return False
    
    # 第一步：检查服务器是否响应
    server_ok = rtmp_handshake(host, port)
    
    if not server_ok:
        return False
    
    # 可选：如果需要更高准确性，可以添加流存在性检查
    # 这里为了性能，只做服务器握手检测
    return True

def _check_other(url):
    """非RTMP流检测 - 使用ffprobe"""
    try:
        import ffmpeg
        probe = ffmpeg.probe(url, timeout=FFMPEG_TIMEOUT)
        return "streams" in probe and len(probe["streams"]) > 0
    except ImportError:
        # 回退到ffmpeg命令行
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=FFMPEG_TIMEOUT * 2,
            )
            return result.returncode == 0
        except Exception:
            return False
    except Exception as e:
        return False

def check_one(channel):
    """检测单个频道"""
    url = channel["address"]
    name = channel.get("title", "unknown")
    
    if url.endswith(".mp4"):
        log(f"[KEEP] 录像 {name} {url}")
        return channel
    
    if url.startswith("rtmp://"):
        ok = _check_rtmp(url)
    else:
        ok = _check_other(url)
    
    status = "✅ 在线" if ok else "❌ 离线"
    log(f"{status} {name} {url}")
    return channel if ok else None

def main():
    start = time.time()
    pt = load(PT_FILE)
    
    # 1. result=1 且去重
    channels = []
    seen = set()
    for src in pt:
        if src.get("result") != 1:
            continue
        for pf in src.get("pingtai", []):
            for ch in pf.get("zhubo", []):
                url = ch.get("address")
                if url and url not in seen:
                    seen.add(url)
                    channels.append(ch)
    
    log(f"[LOAD] 加载 {len(channels)} 个唯一频道")
    
    # 2. 并发检测
    live = []
    failed = 0
    
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        # 提交所有任务
        future_to_ch = {executor.submit(check_one, ch): ch for ch in channels}
        
        # 处理完成的任务
        for future in as_completed(future_to_ch):
            ch = future_to_ch[future]
            try:
                result = future.result()
                if result:
                    live.append(result)
            except Exception as e:
                failed += 1
                log(f"[ERROR] 检测失败: {ch.get('title', 'unknown')} - {str(e)}")
    
    # 3. 保存结果
    Path(CH_FILE).write_text(json.dumps(live, ensure_ascii=False, indent=2))
    
    # 4. 生成统计报告
    total = len(channels)
    success = len(live)
    elapsed = time.time() - start
    
    log(f"[REPORT] 总数: {total} | 在线: {success} | 离线: {total - success} | 失败: {failed}")
    log(f"[REPORT] 成功率: {success/total:.2%} | 耗时: {elapsed:.2f}秒 | 速率: {total/elapsed:.2f}个/秒")
    log(f"[SAVED] 结果保存至 {CH_FILE}")

if __name__ == "__main__":
    main()
