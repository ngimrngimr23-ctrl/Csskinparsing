import asyncio
import logging
import os
import random
import re

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import storage as db
from steam_api import fetch_listings, fetch_sticker_price, test_proxy, _jitter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

BOT_TOKEN = os.environ["BOT_TOKEN"]
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "180"))  # секунд между циклами
PORT = int(os.environ.get("PORT", "10000"))  # Render прокидывает свой PORT сам

router = Router()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

# один общий aiohttp сеанс на весь процесс
http_session: aiohttp.ClientSession | None = None


def mask_proxy(proxy: str) -> str:
    """Прячет пароль прокси в выводе: http://user:pass@host:port -> http://user:***@host:port"""
    return re.sub(r"://([^:/]+):([^@/]+)@", r"://\1:***@", proxy)


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
        "/add_proxies — массово добавить прокси (каждый с новой строки)\n"
        "/list_proxies — список прокси\n"
        "/remove_proxy <N> — удалить прокси по номеру\n"
        "/clear_proxies — очистить список прокси\n"
        "/check_proxies — проверить все прокси\n"
        "/status — текущие настройки\n"
        "/debug — диагностика (Upstash, последняя ошибка)"
    )


@router.message(Command("add_proxies"))
async def cmd_add_proxies(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(
            "Формат — каждый прокси на новой строке:\n"
            "/add_proxies\n"
            "http://user:pass@1.2.3.4:8000\n"
            "http://user:pass@5.6.7.8:8000\n\n"
            "Поддерживаются http:// и socks5:// прокси."
        )
        return
    lines = [l.strip() for l in command.args.splitlines() if l.strip()]
    try:
        proxies, added = await db.add_proxies(http_session, lines)
    except Exception as e:
        logger.exception("Ошибка add_proxies: %s", e)
        await message.answer(f"⚠️ Не удалось сохранить в базу: {e}")
        return
    await message.answer(f"Добавлено новых прокси: {added}\nВсего в списке: {len(proxies)}")


@router.message(Command("list_proxies"))
async def cmd_list_proxies(message: Message):
    try:
        proxies = await db.get_proxies(http_session)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось прочитать базу: {e}")
        return
    if not proxies:
        await message.answer("Прокси не добавлены — бот работает напрямую.")
        return
    idx = await db.get_proxy_index(http_session)
    current = idx % len(proxies)
    lines = []
    for i, p in enumerate(proxies):
        marker = " 👈 текущий" if i == current else ""
        lines.append(f"{i + 1}. {mask_proxy(p)}{marker}")
    await message.answer("Прокси:\n" + "\n".join(lines))


@router.message(Command("remove_proxy"))
async def cmd_remove_proxy(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Формат: /remove_proxy <номер> (см. /list_proxies)")
        return
    try:
        idx = int(command.args.strip()) - 1
    except ValueError:
        await message.answer("Формат: /remove_proxy <номер>")
        return
    try:
        proxies, removed = await db.remove_proxy_by_index(http_session, idx)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось сохранить в базу: {e}")
        return
    if removed is None:
        await message.answer("Нет прокси с таким номером.")
        return
    await message.answer(f"Удалён: {mask_proxy(removed)}\nОсталось: {len(proxies)}")


@router.message(Command("clear_proxies"))
async def cmd_clear_proxies(message: Message):
    try:
        await db.set_proxies(http_session, [])
        await db.set_proxy_index(http_session, 0)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось сохранить в базу: {e}")
        return
    await message.answer("Список прокси очищен. Бот переключён на прямые запросы.")


@router.message(Command("check_proxies"))
async def cmd_check_proxies(message: Message):
    try:
        proxies = await db.get_proxies(http_session)
    except Exception as e:
        await message.answer(f"⚠️ Не удалось прочитать базу: {e}")
        return
    if not proxies:
        await message.answer("Прокси не добавлены.")
        return
    await message.answer(f"Проверяю {len(proxies)} прокси, это может занять до пары минут...")
    results = []
    for i, p in enumerate(proxies):
        ok, detail = await test_proxy(http_session, p, timeout=12)
        status = "✅" if ok else "❌"
        results.append(f"{i + 1}. {status} {mask_proxy(p)} — {detail}")
        await asyncio.sleep(1)
    await message.answer("Результаты проверки:\n" + "\n".join(results))


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
    try:
        await db.set_markup(http_session, value)
    except Exception as e:
        logger.exception("Ошибка записи markup в Upstash: %s", e)
        await message.answer(f"⚠️ Не удалось сохранить в базу: {e}")
        return
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


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    lines = []

    upstash_url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    upstash_token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    lines.append(f"UPSTASH_REDIS_REST_URL: {'✅ задан (' + upstash_url[:25] + '...)' if upstash_url else '❌ ПУСТО'}")
    lines.append(f"UPSTASH_REDIS_REST_TOKEN: {'✅ задан (' + str(len(upstash_token)) + ' симв.)' if upstash_token else '❌ ПУСТО'}")

    try:
        test_val = str(int(asyncio.get_event_loop().time()))
        await db.redis_set(http_session, "debug:ping", test_val)
        readback = await db.redis_get(http_session, "debug:ping")
        if readback == test_val:
            lines.append("Upstash round-trip: ✅ OK")
        else:
            lines.append(f"Upstash round-trip: ⚠️ записали {test_val}, прочитали {readback}")
    except Exception as e:
        lines.append(f"Upstash round-trip: ❌ ОШИБКА: {e}")

    try:
        last_error = await db.get_last_error(http_session)
        lines.append(f"Последняя ошибка скана: {last_error or '(нет)'}")
    except Exception as e:
        lines.append(f"Не удалось прочитать last_error: {e}")

    await message.answer("\n".join(lines))


@router.message(Command("status"))
async def cmd_status(message: Message):
    try:
        markup = await db.get_markup(http_session)
        min_value = await db.get_min_value(http_session)
        skins = await db.get_skins(http_session)
        count = await db.get_listings_count(http_session)
        proxies = await db.get_proxies(http_session)
        current_proxy = await db.get_current_proxy(http_session)
    except Exception as e:
        logger.exception("Ошибка чтения статуса из Upstash: %s", e)
        await message.answer(f"⚠️ Не удалось прочитать базу: {e}")
        return
    proxy_line = f"{len(proxies)} (текущий: {mask_proxy(current_proxy)})" if proxies else "0 (прямые запросы)"
    await message.answer(
        f"Макс. переплата: {markup}%\n"
        f"Мин. стоимость наклеек: ${min_value}\n"
        f"Листингов на скин: {count}\n"
        f"Скинов в отслеживании: {len(skins)}\n"
        f"Прокси: {proxy_line}\n"
        f"Интервал скана: {SCAN_INTERVAL}с"
    )


# ---------------- Логика скана ----------------

async def call_with_proxy_rotation(coro_factory):
    """Вызывает coro_factory(proxy) с текущим прокси; при сбое ротирует и пробует следующий.
    coro_factory должна кинуть исключение при проблеме именно с прокси/сетью."""
    proxies = await db.get_proxies(http_session)
    attempts = max(1, len(proxies))
    last_exc = None
    for _ in range(attempts):
        proxy = await db.get_current_proxy(http_session)
        try:
            return await coro_factory(proxy)
        except Exception as e:
            last_exc = e
            if not proxies:
                raise
            logger.warning("Прокси %s не сработал, переключаюсь: %s", mask_proxy(proxy) if proxy else "direct", e)
            await db.set_last_error(http_session, f"Прокси {mask_proxy(proxy) if proxy else 'direct'} не сработал: {e}")
            await db.rotate_proxy(http_session)
    raise last_exc


async def get_sticker_value(sticker_name: str) -> float:
    cached = await db.get_cached_sticker_price(http_session, sticker_name)
    if cached is not None:
        return cached
    await _jitter(1.0, 1.5)
    try:
        price = await call_with_proxy_rotation(
            lambda proxy: fetch_sticker_price(http_session, sticker_name, proxy=proxy)
        )
    except Exception as e:
        logger.warning("Не удалось получить цену наклейки %s: %s", sticker_name, e)
        price = None
    if price is None:
        price = 0.0
    await db.cache_sticker_price(http_session, sticker_name, price)
    return price


async def scan_skin(skin_name: str, markup_limit: float, min_value: float, chat_ids: list, count: int):
    try:
        listings = await call_with_proxy_rotation(
            lambda proxy: fetch_listings(http_session, skin_name, count=count, proxy=proxy)
        )
    except Exception as e:
        err = f"[{skin_name}] Ошибка fetch_listings (все прокси исчерпаны): {e}"
        logger.exception(err)
        await db.set_last_error(http_session, err)
        return

    for listing in listings:
        listing_id = listing["listing_id"]
        if await db.already_sent(http_session, listing_id):
            continue

        bundle_value = 0.0
        try:
            for sticker in listing["stickers"]:
                bundle_value += await get_sticker_value(sticker)
        except Exception as e:
            err = f"[{skin_name}] Ошибка расчёта цены наклеек: {e}"
            logger.exception(err)
            await db.set_last_error(http_session, err)
            continue

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
                await asyncio.sleep(2 + random.random() * 2)

        except Exception as e:
            logger.exception("Ошибка в scan_loop: %s", e)

        await asyncio.sleep(SCAN_INTERVAL)


async def start_fake_web_server():
    """Пустой HTTP-сервер только для того, чтобы Render видел открытый порт на free Web Service."""
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Fake web server started on port %s", PORT)


async def main():
    global http_session
    http_session = aiohttp.ClientSession()
    try:
        await start_fake_web_server()
        asyncio.create_task(scan_loop())
        await dp.start_polling(bot)
    finally:
        await http_session.close()


if __name__ == "__main__":
    asyncio.run(main())
