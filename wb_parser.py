import aiohttp

async def get_wb_product_info(article: int) -> dict | None:
    url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={article}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                products = data.get("data", {}).get("products", [])
                if not products:
                    return None
                
                product = products[0]
                title = product.get("name", "Товар WB")
                brand = product.get("brand", "")
                full_title = f"{brand} {title}".strip()
                
                sizes = product.get("sizes", [])
                if not sizes:
                    return None
                price_total = sizes[0].get("price", {}).get("total")
                if not price_total:
                    return None
                
                return {
                    "article": article,
                    "title": full_title,
                    "price": float(price_total) / 100
                }
    except Exception:
        return None