import aiohttp

def get_wb_basket_host(article: int) -> str:
    vol = article // 100000
    part = article // 1000

    if 0 <= vol <= 143: basket = "01"
    elif 144 <= vol <= 287: basket = "02"
    elif 288 <= vol <= 431: basket = "03"
    elif 432 <= vol <= 719: basket = "04"
    elif 720 <= vol <= 1007: basket = "05"
    elif 1008 <= vol <= 1061: basket = "06"
    elif 1062 <= vol <= 1115: basket = "07"
    elif 1116 <= vol <= 1169: basket = "08"
    elif 1170 <= vol <= 1313: basket = "09"
    elif 1314 <= vol <= 1601: basket = "10"
    elif 1602 <= vol <= 1655: basket = "11"
    elif 1656 <= vol <= 1919: basket = "12"
    elif 1920 <= vol <= 2045: basket = "13"
    elif 2046 <= vol <= 2189: basket = "14"
    elif 2190 <= vol <= 2405: basket = "15"
    elif 2406 <= vol <= 2621: basket = "16"
    elif 2622 <= vol <= 2837: basket = "17"
    elif 2838 <= vol <= 3053: basket = "18"
    else: basket = "19"

    return f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/ru/card.json"

async def get_wb_product_info(article: int) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. Забираем точные метаданные (название и бренд)
            card_url = get_wb_basket_host(article)
            title = "Товар Wildberries"
            async with session.get(card_url, timeout=8) as r:
                if r.status == 200:
                    cdata = await r.json(content_type=None)
                    name = cdata.get("imt_name") or cdata.get("subj_name", "")
                    brand = cdata.get("selling", {}).get("brand_name", "")
                    title = f"{brand} {name}".strip()

            # 2. Забираем актуальную цену из общего API каталога
            detail_url = f"https://card.wb.ru/cards/v1/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={article}"
            async with session.get(detail_url, timeout=8) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                products = data.get("data", {}).get("products", [])
                if not products:
                    return None
                
                prod = products[0]
                if title == "Товар Wildberries":
                    title = f"{prod.get('brand', '')} {prod.get('name', '')}".strip()

                # salePriceU в копейках
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