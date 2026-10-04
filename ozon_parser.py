import re
import aiohttp

def extract_ozon_id(url_or_id: str) -> int | None:
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return int(url_or_id)

    # Ищем числовой ID перед ? или /
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

    # Если уже есть SKU/ID в ссылке, не тратим время на сетевой редирект
    if extract_ozon_id(target_url):
        return target_url

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(target_url, allow_redirects=True, timeout=8) as resp:
                return str(resp.url)
    except Exception:
        return target_url

async def get_ozon_product_info(item_id: int) -> dict | None:
    # Открытый прокси-агрегатор, который отдает разметку без блокировки Cloudflare
    gateway_url = f"https://api.scraperapi.com/structured/ozon/product?sku={item_id}"
    
    # Резервный публичный парсер карточек
    proxy_url = f"https://api.allorigins.win/get?url={aiohttp.helpers.quote(f'https://www.ozon.ru/product/{item_id}', safe='')}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(proxy_url, timeout=12) as resp:
                if resp.status == 200:
                    res_json = await resp.json(content_type=None)
                    html = res_json.get("contents", "")

                    # 1. Забираем Title
                    title = None
                    t_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
                    if t_match:
                        title = t_match.group(1).replace(" — купить в интернет-магазине OZON", "").strip()
                    elif "<title>" in html:
                        t_match2 = re.search(r"<title>([^<]+)</title>", html)
                        if t_match2:
                            title = t_match2.group(1).split(" — ")[0].strip()

                    # 2. Забираем цену
                    price = None
                    p_match = re.search(r'"price":\s*"(\d+)"', html) or re.search(r'"finalPrice":\s*(\d+)', html) or re.search(r'itemprop="price" content="(\d+)"', html)
                    if p_match:
                        price = float(p_match.group(1))

                    if title and price:
                        return {
                            "article": item_id,
                            "title": title[:100],
                            "price": price,
                            "platform": "ozon"
                        }
    except Exception:
        pass

    return None