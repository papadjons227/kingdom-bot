import re
import aiohttp

def extract_ozon_id(url_or_id: str) -> int | None:
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return int(url_or_id)
    
    # Ищем ID после тире или в /product/
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

async def resolve_ozon_link(url: str) -> str:
    match = re.search(r"https?://[^\s]+", url)
    if not match: 
        return url
    target_url = match.group(0)
    
    # Если уже есть ID, не делаем лишних запросов
    if extract_ozon_id(target_url):
        return target_url

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(target_url, allow_redirects=True, timeout=8) as resp:
                return str(resp.url)
    except Exception:
        return target_url

async def get_ozon_product_info(item_id: int) -> dict | None:
    # Пробуем мобильный API Ozon (часто работает лучше веб-версии)
    url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{item_id}"
    
    headers = {
        "User-Agent": "ozonapp_android/16.48.0 (Linux; U; Android 14; ru_RU)",
        "Accept": "application/json",
        "Client-Name": "ozonapp_android",
        "Host": "api.ozon.ru"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=8) as resp:
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
    except Exception as e:
        print(f"Ozon Mobile API Error: {e}")

    # Фоллбэк: если мобильный API не сработал, пробуем парсить HTML через прокси (на крайняк)
    # Но обычно мобильный API хватает. Если нет - значит Ozon полностью закрылся для этого IP.
    return None