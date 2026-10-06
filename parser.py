import aiohttp
from bs4 import BeautifulSoup
from database import save_post_data

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

async def parse_channel_posts(channel_username: str):
    """Парсит последние посты из публичной веб-версии канала t.me/s/{username}"""
    url = f"https://t.me/s/{channel_username}"
    headers = {"User-Agent": USER_AGENT}
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                
                html = await resp.text()
                soup = BeautifulSoup(html, 'html.parser')
                posts = soup.find_all('div', class_='tgme_widget_message')
                
                results = []
                for post in posts[-10:]: # Берем последние 10 постов
                    # Ссылка на пост
                    link_tag = post.find('a', class_='tgme_widget_message_date')
                    if not link_tag or 'href' not in link_tag.attrs:
                        continue
                    
                    link = link_tag['href']
                    
                    # Текст поста
                    text_tag = post.find('div', class_='tgme_widget_message_text')
                    text = text_tag.get_text(strip=True) if text_tag else ""
                    
                    # Наличие медиа
                    has_media = bool(post.find('a', class_='tgme_widget_message_photo_wrap') or 
                                   post.find('video') or 
                                   post.find('audio'))
                    
                    # Сохраняем в БД
                    await save_post_data(channel_username, link, text, has_media)
                    results.append({"link": link, "text": text[:50], "has_media": has_media})
                    
                return results
    except Exception as e:
        print(f"Ошибка парсинга {channel_username}: {e}")
        return []
