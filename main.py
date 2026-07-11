import asyncio
import logging
import os
import random

import aiohttp
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import storage as db
from steam_api import fetch_listings, fetch_sticker_price, _jitter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

BOT_TOKEN = os.environ["BOT_TOKEN"]
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "180"))  # секунд между циклами

router = Router()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

# один общий aiohttp сеанс на весь процесс
http_session: aiohttp.ClientSession | None = None


# ---------------- Telegram команды ----------------

@router.message(Command("start"))
async def cmd_start(message: Message):
    await db.add_chat_id(http_session, message.chat.id)
    await message.answer(
        "Готов. Буду присматривать наклейки на настроенных скинах.\n\n"
        "Команды:\n"
        "/add_skin <market_hash_name> — добавить скин\n"
        "/remove_skin <market_hash_name> — убрать скин\n"
        "/list_skins — список скинов\n"
        "/set_markup <%> — макс. переплата сверх стоимости наклеек\n"
        "/set_min_value <$> — мин. суммарная стоимость наклеек\n"
        "/set_count <N> — сколько листингов проверять за проход\n"
        "/status — текущие настройки"
    )


@router.message(Command("add_skin"))
async def cmd_add_skin(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: /add_skin AK-47 | Slate (Minimal Wear)")
        return
    name = command.args.strip()
    skins = await db.add_skin(http_session, name)
    await message.answer(f"Добавлено: {name}\nВсего скинов: {len(skins)}")


@router.message(Command("remove_skin"))
async def cmd_remove_skin(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: /remove_skin AK-47 | Slate (Minimal Wear)")
        return
    name = command.args.strip()
    skins = await db.remove_skin(http_session, name)
    await message.answer(f"Убрано: {name}\nОсталось скинов: {len(skins)}")


@router.message(Command("list_skins"))
async def cmd_list_skins(message: Message):
    skins = await db.get_skins(http_session)
    if not skins:
        await message.answer("Список пуст. Добавь скин через /add_skin")
        return
    await message.answer("Отслеживаются:\n" + "\n".join(f"• {s}" for s in skins))


@router.message(Command("set_markup"))
async def cmd_set_markup(message: Message, command: CommandObject):
    try:
        value = float(command.args.strip().replace(",", "."))
    except (AttributeError, ValueError):
        await message.answer("Формат: /set_markup 15")
        return
    await db.set_markup(http_session, value)
    await message.answer(f"Макс. переплата установлена: {value}%")


@router.message(Command("set_min_value"))
async def cmd_set_min_value(message: Message, command: CommandObject):
    try:
        value = float(command.args.strip().replace(",", "."))
    except (AttributeError, ValueError):
        await message.answer("Формат: /set_min_value 7")
        return
    await db.set_min_value(http_session, value)
    await message.answer(f"Мин. стоимость наклеек установлена: ${value}")


@router.message(Command("set_count"))
async def cmd_set_count(message: Message, command: CommandObject):
    try:
        value = int(command.args.strip())
    except (AttributeError, ValueError):
        await message.answer("Формат: /set_count 50")
        return
    if value < 1 or value > 200:
        await message.answer("Число должно быть от 1 до 200 (без прокси не советую больше 100).")
        return
    await db.set_listings_count(http_session, value)
    await message.answer(f"Листингов за проход на скин: {value}")


@router.message(Command("status"))
async def cmd_status(message: Message):
    markup = await db.get_markup(http_session)
    min_value = await db.get_min_value(http_session)
    skins = await db.get_skins(http_session)
    count = await db.get_listings_count(http_session)
    await message.answer(
        f"Макс. переплата: {markup}%\n"
        f"Мин. стоимость наклеек: ${min_value}\n"
        f"Листингов на скин: {count}\n"
        f"Скинов в отслеживании: {len(skins)}\n"
        f"Интервал скана: {SCAN_INTERVAL}с"
    )


# ---------------- Логика скана ----------------

async def get_sticker_value(sticker_name: str) -> float:
    cached = await db.get_cached_sticker_price(http_session, sticker_name)
    if cached is not None:
        return cached
    await _jitter(1.0, 1.5)
    price = await fetch_sticker_price(http_session, sticker_name)
    if price is None:
        price = 0.0
    await db.cache_sticker_price(http_session, sticker_name, price)
    return price


async def scan_skin(skin_name: str, markup_limit: float, min_value: float, chat_ids: list, count: int):
    listings = await fetch_listings(http_session, skin_name, count=count)
    for listing in listings:
        listing_id = listing["listing_id"]
        if await db.already_sent(http_session, listing_id):
            continue

        bundle_value = 0.0
        for sticker in listing["stickers"]:
            bundle_value += await get_sticker_value(sticker)

        if bundle_value < min_value:
            continue

        price = listing["price_total"]
        if bundle_value <= 0:
            continue

        markup_pct = (price - bundle_value) / bundle_value * 100

        if markup_pct <= markup_limit:
            text = (
                f"🎯 {skin_name}\n"
                f"Цена лота: ${price:.2f}\n"
                f"Стоимость наклеек: ${bundle_value:.2f}\n"
                f"Переплата: {markup_pct:.1f}%\n"
                f"Наклейки: {', '.join(listing['stickers'])}"
            )
            for chat_id in chat_ids:
                try:
                    await bot.send_message(chat_id, text)
                except Exception as e:
                    logger.warning("Не удалось отправить в %s: %s", chat_id, e)

            await db.mark_sent(http_session, listing_id)


async def scan_loop():
    while True:
        try:
            markup_limit = await db.get_markup(http_session)
            min_value = await db.get_min_value(http_session)
            skins = await db.get_skins(http_session)
            chat_ids = await db.get_chat_ids(http_session)
            count = await db.get_listings_count(http_session)

            if not skins or not chat_ids:
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            for skin_name in skins:
                try:
                    await scan_skin(skin_name, markup_limit, min_value, chat_ids, count)
                except Exception as e:
                    logger.exception("Ошибка при скане %s: %s", skin_name, e)
                await asyncio.sleep(2 + random.random() * 2)  # джиттер между скинами

        except Exception as e:
            logger.exception("Ошибка в scan_loop: %s", e)

        await asyncio.sleep(SCAN_INTERVAL)


async def main():
    global http_session
    http_session = aiohttp.ClientSession()
    try:
        asyncio.create_task(scan_loop())
        await dp.start_polling(bot)
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
