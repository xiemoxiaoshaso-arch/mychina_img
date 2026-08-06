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
    url = f"{base_api}/api/recent-codes?limit=100"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode('utf-8')
            res_data = json.loads(res_body)
            if res_data.get("success"):
                existing = {row["code"].upper() for row in res_data.get("data", []) if "code" in row}
                print(f"✅ 初始化成功！成功加载最近的 {len(existing)} 个最新入库番号用于增量比对。")
                return existing
            else:
                print(f"⚠️ 初始化失败: {res_data.get('error')}")
    except Exception as e:
        print(f"⚠️ 无法通过 API 获取最近号码列表: {e}")
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
def parse_domestic_movie_detail(movie_url, pre_release_date):
    html = fetch_html_content(movie_url)
    # 正则提取 URL 中的唯一识别 ID（例如：tx4423），作为电影的 code (番号)
    id_match = re.search(r'/video/([^/]+)', html)
    jdb_code = id_match.group(1).upper() if id_match else f"MADOU-{str(int(time.time()))}"
    print(f"\n [采集线程] 正在抓取电影详情(识别码: {jdb_code})")
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        title_tag = soup.find('h1') or soup.find('h2') or soup.find('h3', class_='entry-title')
        title = title_tag.text.strip() if title_tag else "未知标题"

        # 🌟【100% 精准提取封面】：
        # 1. 优先寻找文章内容区域、媒体容器或文章内的第一张带 wp-image- 的大图
        cover_url = ""
        cover_img = (
            soup.select_one("article.post img[class*='wp-image-']") or
            soup.select_one(".entry-media img") or
            soup.select_one(".entry-content img") or
            soup.find('meta', property='og:image')
        )
        
        if cover_img:
            if cover_img.name == 'meta':
                cover_url = cover_img.get('content', '')
            else:
                cover_url = cover_img.get('src') or cover_img.get('data-src', '')
        
        # 2. 如果上面没找到，全页遍历寻找第一张包含 /wp-content/uploads/ 的有效大图
        if not cover_url or "avatar" in cover_url.lower():
            for img in soup.find_all('img'):
                src = img.get('src') or img.get('data-src', '')
                if src and '/wp-content/uploads/' in src and not any(x in src.lower() for x in ['avatar', 'logo', 'icon', 'spacer', 'pixel', 'banner']):
                    cover_url = src
                    break

        if cover_url and cover_url.startswith('//'):
            cover_url = 'https:' + cover_url           

        # 3.4 解析面包屑导航提取【片商】与【分类】
        studio = ""
        genres = []
        breadcrumb_div = soup.find('div', class_='breadcrumbs')
        if breadcrumb_div:
            category_links = breadcrumb_div.find_all('a')
            category_names = [a.text.strip() for a in category_links[1:]]
            if category_names:
                studio = category_names[-1]
                genres = category_names

        # 3.5 精准番号（code）提取：优先在文本中寻找“麻豆番号”标签
        code = ""
        entry_content = soup.find('div', class_='entry-content')
        if entry_content:
            for p in entry_content.find_all('p'):
                p_text = p.get_text().strip()
                match = re.search(r'(?:麻豆)?番号\s*[：:]\s*([a-zA-Z0-9_-]+)', p_text)
                if match:
                    code = match.group(1).strip().upper()
                    print(f"  🔍 从正文中精确捕获到官方番号: {code}")
                    break

        if not code:
            code = jdb_code
            print(f"  ℹ️ 该页面无番号标注，已降级使用路径标识符: {code}")

        # 3.6 多演员（actors）提取
        actors = []
        tags_div = soup.find('div', class_='entry-tags')
        if tags_div:
            for a in tags_div.find_all('a'):
                actor_name = a.text.strip()
                if actor_name and actor_name not in actors:
                    actors.append(actor_name)

        if not actors and entry_content:
            for p in entry_content.find_all('p'):
                p_text = p.get_text().strip()
                match = re.search(r'(?:麻豆女郎|演员)\s*[：:]\s*(.+)', p_text)
                if match:
                    raw_actors = match.group(1).strip()
                    split_actors = re.split(r'[、，,\s/]+', raw_actors)
                    for name in split_actors:
                        name = name.strip()
                        if name and name not in actors:
                            actors.append(name)
                    break

        if actors:
            print(f"  👥 提取到该片的参演演员: {actors}")

        # 3.7 提取剧照列表
        preview_images = [cover_url] if cover_url else []

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
            "release_date": pre_release_date,
            "duration": "",
            "director": "",
            "studio": studio,
            "series": "",
            "actors": actors,
            "genres": genres,
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
        
        # 🌟【完全对齐源码】：精准遍历每一个 <article class="post"> 卡片
        for article in soup.select("article.post"):
            # 提取标题处的链接（包含 /video/ 的 a 标签）
            a_tag = article.select_one("h2.entry-title a[href*='/video/']")
            if not a_tag:
                a_tag = article.select_one("a[href*='/video/']")
                
            if not a_tag:
                continue
                
            detail_url = a_tag.get("href")
            if not detail_url:
                continue
                
            if not detail_url.startswith('http'):
                detail_url = DOMESTIC_HOST + detail_url

            # 🌟 提取列表卡片中的绝对日期 (如 2026-08-05)
            release_date = time.strftime("%Y-%m-%d")
            time_tag = article.select_one("time[datetime]")
            if time_tag and time_tag.get("datetime"):
                dt_str = time_tag.get("datetime")
                if len(dt_str) >= 10:
                    release_date = dt_str[:10]
            
            # 🌟【精准提取番号】：从 title 属性（如 "MNSC-MB-066 落地窗前蜜穴榨精"）中通过正则切出标准番号
            raw_title = a_tag.get("title") or a_tag.text.strip()
            code_match = re.search(r'^([A-Z0-9\-]+)', raw_title, re.IGNORECASE)
            code = code_match.group(1).upper() if code_match else ""
            
            # 保底：如果标题里没截取到，从 URL 尾部切（如 mnsc-mb-066 -> MNSC-MB-066）
            if not code or len(code) < 3:
                code = detail_url.strip('/').split('/')[-1].upper()

            if code and not any(m["code"] == code for m in page_cards):
                # 🌟 将 url 和 release_date 一起打包存入列表
                page_cards.append({"code": code, "url": detail_url, "release_date": release_date})

        print(f"🔍 [Debug] 当前页通过精确定位成功提取到视频数: {len(page_cards)}")

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
        release_date = m["release_date"] # 获取列表页的日期
        try:
            print(f"  ⏳ 正在抓取详情页: {code} -> {url} (日期: {release_date})")
            movie_data = parse_domestic_movie_detail(url, release_date)
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
