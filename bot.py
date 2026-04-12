import asyncio
import logging
import urllib.parse
import os
import random
import threading
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
import aiohttp
from aiohttp import web

# ==========================================
# 1. ПЕРЕМЕННЫЕ И НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# Глобальные настройки (общие для всех потоков)
proxy_list = []
parser_running = False
target_items = {}         
base_drop_percent = 20.0  
max_week_trend_drop = -10.0 

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
# 3. КОМАНДЫ НАСТРОЙКИ И МЕНЮ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(f"❌ Нет доступа. Ваш ID: {message.from_user.id}")
        return
        
    await message.answer(
        "🎯 <b>Снайпер-бот запущен (Многопоточный режим)!</b>\n\n"
        "Доступные команды:\n"
        "🔸 /add_proxy http://user:pass@ip:port - Добавить прокси\n"
        "🔸 /set_markup <кол-во> <процент> - Наценка за стрик наклеек\n"
        "🔸 /set_drop <процент> - % падения базовой цены для алерта\n"
        "🔸 /set_trend <процент> - Макс. просадка тренда (напр. -10)\n"
        "🔸 /set_item <имя> <цена> - Добавить скин и его базу\n"
        "🔸 /start_parser - Запустить мониторинг Steam\n"
        "🔸 /stop_parser - Остановить мониторинг",
        parse_mode="HTML"
    )

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
        await message.answer(f"✅ Фильтр тренда: {max_week_trend_drop}%")
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

@dp.message(Command("start_parser"))
async def cmd_start_parser(message: types.Message):
    global parser_running
    if message.from_user.id != ADMIN_ID: return
    if not target_items:
        await message.answer("❌ Список предметов пуст. Добавь через /set_item")
        return
        
    parser_running = True
    await message.answer("🚀 Мониторинг запущен в фоновом потоке!")

@dp.message(Command("stop_parser"))
async def cmd_stop_parser(message: types.Message):
    global parser_running
    if message.from_user.id != ADMIN_ID: return
    parser_running = False
    await message.answer("🛑 Мониторинг остановлен.")

# ==========================================
# 4. ЛОГИКА ПАРСИНГА (РАБОТАЕТ В ДРУГОМ ПОТОКЕ)
# ==========================================
async def check_week_trend(session, item_name):
    url = f"https://csgobackpack.net/api/GetItemPrice/?currency=RUB&id={urllib.parse.quote(item_name)}&time=7"
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            if data.get("success"):
                avg_24h = float(data.get("average_price", 0))
                history = data.get("price_history", [])
                if history:
                    oldest_price = float(history[0]["average"]) 
                    if oldest_price > 0:
                        return ((avg_24h - oldest_price) / oldest_price) * 100
    except: pass
    return 0.0

async def check_stickers(session, inspect_link):
    url = f"https://api.csfloat.com/v1/me/inspect?url={urllib.parse.quote(inspect_link)}"
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("iteminfo", {}).get("stickers", [])
    except: pass
    return []

async def parser_loop_async():
    """Асинхронный цикл парсера"""
    # Создаем отдельного бота для этого потока, чтобы не ломать основной
    thread_bot = Bot(token=BOT_TOKEN)
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession() as session:
        while True: # Поток живет всегда
            if not parser_running or not target_items:
                await asyncio.sleep(2)
                continue
                
            for item_name, base_price in target_items.items():
                if not parser_running: break
                
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
                                    trend = await check_week_trend(session, item_name)
                                    if trend >= max_week_trend_drop:
                                        await thread_bot.send_message(
                                            ADMIN_ID,
                                            f"🔥 <b>СКИН УПАЛ (Тренд в норме: {trend:.1f}%)</b>\n"
                                            f"Предмет: {item_name}\n"
                                            f"Цена: {final_price} руб. (База: {base_price})\n"
                                            f"<a href='https://steamcommunity.com/market/listings/730/{urllib.parse.quote(item_name)}'>Купить</a>",
                                            parse_mode="HTML"
                                        )

                                # ПРОВЕРКА 2: Наклейки
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
                                                    await thread_bot.send_message(
                                                        ADMIN_ID,
                                                        f"💎 <b>НАЙДЕН СТРИК НАКЛЕЕК!</b>\n"
                                                        f"Предмет: {item_name}\n"
                                                        f"Стрик: {count}x {unique}\n"
                                                        f"Цена: {final_price} руб.\n"
                                                        f"<a href='https://steamcommunity.com/market/listings/730/{urllib.parse.quote(item_name)}'>Купить</a>",
                                                        parse_mode="HTML"
                                                    )
                except Exception as e:
                    logging.error(f"Ошибка парсера: {e}")
                
                await asyncio.sleep(2) # Задержка между предметами

def start_parser_thread():
    """Изолированный запуск парсера в новом потоке"""
    logging.info("Запуск независимого потока парсера...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(parser_loop_async())

# ==========================================
# 5. ВЕБ-СЕРВЕР И СТАРТ
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is running! UptimeRobot OK.", status=200)

async def start_bot_in_background(app):
    logging.info("Очистка старых вебхуков и запуск polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    # 1. Запускаем выделенный поток для парсера СРАЗУ
    t = threading.Thread(target=start_parser_thread, daemon=True)
    t.start()

    # 2. Поднимаем веб-сервер и Телеграм-бота в основном потоке
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.on_startup.append(start_bot_in_background)
    
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host='0.0.0.0', port=port)
    
