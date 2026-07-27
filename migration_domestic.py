import os
import sys
import time
import requests

# =================================================================
# 1. 自动读取新仓库的 Actions 密钥配置
# =================================================================
TELEGRAPH_DOMAIN = os.getenv("TELEGRAPH_DOMAIN")
CF_WORKER_DOMESTIC_API = os.getenv("CF_WORKER_DOMESTIC_API") # 🌟 例如 https://omchina.tjshida.workers.dev
CF_SECRET_TOKEN = os.getenv("CF_SECRET_TOKEN")

# 自动解析出 Base URL（防止配置中自带了末尾斜杠）
base_api = CF_WORKER_DOMESTIC_API.rstrip('/')

# 精准排查缺失哪个变量
missing = []
if not TELEGRAPH_DOMAIN: missing.append("TELEGRAPH_DOMAIN")
if not CF_WORKER_DOMESTIC_API: missing.append("CF_WORKER_DOMESTIC_API")
if not CF_SECRET_TOKEN: missing.append("CF_SECRET_TOKEN")

if missing:
    print(f"❌ 缺少以下必要的环境变量: {', '.join(missing)}")
    print("💡 请前往您新仓库的 Settings -> Secrets and variables -> Actions 中进行配置！")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {CF_SECRET_TOKEN}"
}

base_domain = TELEGRAPH_DOMAIN.rstrip('/')
if not base_domain.startswith(('http://', 'https://')):
    base_domain = 'https://' + base_domain

# 🌟 本地已清洗数据的备份原站下载域名（万一遇到相对路径，自动以此域名补全去原站拉取）
BACKUP_DOWNLOAD_HOST = "https://i0.wp.com/madouqu.com" 

img_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
}

# =================================================================
# 2. 核心任务：批量转存电影封面 (western_movies)
# =================================================================
def migrate_movie_covers():
    print("\n==================================================")
    print("🎬 开始处理 [电影封面] 转存任务...")
    print("==================================================")
    try:
        # 每次获取 500 部进行处理
        resp = requests.get(f"{base_api}/api/migration-pending?limit=500", headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"❌ 获取待处理封面名单失败: {resp.text}")
            return
        
        movies = resp.json().get("data", [])
        if not movies:
            print("🎉 赞！所有国产区影片的封面都已完成转存。")
            return
            
        print(f"📦 发现 {len(movies)} 个封面等待转存...")
        
        for idx, movie in enumerate(movies):
            code = movie["code"]
            orig_url = movie["cover_url"]
            
            # 相对路径智能保底补全
            if not orig_url.startswith(('http://', 'https://')):
                download_url = BACKUP_DOWNLOAD_HOST.rstrip('/') + "/" + orig_url.lstrip('/')
                print(f"[{idx+1}/{len(movies)}] 处理电影封面(补全): {code} -> {download_url}")
            else:
                download_url = orig_url
                print(f"[{idx+1}/{len(movies)}] 处理电影封面: {code} -> {download_url}")
            
            # 2.1 下载原图（标准 Python 异常捕获语法，已修正！）
            try:
                img_resp = requests.get(download_url, headers=img_headers, timeout=15)
                if img_resp.status_code != 200:
                    print(f"  ❌ 原图下载失败，状态码: {img_resp.status_code}")
                    continue
                img_content = img_resp.content
            except Exception as e:
                print(f"  ❌ 原图下载发生异常: {e}")
                continue

            # 2.2 上传到 Telegraph
            files = {'file': ('image.jpg', img_content, 'image/jpeg')}
            try:
                upload_resp = requests.post(f"{base_domain}/upload", files=files, timeout=25)
                if upload_resp.status_code == 200:
                    result = upload_resp.json()
                    if isinstance(result, list) and len(result) > 0:
                        new_img_url = f"{base_domain}{result[0]['src']}"
                        print(f"  ✅ 转存成功 -> {new_img_url}")
                        
                        # 2.3 成功后回写到 Cloudflare D1
                        update_payload = {
                            "code": code,
                            "cover_url": new_img_url
                        }
                        update_resp = requests.post(f"{base_api}/api/migration-update", json=update_payload, headers=headers, timeout=15)
                        if update_resp.status_code == 200:
                            print(f"  ✅ 数据库已同步更新！")
                        else:
                            print(f"  ❌ 回写数据库失败: {update_resp.text}")
                    else:
                        print(f"  ❌ 解析上传结果异常: {result}")
                elif upload_resp.status_code == 429:
                    print("  ⚠️ 触发 Telegraph 频控限流！本轮任务挂起，等待下一轮自动重跑。")
                    break
                else:
                    print(f"  ❌ 上传图床失败: {upload_resp.status_code}")
            except Exception as e:
                print(f"  ❌ 转存发生异常: {e}")
                
            time.sleep(1.5)
            
    except Exception as e:
        print(f"❌ 连接 Cloudflare 电影接口失败: {e}")

if __name__ == "__main__":
    migrate_movie_covers()
    print("\n🎉 本轮国产区资产转存任务全部执行完毕！")
