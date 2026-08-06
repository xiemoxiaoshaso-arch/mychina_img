import os
import sys
import time
import re
import json
import random
import urllib.request
from urllib.parse import quote
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 1. 国产区专属配置区 ====================
DOMESTIC_HOST = "https://madouqu.com"

# 安全并发数
MAX_WORKERS = 3

# 🌟 从环境变量中读取国产区 Worker API 密钥
CF_WORKER_DOMESTIC_API = os.getenv("CF_WORKER_DOMESTIC_API") # 例如 https://omchina.tjshida.workers.dev/api/movie
CF_SECRET_TOKEN = os.getenv("CF_SECRET_TOKEN")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY")

missing_vars = []
if not CF_WORKER_DOMESTIC_API: missing_vars.append("CF_WORKER_DOMESTIC_API")
if not CF_SECRET_TOKEN: missing_vars.append("CF_SECRET_TOKEN")

if missing_vars:
    print(f"❌ 启动失败！检测到当前系统环境中缺少以下必要变量: {', '.join(missing_vars)}")
    sys.exit(1)

base_api = CF_WORKER_DOMESTIC_API.rstrip('/')
if base_api.endswith('/api/movie'):
    base_api = base_api[:-10]
elif base_api.endswith('/api/movie/'):
    base_api = base_api[:-11]

headers = {
    "Authorization": f"Bearer {CF_SECRET_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
}

# ==================== 2. 获取线上国产 D1 已有番号 ====================
def get_existing_codes_from_api():
    print("🔍 正在拉取国产区线上 D1 数据库中的已入库番号大名单...")
    url = f"{base_api}/api/movies?limit=100000"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode('utf-8')
            res_data = json.loads(res_body)
            if res_data.get("success"):
                existing = {row["code"].upper() for row in res_data.get("data", []) if "code" in row}
                print(f"✅ 初始化成功！国产区云端数据库中已存在 {len(existing)} 部电影。")
                return existing
            else:
                print(f"⚠️ 初始化失败: {res_data.get('error')}")
    except Exception as e:
        print(f"⚠️ 无法通过 API 获取云端号码列表，将进行全量更新比对: {e}")
    return set()

# ==================== 3. 🌟 接入免费开源 FlareSolverr 过盾器 ====================
def fetch_html_content(url):
    """
    通过在 GitHub Actions 本地运行的 FlareSolverr 容器自动绕过 Cloudflare 5秒盾
    """
    flaresolverr_url = "http://localhost:8191/v1"
    
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": 60000
    }
    
    print(f"📡 正在通过本地 FlareSolverr 免费过盾请求: {url}")
    try:
        resp = requests.post(flaresolverr_url, json=payload, timeout=70)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("status") == "ok":
                html_text = result.get("solution", {}).get("html", "")
                print(f"📡 [FlareSolverr 成功] 返回网页前 300 字符:\n{html_text[:300]}")
                return html_text
            else:
                print(f"  ⚠️ FlareSolverr 返回状态异常: {result.get('status')}")
        else:
            print(f"  ⚠️ FlareSolverr 接口 HTTP 异常: {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️ 连接本地 FlareSolverr 发生异常: {e}")
        
    return None

# ==================== 4. 国产详情页解析模块 ====================
def parse_domestic_movie_detail(movie_url):
    time.sleep(random.uniform(1.0, 2.0))
    html = fetch_html_content(movie_url)
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        title_tag = soup.find('h1') or soup.find('h2')
        title = title_tag.text.strip() if title_tag else "未知标题"
        code = movie_url.strip('/').split('/')[-1].upper()

        cover_url = ""
        cover_img = soup.find('img', class_='cover') or soup.find('meta', property='og:image')
        if cover_img:
            cover_url = cover_img.get('content') if cover_img.name == 'meta' else cover_img.get('src', '')

        preview_images = []
        for img in soup.select('.photos img, .preview img'):
            src = img.get('src')
            if src: preview_images.append(src)

        magnets = []
        for a in soup.find_all('a', href=True):
            link = a['href']
            if link.startswith('magnet:'):
                magnets.append({
                    "title": title,
                    "link": link,
                    "size": "未知大小",
                    "share_date": "",
                    "hd": True
                })

        return {
            "code": code,
            "title": title,
            "cover_url": cover_url,
            "release_date": time.strftime("%Y-%m-%d"),
            "duration": "120",
            "director": "",
            "studio": "Madouqu",
            "series": "",
            "actors": [],
            "genres": ["国产传媒"],
            "preview_images": preview_images,
            "magnets": magnets,
            "rating_score": 0.0,
            "rating_users": 0,
            "preview_video_url": ""
        }
    except Exception:
        return None

# ==================== 5. 推送至 D1 API ====================
def post_movie_to_api(movie_data):
    url = f"{base_api}/api/movie"
    try:
        req_body = json.dumps(movie_data, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode('utf-8')
            res_data = json.loads(res_body)
            if res_data.get("success"):
                return True, ""
            else:
                return False, res_data.get("error", "未知错误")
    except Exception as e:
        return False, str(e)

# ==================== 6. 增量自适应多页扫描控制流 ====================
def start_domestic_scan():
    existing_codes = get_existing_codes_from_api()
    new_movies = []
    page = 1
    consecutive_duplicates = 0
    duplicate_threshold = 10 
    stop_scanning = False

    print("🚀 开始国产区增量自适应多页扫描...")

    while True:
        url = f"{DOMESTIC_HOST}/modelmedia/page/{page}/"
        print(f"📦 正在扫描国产列表页 (Page {page}): {url}")
        
        html = fetch_html_content(url)
        
        if not html:
            print(f"❌ 访问第 {page} 页失败（请求为空）")
            break

        soup = BeautifulSoup(html, 'html.parser')
        page_cards = []
        
        for a in soup.select("a[href*='/modelmedia/']"):
            href = a.get("href")
            if href and href != "/modelmedia/" and href.count('/') >= 2:
                detail_url = href if href.startswith('http') else DOMESTIC_HOST + href
                code = href.strip('/').split('/')[-1].upper()
                if code and not any(m["code"] == code for m in page_cards):
                    page_cards.append({"code": code, "url": detail_url})

        print(f"🔍 [Debug] 当前页通过选择器初步提取到链接数: {len(page_cards)}")

        if not page_cards:
            print(f"🏁 扫描到第 {page} 页时没有发现任何链接，打印当前页面前 400 字符供分析：\n{html[:400]}")
            break

        page_new_count = 0
        for m in page_cards:
            if m["code"] in existing_codes:
                consecutive_duplicates += 1
                if consecutive_duplicates >= duplicate_threshold:
                    print(f"🎯【数据追平】已连续匹配到 {consecutive_duplicates} 个已入库番号，停止后续扫描！")
                    stop_scanning = True
                    break
            else:
                consecutive_duplicates = 0
                new_movies.append(m)
                page_new_count += 1

        print(f"  └─ Page {page} 扫描完毕，发现全新视频: {page_new_count} 部 | 当前连续重复累计数: {consecutive_duplicates}")

        if stop_scanning:
            break

        page += 1
        if page > 15:
            print("⚠️ 达到单次最大扫描页数限制（15页），安全退出。")
            break

    if not new_movies:
        print("🎉【无漏网之鱼】国产区云端 D1 数据库已被补至最新，本轮无新增内容。")
        return

    print(f"🌟 开始针对 【{len(new_movies)}】 部全新的国产视频进行并发详情页抓取与实时推送...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(parse_domestic_movie_detail, m["url"]): m["code"] for m in new_movies}
        for future in as_completed(futures):
            code = futures[future]
            try:
                movie_data = future.result()
                if movie_data:
                    success, error_msg = post_movie_to_api(movie_data)
                    if success:
                        print(f"  ✅ [同步成功] 国产视频 [{code}] 已成功录入线上 D1 数据库")
                    else:
                        print(f"  ❌ [同步失败] 国产视频 [{code}] 推送失败: {error_msg}")
                else:
                    print(f"  ❌ [解析失败] 标识码: {code}")
            except Exception as exc:
                print(f"  ❌ 标识码 {code} 线程崩溃: {exc}")

    print("\n🎉【国产区每日增量同步任务全部执行成功！】")

if __name__ == "__main__":
    start_domestic_scan()
