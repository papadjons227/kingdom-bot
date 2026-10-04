import re
import json
import aiohttp

async def resolve_ozon_link(url: str) -> str:
    match = re.search(r"https?://[^\s]+", url)
    if not match:
        return url
    target_url = match.group(0)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(target_url, allow_redirects=True, timeout=8) as resp:
                return str(resp.url)
    except Exception:
        return target_url

def extract_ozon_id(url_or_id: str) -> int | None:
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return int(url_or_id)

    # Ищем SKU в URL: -3765271793/? или sku=3765271793
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
    # 1. Пробуем легкий мобильный endpoint с User-Agent поискового робота
    target_url = f"https://www.ozon.ru/product/{item_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(target_url, timeout=10) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    
                    # Ищем микроразметку schema.org/Product
                    schema_match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
                    if schema_match:
                        try:
                            sdata = json.loads(schema_match.group(1))
                            title = sdata.get("name")
                            offers = sdata.get("offers", {})
                            price = offers.get("price")
                            if title and price:
                                return {
                                    "article": item_id,
                                    "title": title[:100],
                                    "price": float(price),
                                    "platform": "ozon"
                                }
                        except Exception:
                            pass

                    # Резервный парсинг по meta-тегам
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