import re
import aiohttp

async def resolve_ozon_link(url: str) -> str:
    match = re.search(r"https?://[^\s]+", url)
    if not match:
        return url
    target_url = match.group(0)

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(target_url, allow_redirects=True, timeout=8) as resp:
                final_url = str(resp.url)
                if "/product/" in final_url:
                    return final_url
                text = await resp.text()
                link_match = re.search(r'href="([^"]*product/[^"]*)"', text)
                if link_match:
                    return link_match.group(1)
                return final_url
    except Exception:
        return target_url

def extract_ozon_id(url_or_id: str) -> int | None:
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return int(url_or_id)

    # Ищем ID товара в конце слага: ...ekran-5369786946/?...
    match = re.search(r"-(\d{7,12})(?:/|\?|$)", url_or_id)
    if match:
        return int(match.group(1))

    match_direct = re.search(r"/product/(\d{7,12})", url_or_id)
    if match_direct:
        return int(match_direct.group(1))

    match_sku = re.search(r"sku=(\d{7,12})", url_or_id)
    if match_sku:
        return int(match_sku.group(1))

    return None

async def get_ozon_product_info(item_id: int) -> dict | None:
    # Запрос напрямую к мобильному API Ozon
    api_url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{item_id}"
    headers = {
        "User-Agent": "ozonapp_android/16.48.0",
        "Accept": "application/json",
        "Host": "api.ozon.ru"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(api_url, timeout=8) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    tracking_info = data.get("trackingInfo", {})
                    title = tracking_info.get("name")
                    price = tracking_info.get("price")
                    
                    if title and price:
                        return {
                            "article": item_id,
                            "title": title[:100],
                            "price": float(price),
                            "platform": "ozon"
                        }
    except Exception:
        pass

    # Резервный шлюз OpenGraph метаданных
    try:
        page_url = f"https://www.ozon.ru/product/{item_id}"
        backup_headers = {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept": "text/html"
        }
        async with aiohttp.ClientSession(headers=backup_headers) as session:
            async with session.get(page_url, timeout=8) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    title_m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
                    price_m = re.search(r'"price":\s*"(\d+)"', html) or re.search(r'itemprop="price" content="(\d+)"', html)
                    
                    if title_m and price_m:
                        return {
                            "article": item_id,
                            "title": title_m.group(1)[:100],
                            "price": float(price_m.group(1)),
                            "platform": "ozon"
                        }
    except Exception:
        pass

    return None