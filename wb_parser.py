import re
import aiohttp

def extract_wb_article(text: str) -> int | None:
    # Ищем артикул в ссылке вида catalog/12345678/detail
    match = re.search(r"catalog/(\d+)/detail", text)
    if match:
        return int(match.group(1))
    # Или просто цифры в тексте
    digits = re.findall(r"\b\d{7,11}\b", text)
    if digits:
        return int(digits[0])
    return None

async def get_wb_product_info(article: int) -> dict | None:
    # Используем endpoint поиска, он часто работает стабильнее с иностранных IP
    url = f"https://search.wb.ru/exactmatch/ru/common/v4/search?curr=rub&dest=-1257786&query={article}&resultset=catalog"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://www.wildberries.ru/"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json(content_type=None)
                products = data.get("data", {}).get("products", [])
                
                # Ищем товар с точным совпадением ID
                product = None
                for p in products:
                    if p.get("id") == article:
                        product = p
                        break
                
                # Если не нашли точное совпадение, берем первый (на всякий случай)
                if not product and products:
                    product = products[0]

                if not product:
                    return None

                brand = product.get("brand", "")
                name = product.get("name", "Товар WB")
                title = f"{brand} {name}".strip()

                # Цена может быть в разных полях
                price_raw = product.get("salePriceU") or product.get("priceU")
                
                if not price_raw:
                    sizes = product.get("sizes", [])
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
    except Exception as e:
        print(f"WB Error: {e}")
        return None