import re
import json
import aiohttp

async def resolve_ozon_link(url: str) -> str:
    """Разворачивает короткие ссылки ozon.ru/t/... через HTTP-редирект"""
    if "/t/" not in url:
        return url

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, allow_redirects=True, timeout=8) as resp:
                return str(resp.url)
    except Exception:
        return url

def extract_ozon_id(url_or_id: str) -> int | None:
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return int(url_or_id)

    match = re.search(r"product/.*?(\d{7,12})", url_or_id)
    if match:
        return int(match.group(1))

    match_sku = re.search(r"sku=(\d{7,12})", url_or_id)
    if match_sku:
        return int(match_sku.group(1))

    return None

async def get_ozon_product_info(item_id: int) -> dict | None:
    api_url = f"https://api.allorigins.win/raw?url=https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{item_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(api_url, timeout=12) as response:
                if response.status != 200:
                    return None
                data = await response.json(content_type=None)

                widget_states = data.get("widgetStates", {})
                title = None
                price = None

                for key, state_str in widget_states.items():
                    if "webPrice" in key and not price:
                        state = json.loads(state_str)
                        raw_price = state.get("price") or state.get("originalPrice")
                        if raw_price:
                            clean = re.sub(r"[^\d]", "", str(raw_price))
                            if clean:
                                price = float(clean)

                    if "webProductHeading" in key and not title:
                        state = json.loads(state_str)
                        title = state.get("title")

                if price and title:
                    return {
                        "article": item_id,
                        "title": title[:100],
                        "price": price,
                        "platform": "ozon"
                    }
    except Exception:
        pass

    return None