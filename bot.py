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

proxy_list = []
parser_running = False
target_items = {}         # Топ-1000 ликвидных скинов
blacklist = set()         # Черный список скинов
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
# 3. КОМАНДЫ НАСТРОЙКИ, БЛЕКЛИСТА И МЕНЮ
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(f"❌ Нет доступа. Ваш ID: {message.from_user.id}")
        return
        
    await message.answer(
        "🎯 <b>Снайпер-бот запущен (Топ-1000 ликвидных скинов)!</b>\n\n"
        "Доступные команды:\n"
        "🔸 /add_proxy http://... - Добавить прокси\n"
        "🔸 /set_markup [кол-во] [процент] - Наценка за стрик наклеек\n"
        "🔸 /set_drop [процент] - % падения базовой цены\n"
        "🔸 /set_trend [процент] - Макс. просадка тренда (напр. -10)\n"
        "🔸 /bl_add [имя] - Добавить скин в черный список\n"
        "🔸 /bl_del [имя] - Удалить из черного списка\n"
        "🔸 /start_parser - Скачать топ-1000 и начать мониторинг\n"
        "🔸 /stop_parser - Остановить мониторинг",
        parse_mode="HTML"
    )

@dp.message(Command("bl_add"))
async def cmd_bl_add(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    item_name = command.args
    if item_name:
        blacklist.add(item_name)
        await message.answer(f"✅ Добавлено в черный список:\n{item_name}")
    else:
        await message.answer("⚠️ Формат: /bl_add AK-47 | Safari Mesh (Battle-Scarred)")

@dp.message(Command("bl_del"))
async def cmd_bl_del(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    item_name = command.args
    if item_name in blacklist:
        blacklist.remove(item_name)
        await message.answer(f"✅ Удалено из черного списка:\n{item_name}")
    else:
        await message.answer("⚠️ Скин не найден в черном списке.")

@dp.message(Command("set_markup"))
async def cmd_set_markup(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = command.args.split()
        count, percent = int(args[0]), float(args[1])
        if count in [1, 2, 3, 4]:
            sticker_settings[count] = percent
            await message.answer(f"✅ Наценка за {count} наклеек = {percent}%")
    except:
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

# ==========================================
# 4. АВТО-ЗАГРУЗКА ТОП-1000 ЛИКВИДНОСТИ
# ==========================================
async def load_top_1000_liquid_items():
    """Скачивает рынок, сортирует по количеству продаж и отдает Топ-1000"""
    url = "https://csgobackpack.net/api/GetItemsList/v2/"
    temp_items = []
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=20) as resp:
                data = await resp.json()
                if data.get("success"):
                    items_list = data.get("items_list", {})
                    for name, info in items_list.items():
                        prices = info.get("price", {})
                        if prices:
                            data_24h = prices.get("24_hours", {})
                            price_24h = data_24h.get("median", 0)
                            
                            # Получаем объем продаж (ликвидность)
                            volume_str = data_24h.get("sold", "0")
                            try:
                                volume = int(str(volume_str).replace(',', ''))
                            except:
                                volume = 0
                                
                            if price_24h > 0 and volume > 0:
                                temp_items.append({
                                    'name': name,
                                    'price': float(price_24h) * 90.0, # Перевод в рубли
                                    'volume': volume
                                })
    except Exception as e:
        logging.error(f"Ошибка загрузки базы рынка: {e}")
        return {}

    # Сортируем по объему (от самых продаваемых к менее)
    temp_items.sort(key=lambda x: x['volume'], reverse=True)
    
    # Берем топ 1000
    top_1000 = temp_items[:1000]
    
    # Переводим обратно в нужный формат словаря {"Название": цена}
    loaded_items = {item['name']: item['price'] for item in top_1000}
    return loaded_items

@dp.message(Command("start_parser"))
async def cmd_start_parser(message: types.Message):
    global parser_running, target_items
    if message.from_user.id != ADMIN_ID: return
    
    if not target_items:
        await message.answer("⏳ Собираю аналитику рынка и отбираю Топ-1000 самых ликвидных скинов...")
        target_items = await load_top_1000_liquid_items()
        await message.answer(f"✅ База готова! Загружено предметов: {len(target_items)}")
        
    parser_running = True
    await message.answer("🚀 Снайпинг ликвидности запущен!")

@dp.message(Command("stop_parser"))
async def cmd_stop_parser(message: types.Message):
    global parser_running
    if message.from_user.id != ADMIN_ID: return
    parser_running = False
    await message.answer("🛑 Мониторинг остановлен.")

# ==========================================
# 5. ЛОГИКА ПАРСИНГА В ФОНЕ
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
    thread_bot = Bot(token=BOT_TOKEN)
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession() as session:
        while True:
            if not parser_running or not target_items:
                await asyncio.sleep(2)
                continue
                
            for item_name, base_price in target_items.items():
                if not parser_running: break
                
                if item_name in blacklist:
                    continue
                
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
                                            f"🔥 <b>СКИН УПАЛ (Тренд: {trend:.1f}%)</b>\n"
                                            f"Предмет: {item_name}\n"
                                            f"Цена: {final_price} руб. (База: {base_price:.2f})\n"
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
                    pass 
                
                await asyncio.sleep(2)

def start_parser_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(parser_loop_async())

# ==========================================
# 6. ВЕБ-СЕРВЕР И СТАРТ
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is running! UptimeRobot OK.", status=200)

async def start_bot_in_background(app):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    t = threading.Thread(target=start_parser_thread, daemon=True)
    t.start()

    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.on_startup.append(start_bot_in_background)
    
    port = int(os.environ.get("PORT", 10000))
    web.run_app(app, host='0.0.0.0', port=port)
    
