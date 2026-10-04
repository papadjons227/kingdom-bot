import re
import aiohttp

def extract_wb_article(text: str) -> int | None:
    match = re.search(r"catalog/(\d+)/detail", text)
    if match:
        return int(match.group(1))
    digits = re.findall(r"\b\d{7,11}\b", text)
    if digits:
        return int(digits[0])
    return None

def get_basket_number(article: int) -> str:
    vol = article // 100000
    if 0 <= vol <= 143: return "01"
    elif 144 <= vol <= 287: return "02"
    elif 288 <= vol <= 431: return "03"
    elif 432 <= vol <= 719: return "04"
    elif 720 <= vol <= 1007: return "05"
    elif 1008 <= vol <= 1061: return "06"
    elif 1062 <= vol <= 1115: return "07"
    elif 1116 <= vol <= 1169: return "08"
    elif 1170 <= vol <= 1313: return "09"
    elif 1314 <= vol <= 1601: return "10"
    elif 1602 <= vol <= 1655: return "11"
    elif 1656 <= vol <= 1919: return "12"
    elif 1920 <= vol <= 2045: return "13"
    elif 2046 <= vol <= 2189: return "14"
    elif 2190 <= vol <= 2405: return "15"
    elif 2406 <= vol <= 2621: return "16"
    elif 2622 <= vol <= 2837: return "17"
    elif 2838 <= vol <= 3053: return "18"
    elif 3054 <= vol <= 3269: return "19"
    elif 3270 <= vol <= 3485: return "20"
    elif 3486 <= vol <= 3701: return "21"
    elif 3702 <= vol <= 3917: return "22"
    elif 3918 <= vol <= 4133: return "23"
    elif 4134 <= vol <= 4349: return "24"
    elif 4350 <= vol <= 4565: return "25"
    elif 4566 <= vol <= 5000: return "26"
    elif 5001 <= vol <= 7000: return "27"
    else: return "28"

async def get_wb_product_info(article: int) -> dict | None:
    vol = article // 100000
    part = article // 1000
    basket = get_basket_number(article)
    
    # 1. Забираем карточку с CDN корзины (работает отовсюду без блокировок)
    card_url = f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/ru/card.json"
    price_url = f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/price-history.json"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Accept": "*/*"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            title = "Товар Wildberries"
            async with session.get(card_url, timeout=7) as resp:
                if resp.status == 200:
                    cdata = await resp.json(content_type=None)
                    name = cdata.get("imt_name") or cdata.get("subj_name", "")
                    brand = cdata.get("selling", {}).get("brand_name", "")
                    title = f"{brand} {name}".strip() or title

            # 2. Цену забираем из истории цен корзины или через мобильный search
            price = None
            async with session.get(price_url, timeout=7) as resp:
                if resp.status == 200:
                    pdata = await resp.json(content_type=None)
                    if pdata and isinstance(pdata, list):
                        # Последняя запись в истории содержит актуальную цену в копейках
                        price = float(pdata[-1].get("price", {}).get("RUB", 0)) / 100

            # Если в истории пусто — дергаем fallback
            if not price or price == 0:
                fb_url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={article}"
                async with session.get(fb_url, timeout=7) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        prods = data.get("data", {}).get("products", [])
                        if prods:
                            sizes = prods[0].get("sizes", [])
                            if sizes:
                                p_raw = sizes[0].get("price", {}).get("total")
                                if p_raw:
                                    price = float(p_raw) / 100

            if price and price > 0:
                return {
                    "article": article,
                    "title": title,
                    "price": price,
                    "platform": "wb"
                }
    except Exception:
        pass
    return None