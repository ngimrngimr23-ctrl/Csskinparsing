import asyncio
import logging
import urllib.parse
import os
import random
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
import aiohttp

# ==========================================
# 1. ПЕРЕМЕННЫЕ И НАСТРОЙКИ (Берутся из Render)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# Глобальные настройки
proxy_list = []
parser_running = False
target_items = {}         # {"Скин": базовая_цена}
base_drop_percent = 20.0  # Ловить лоты дешевле базы на 20%
max_week_trend_drop = -10.0 # ФИЛЬТР: Отсеивать лот, если за неделю средняя цена скина упала больше чем на 10%

sticker_settings = {
    1: 5.0, 2: 15.0, 3: 35.0, 4: 80.0
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==========================================
# 2. КОМАНДЫ ДЛЯ ПРОКСИ
# ==========================================
@dp.message(Command("add_proxy"))
async def cmd_add_proxy(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    proxy_url = command.args
    if not proxy_url:
        await message.answer("⚠️ Формат: /add_proxy http://user:pass@ip:port")
        return

    msg = await message.answer("⏳ Проверяю прокси на Стиме...")
    
    # Тестируем прокси
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://steamcommunity.com/market/", proxy=proxy_url, timeout=7) as resp:
                if resp.status == 200:
                    proxy_list.append(proxy_url)
                    await msg.edit_text(f"✅ Прокси работает! Код 200.\nДобавлен в пул. Всего прокси: {len(proxy_list)}")
                elif resp.status == 429:
                    await msg.edit_text("❌ Прокси рабочий, но УЖЕ забанен Стимом (Код 429).")
                else:
                    await msg.edit_text(f"❌ Стим ответил ошибкой: {resp.status}")
    except Exception as e:
        await msg.edit_text(f"❌ Прокси мертв или не отвечает. Ошибка: {str(e)}")

# ==========================================
# 3. КОМАНДЫ НАСТРОЙКИ
# ==========================================
@dp.message(Command("set_markup"))
async def cmd_set_markup(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = command.args.split()
        count, percent = int(args[0]), float(args[1])
        if count in [1, 2, 3, 4]:
            sticker_settings[count] = percent
            await message.answer(f"✅ Наценка за {count} наклеек = {percent}%")
    except Exception:
        await message.answer("⚠️ Формат: /set_markup 4 80")

@dp.message(Command("set_drop"))
async def cmd_set_drop(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    global base_drop_percent
    try:
        base_drop_percent = float(command.args)
        await message.answer(f"✅ Ищем лоты дешевле на {base_drop_percent}% от базы.")
    except: pass

@dp.message(Command("set_trend"))
async def cmd_set_trend(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    global max_week_trend_drop
    try:
        max_week_trend_drop = float(command.args)
        await message.answer(f"✅ Фильтр тренда установлен. Отсеиваем скины, которые упали за неделю сильнее чем на {max_week_trend_drop}%")
    except: pass

@dp.message(Command("set_item"))
async def cmd_set_item(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = command.args.rsplit(' ', 1)
        item_name, price = args[0].strip(), float(args[1])
        target_items[item_name] = price
        await message.answer(f"✅ Добавлен: {item_name} | База: {price} руб.")
    except: pass

# ==========================================
# 4. ЛОГИКА ПАРСИНГА И ФИЛЬТРАЦИИ
# ==========================================
async def check_week_trend(session, item_name):
    """Проверяет динамику скина за неделю через CSGOBackpack"""
    url = f"https://csgobackpack.net/api/GetItemPrice/?currency=RUB&id={urllib.parse.quote(item_name)}&time=7"
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            if data.get("success"):
                avg_24h = float(data.get("average_price", 0))
                # Тут берем медиану за 7 дней из истории, упрощенно - берем самую старую доступную в ответе
                history = data.get("price_history", [])
                if history:
                    oldest_price = float(history[0]["average"]) # Цена 7 дней назад
                    if oldest_price > 0:
                        trend = ((avg_24h - oldest_price) / oldest_price) * 100
                        return trend
    except Exception: pass
    return 0.0 # Если API не ответил, считаем что тренд нулевой

async def check_stickers(session, inspect_link):
    url = f"https://api.csfloat.com/v1/me/inspect?url={urllib.parse.quote(inspect_link)}"
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("iteminfo", {}).get("stickers", [])
    except: pass
    return []

# ==========================================
# 5. ГЛАВНЫЙ ЦИКЛ СНАЙПИНГА
# ==========================================
@dp.message(Command("start_parser"))
async def cmd_start_parser(message: types.Message):
    global parser_running
    if message.from_user.id != ADMIN_ID: return
    parser_running = True
    await message.answer("🚀 Мониторинг запущен! Работаем в фоне.")
    asyncio.create_task(parser_loop())

@dp.message(Command("stop_parser"))
async def cmd_stop_parser(message: types.Message):
    global parser_running
    if message.from_user.id != ADMIN_ID: return
    parser_running = False
    await message.answer("🛑 Мониторинг остановлен.")

async def parser_loop():
    global parser_running
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession() as session:
        while parser_running:
            for item_name, base_price in target_items.items():
                if not parser_running: break
                
                # Выбор прокси, если они добавлены
                current_proxy = random.choice(proxy_list) if proxy_list else None
                url = f"https://steamcommunity.com/market/listings/730/{urllib.parse.quote(item_name)}/render/?query=&start=0&count=10&country=RU&language=russian&currency=5"
                
                try:
                    async with session.get(url, headers=headers, proxy=current_proxy, timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            listings = data.get("listinginfo", {})
                            
                            for listing_id, listing_data in listings.items():
                                final_price = (listing_data["price"] + listing_data["fee"]) / 100.0
                                target_drop_price = base_price * (1 - (base_drop_percent / 100))
                                
                                # ПРОВЕРКА 1: Просадка базы
                                if final_price <= target_drop_price:
                                    
                                    # ПРОВЕРКА 2: Фильтр недельного тренда (на лету!)
                                    trend = await check_week_trend(session, item_name)
                                    if trend >= max_week_trend_drop: # Пропускаем, если тренд не хуже нашего лимита
                                        await bot.send_message(
                                            ADMIN_ID,
                                            f"🔥 <b>СКИН УПАЛ (Тренд в норме: {trend:.1f}%)</b>\n"
                                            f"Предмет: {item_name}\n"
                                            f"Цена: {final_price} руб. (База: {base_price})\n"
                                            f"<a href='https://steamcommunity.com/market/listings/730/{urllib.parse.quote(item_name)}'>Купить</a>",
                                            parse_mode="HTML"
                                        )

                                # ПРОВЕРКА 3: Наклейки
                                asset = listing_data.get("asset", {})
                                action_link = asset.get("market_actions", [{}])[0].get("link", "")
                                if action_link:
                                    inspect_link = action_link.replace("%listingid%", listing_id).replace("%assetid%", asset["id"])
                                    stickers = await check_stickers(session, inspect_link)
                                    
                                    if stickers:
                                        names = [s.get("name") for s in stickers]
                                        for unique in set(names):
                                            count = names.count(unique)
                                            if count in sticker_settings:
                                                markup = sticker_settings[count]
                                                if final_price <= (base_price * (1 + (markup / 100))):
                                                    await bot.send_message(
                                                        ADMIN_ID,
                                                        f"💎 <b>НАЙДЕН СТРИК НАКЛЕЕК!</b>\n"
                                                        f"Предмет: {item_name}\n"
                                                        f"Стрик: {count}x {unique}\n"
                                                        f"Цена: {final_price} руб.\n"
                                                        f"<a href='https://steamcommunity.com/market/listings/730/{urllib.parse.quote(item_name)}'>Купить</a>",
                                                        parse_mode="HTML"
                                                    )
                except Exception as e:
                    logging.error(f"Ошибка парсинга {item_name}: {e}")
                
                await asyncio.sleep(2) # Пауза между запросами

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
                                   
