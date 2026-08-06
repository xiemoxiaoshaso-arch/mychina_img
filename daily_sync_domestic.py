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

# 安全并发数
MAX_WORKERS = 3

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

# ==================== 3. 智能网页请求器（带 Playwright / Curl-Cffi 双保险） ====================
def fetch_html_content(url):
    # 优先使用直连加模拟指纹（madouqu.com 对 curl_cffi 的 chrome120 指纹兼容极好）
    try:
        resp = curl_requests.get(url, headers=HEADERS, impersonate="chrome120", timeout=15)
        if resp.status_code == 200:
            if "Just a moment" not in resp.text and len(resp.text) > 500:
                return resp.text
    except Exception:
        pass

    # 备用：若直连受阻，尝试通过 Playwright 无头浏览器渲染
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(user_agent=HEADERS["User-Agent"]).new_page()
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            time.sleep(2)
            html_text = page.content()
            browser.close()
            if html_text and len(html_text) > 500:
                return html_text
    except Exception as e:
        print(f"  ⚠️ Playwright 备用抓取异常: {e}")

    return None

# ==================== 4. 国产详情页解析模块 ====================
def parse_domestic_movie_detail(movie_url):
    time.sleep(random.uniform(1.0, 2.0))
    html = fetch_html_content(movie_url)
    if not html:
        return None

    # 拦截 Cloudflare 报错或登录页
    if "520:" in html or "502" in html or "just a moment" in html.lower():
        return None

    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # 提取标题
        title_tag = soup.find('h1') or soup.find('h2', class_='entry-title')
        title = title_tag.text.strip() if title_tag else "未知标题"
        
        # 从 URL 路径中提取唯一 Code（例如 /video/mnsc-mb-066/ -> MNSC-MB-066）
        code = movie_url.strip('/').split('/')[-1].upper()

        # 提取封面图
        cover_url = ""
        cover_img = soup.find('img', class_='attachment-large') or soup.find('meta', property='og:image')
        if cover_img:
            cover_url = cover_img.get('content') if cover_img.name == 'meta' else cover_img.get('src', '')

        # 提取剧照预览图
        preview_images = []
        for img in soup.select('.entry-content img, .photos img, .preview img'):
            src = img.get('src')
            if src and src != cover_url:
                preview_images.append(src)

        # 提取磁力链接
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
            "studio": "麻豆传媒",
            "series": "",
            "actors": [],
            "genres": ["国产传媒", "麻豆传媒"],
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

# ==================== 6. 🌟 对齐真实 HTML 的增量自适应多页扫描 ====================
def start_domestic_scan():
    existing_codes = get_existing_codes_from_api()
    new_movies = []
    page = 1
    consecutive_duplicates = 0
    duplicate_threshold = 10 
    stop_scanning = False

    print("🚀 开始国产区增量自适应多页扫描...")

    while True:
        # 🌟 对齐格式：https://madouqu.com/modelmedia/page/2/
        url = f"{DOMESTIC_HOST}/modelmedia/page/{page}/"
        print(f"📦 正在扫描国产列表页 (Page {page}): {url}")
        
        html = fetch_html_content(url)
        if not html:
            print(f"❌ 访问第 {page} 页失败（请求为空）")
            break

        soup = BeautifulSoup(html, 'html.parser')
        page_cards = []
        
        # 🌟【精准对齐】：根据你提供的源码，精确匹配 article.post 卡片及其内部的 h2.entry-title a 链接
        for article in soup.select("article.post, .posts-wrapper article"):
            a_tag = article.select_one("h2.entry-title a") or article.select_one("a[href*='/video/']")
            if a_tag:
                detail_url = a_tag.get("href")
                if detail_url and not detail_url.startswith('http'):
                    detail_url = DOMESTIC_HOST + detail_url
                
                # 从 /video/mnsc-mb-066/ 提取出大写的 MNSC-MB-066 作为唯一 Code
                slug = detail_url.strip('/').split('/')[-1]
                code = slug.upper()
                
                if code and detail_url and not any(m["code"] == code for m in page_cards):
                    page_cards.append({"code": code, "url": detail_url})

        print(f"🔍 [Debug] 当前页通过精准选择器提取到视频数: {len(page_cards)}")

        if not page_cards:
            print(f"🏁 扫描到第 {page} 页时没有发现任何视频卡片，自动结束扫描。")
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
