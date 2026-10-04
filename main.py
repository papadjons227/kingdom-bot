@dp.message(F.text)
async def handle_product_link(message: Message):
    text = message.text.strip()
    status_msg = await message.answer("🔍 Распознаю товар и проверяю цену...")

    # 1. Проверяем Ozon (включая ссылки из мобилки ozon.ru/t/...)
    if "ozon.ru" in text:
        resolved_url = await resolve_ozon_link(text)
        ozon_id = extract_ozon_id(resolved_url)
        
        # Если ссылка /t/ не развернулась из-за защиты, просим полную ссылку
        if not ozon_id:
            await status_msg.edit_text(
                "❌ Не удалось автоматически развернуть мобильную ссылку Ozon (защита Ozon заблокировала редирект на зарубежном сервере).\n\n"
                "💡 **Открой ссылку в браузере и скинь полный URL** (где в ссылке есть цифры SKU или `/product/...`)."
            )
            return

        data = await get_ozon_product_info(ozon_id)
        if not data:
            await status_msg.edit_text("❌ Шлюз Ozon не отдал цену. Попробуй скинуть ссылку еще раз.")
            return

        await add_or_update_item(message.from_user.id, ozon_id, data["title"], data["price"], "ozon")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔗 Открыть Ozon", url=f"https://www.ozon.ru/product/{ozon_id}")
        ]])
        await status_msg.edit_text(
            f"🎯 **Товар Ozon взят на слежку!**\n\n"
            f"📦 **{data['title']}**\n"
            f"SKU: `{ozon_id}`\n"
            f"Текущая цена: **{data['price']:,.0f} ₽**",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    # 2. Проверяем Wildberries
    if "wildberries.ru" in text or text.isdigit():
        from wb_parser import extract_wb_article
        article = extract_wb_article(text)
        if not article:
            await status_msg.edit_text("❌ Не удалось извлечь артикул Wildberries.")
            return

        data = await get_wb_product_info(article)
        if not data:
            await status_msg.edit_text(f"❌ Товар WB с артикулом `{article}` не найден.", parse_mode="Markdown")
            return

        await add_or_update_item(message.from_user.id, article, data["title"], data["price"], "wb")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔗 Открыть WB", url=f"https://www.wildberries.ru/catalog/{article}/detail.aspx")
        ]])
        await status_msg.edit_text(
            f"🎯 **Товар WB взят на слежку!**\n\n"
            f"📦 **{data['title']}**\n"
            f"Артикул: `{article}`\n"
            f"Текущая цена: **{data['price']:,.0f} ₽**",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        return

    await status_msg.edit_text("Не понял ссылку. Отправь ссылку на Ozon или артикул/ссылку Wildberries.")