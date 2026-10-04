import re
import json
import aiohttp

async def resolve_ozon_link(url: str) -> str:
    # Вытаскиваем ссылку из текста сообщения
    match = re.search(r"https?://[^\s]+", url)
    if not match:
        return url
    target_url = match.group(0)

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(target_url, allow_redirects=True, timeout=10) as resp:
                # Берем итоговый URL после редиректов
                final_url = str(resp.url)
                if "/product/" in final_url:
                    return final_url
                
                # Если перекинуло на промежуточную страницу — читаем тело
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

    match = re.search(r"/product/[^/]*?-(\d{7,12})", url_or_id)
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
    # Используем публичный мобильный шлюз каталога
    api_url = f"https://api.allorigins.win/raw?url=https://www.ozon.ru/api/composer-api.bx/page/json/v2?url=/product/{item_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(api_url, timeout=15) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                widget_states = data.get("widgetStates", {})
                
                title = None
                price = None

                for key, state_str in widget_states.items():
                    if "webPrice" in key and not price:
                        try:
                            st = json.loads(state_str)
                            raw = st.get("price") or st.get("originalPrice")
                            if raw:
                                num = re.sub(r"[^\d]", "", str(raw))
                                if num:
                                    price = float(num)
                        except Exception:
                            pass

                    if "webProductHeading" in key and not title:
                        try:
                            st = json.loads(state_str)
                            title = st.get("title")
                        except Exception:
                            pass

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