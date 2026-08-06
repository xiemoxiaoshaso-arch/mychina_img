import os
import sys
import time
import re
import json
import random
import urllib.request
from urllib.parse import quote
from bs4 import BeautifulSoup
import requests # 🌟【核心修复】：确保导入了 requests 库供 FlareSolverr 通信使用
from curl_cffi import requests as curl_requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright

# ==================== 1. 国产区专属配置区 ====================
DOMESTIC_HOST = "https://madouqu.com"

# 🌟 从环境变量中读取国产区 Worker API 密钥
CF_WORKER_DOMESTIC_API = os.getenv("CF_WORKER_DOMESTIC_API") # 例如 https://omchina.tjshida.workers.dev/api/movie
CF_SECRET_TOKEN = os.getenv("CF_SECRET_TOKEN")

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

# ==================== 3. 🌟 增强型 Playwright 无头浏览器过盾器 ====================
def fetch_html_content(url):
    """
    使用 Playwright 模拟真实浏览器，设置 networkidle 等待状态，完美过盾
    """
    print(f"📡 正在通过无头浏览器访问: {url}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()
            
            # 访问网页，等待网络完全空闲（代表 Cloudflare 5秒盾已经自动解开并渲染完毕）
            page.goto(url, timeout=60000, wait_until="networkidle")
            
            # 额外的保险等待
            time.sleep(2)
            
            html_text = page.content()
            browser.close()
            
            if html_text and len(html_text) > 500:
                print(f"📡 [浏览器抓取成功] 返回网页前 300 字符:\n{html_text[:300]}")
                return html_text
            else:
                print("  ⚠️ 浏览器抓取到的页面内容过短或为空。")
    except Exception as e:
        print(f"  ❌ Playwright 浏览器渲染发生异常: {e}")
        
    return None

# ==================== 4. 国产详情页解析模块 ====================
def parse_domestic_movie_detail(movie_url):
    html = fetch_html_content(movie_url)
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # 拦截 Cloudflare 错误页
        html_lower = html.lower()
        if "520:" in html_lower or "502" in html_lower or "just a moment" in html_lower:
            print(f"  ❌ [拦截废弃] 详情页遭遇防爬盾牌，拒绝入库！")
            return None

        title_tag = soup.find('h1') or soup.find('h2') or soup.find('h3', class_='entry-title')
        title = title_tag.text.strip() if title_tag else "未知标题"
        code = movie_url.strip('/').split('/')[-1].upper()

        if code.isdigit() or len(code) < 3:
            return None

        cover_url = ""
        cover_img = soup.find('img', class_='cover') or soup.find('meta', property='og:image') or soup.find('div', class_='entry-media').find('img') if soup.find('div', class_='entry-media') else None
        if cover_img:
            cover_url = cover_img.get('content') if cover_img.name == 'meta' else cover_img.get('src', '') or cover_img.get('data-src', '')
            if cover_url and cover_url.startswith('//'):
                cover_url = 'https:' + cover_url

        preview_images = []
        for img in soup.select('.photos img, .preview img, .entry-content img'):
            src = img.get('src') or img.get('data-src')
            if src and "avatar" not in src and "logo" not in src:
                if src.startswith('//'): src = 'https:' + src
                preview_images.append(src)

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

# ==================== 6. 增量自适应多页扫描控制流（单线程稳健版） ====================
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
        
        for a in soup.select("a[href*='/video/']"):
            href = a.get("href")
            if href and href.count('/') >= 2:
                detail_url = href if href.startswith('http') else DOMESTIC_HOST + href
                code = href.strip('/').split('/')[-1].upper()
                if code and not any(m["code"] == code for m in page_cards):
                    page_cards.append({"code": code, "url": detail_url})

        print(f"🔍 [Debug] 当前页通过精准选择器提取到视频数: {len(page_cards)}")

        if not page_cards:
            print(f"🏁 扫描到第 {page} 页时没有发现任何链接，自动结束扫描。")
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

    print(f"🌟 发现 【{len(new_movies)}】 部全新的国产视频，开始串行安全抓取详情并实时推送...")

    # 🌟 改为单线程串行安全抓取（彻底避免多开浏览器引起的内存崩溃和 Cloudflare 拦截）
    for m in new_movies:
        code = m["code"]
        url = m["url"]
        try:
            print(f"  ⏳ 正在抓取详情页: {code} -> {url}")
            movie_data = parse_domestic_movie_detail(url)
            if movie_data:
                success, error_msg = post_movie_to_api(movie_data)
                if success:
                    print(f"  ✅ [同步成功] 国产视频 [{code}] 已成功录入线上 D1 数据库")
                else:
                    print(f"  ❌ [同步失败] 国产视频 [{code}] 推送失败: {error_msg}")
            else:
                print(f"  ❌ [解析失败] 标识码: {code}")
        except Exception as exc:
            print(f"  ❌ 标识码 {code} 执行异常: {exc}")
        
        time.sleep(2) # 礼貌延时

    print("\n🎉【国产区每日增量同步任务全部执行成功！】")

if __name__ == "__main__":
    start_domestic_scan()
