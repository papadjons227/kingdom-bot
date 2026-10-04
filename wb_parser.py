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

async def get_wb_product_info(article: int) -> dict | None:
    # Используем мобильный API шлюз WB, который не режет зарубежные IP
    url = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={article}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.wildberries.ru",
        "Referer": f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                products = data.get("data", {}).get("products", [])
                if not products:
                    return None
                
                prod = products[0]
                brand = prod.get("brand", "")
                name = prod.get("name", "Товар WB")
                title = f"{brand} {name}".strip()

                # Проверяем цену: сначала salePriceU, затем sizes
                price_raw = prod.get("salePriceU") or prod.get("priceU")
                if not price_raw:
                    sizes = prod.get("sizes", [])
                    if sizes:
                        price_raw = sizes[0].get("price", {}).get("total")

                if not price_raw:
                    return None

                return {
                    "article": article,
                    "title": title,
                    "price": float(price_raw) / 100,
                    "platform": "wb"
                }
    except Exception:
        return None