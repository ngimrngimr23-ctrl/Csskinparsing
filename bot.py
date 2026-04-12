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
target_items = {}         
blacklist = set()         
base_drop_percent = 20.0  
max_week_trend_drop = -10.0 

sticker_settings = {1: 5.0, 2: 15.0, 3: 35.0, 4: 80.0}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Вспомогательные функции для защиты от вылетов
def safe_float(val):
    try:
        return float(val) if val is not None else 0.0
    except:
        return 0.0

def safe_int(val):
    try:
        if val is None: return 0
        return int(str(val).replace(',', '').strip())
    except:
        return 0

# ==========================================
# 2. КОМАНДЫ (ПРОКСИ И НАСТРОЙКИ)
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
                    await msg.edit_text(f"✅ Работает! Всего прокси: {len(proxy_list)}")
                else:
                    await msg.edit_text(f"❌ Ошибка Стима: {resp.status}")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка подключения: {str(e)}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer(f"❌ Нет доступа. Ваш ID: {message.from_user.id}")
        return
        
    await message.answer(
        "🎯 <b>Снайпер-бот (ВЕРСИЯ 2.1)</b>\n\n"
        "🔸 /add_proxy [url] - Добавить прокси\n"
        "🔸 /set_markup [кол-во] [процент] - Наценка за наклейки\n"
        "🔸 /set_drop [процент] - % падения цены\n"
        "🔸 /set_trend [процент] - Фильтр тренда (напр. -10)\n"
        "🔸 /bl_add [имя] - В черный список\n"
        "🔸 /start_parser - Запуск мониторинга\n"
        "🔸 /stop_parser - Остановка",
        parse_mode="HTML"
    )

@dp.message(Command("set_markup"))
async def cmd_set_markup(message: types.Message, command: CommandObject):
    try:
        args = command.args.split()
        sticker_settings[int(args[0])] = float(args[1])
        await message.answer("✅ Настройка сохранена")
    except: await message.answer("⚠️ Ошибка формата")

@dp.message(Command("set_drop"))
async def cmd_set_drop(message: types.Message, command: CommandObject):
    global base_drop_percent
    try:
        base_drop_percent = float(command.args)
        await message.answer(f"✅ Падение: {base_drop_percent}%")
    except: pass

@dp.message(Command("set_trend"))
async def cmd_set_trend(message: types.Message, command: CommandObject):
    global max_week_trend_drop
    try:
        max_week_trend_drop = float(command.args)
        await message.answer(f"✅ Тренд: {max_week_trend_drop}%")
    except: pass

@dp.message(Command("bl_add"))
async def cmd_bl_add(message: types.Message, command: CommandObject):
    if command.args:
        blacklist.add(command.args)
        await message.answer("✅ В блеклисте")

# ==========================================
# 3. ЗАГРУЗКА БАЗЫ (БРОНИРОВАННАЯ)
# ==========================================
async def load_top_1000():
    url = "https://csgobackpack.net/api/GetItemsList/v2/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
    temp_items = []
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=30) as resp:
                if resp.status != 200: return {}
                data = await resp.json()
                
                if data.get("success"):
                    items_list = data.get("items_list", {})
                    for name, info in items_list.items():
                        try:
                            # Тотальная проверка каждого значения
                            prices = info.get("price")
                            if not isinstance(prices, dict): continue
                            
                            d24 = prices.get("24_hours")
                            if not isinstance(d24, dict): continue
                            
                            p = safe_float(d24.get("median"))
                            v = safe_int(d24.get("sold"))
                            
                            if p > 0 and v > 0:
                                temp_items.append({'n': name, 'p': p * 90.0, 'v': v})
                        except: continue # Если один скин "кривой", просто идем к следующему
    except Exception as e:
        logging.error(f"Критическая ошибка загрузки: {e}")
        return {}

    # Сортировка и выборка ТОП-1000
    temp_items.sort(key=lambda x: x['v'], reverse=True)
    return {i['n']: i['p'] for i in temp_items[:1000]}

@dp.message(Command("start_parser"))
async def cmd_start_parser(message: types.Message):
    global parser_running, target_items
    if message.from_user.id != ADMIN_ID: return
    
    await message.answer("⏳ Анализирую рынок (Топ-1000 ликвидности)...")
    target_items = await load_top_1000()
    
    if target_items and len(target_items) > 0:
        parser_running = True
        await message.answer(f"✅ База готова ({len(target_items)} скинов). Снайпинг запущен!")
    else:
        await message.answer("❌ Ошибка API. Попробуй через минуту.")

@dp.message(Command("stop_parser"))
async def cmd_stop_parser(message: types.Message):
    global parser_running
    parser_running = False
    await message.answer("🛑 Остановлено")

# ==========================================
# 4. ЦИКЛ ПАРСИНГА (ОТДЕЛЬНЫЙ ПОТОК)
# ==========================================
async def parser_worker():
    thread_bot = Bot(token=BOT_TOKEN)
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with aiohttp.ClientSession() as session:
        while True:
            if not parser_running or not target_items:
                await asyncio.sleep(3); continue
                
            for name, base_p in list(target_items.items()):
                if not parser_running: break
                if name in blacklist: continue
                
                proxy = random.choice(proxy_list) if proxy_list else None
                url = f"https://steamcommunity.com/market/listings/730/{urllib.parse.quote(name)}/render/?start=0&count=5&currency=5"
                
                try:
                    async with session.get(url, headers=headers, proxy=proxy, timeout=7) as r:
                        if r.status == 200:
                            data = await r.json()
                            listings = data.get("listinginfo", {})
                            for l_id, l_data in listings.items():
                                price = (l_data["price"] + l_data["fee"]) / 100.0
                                
                                # Сигнал по цене
                                if price <= base_p * (1 - (base_drop_percent/100)):
                                    await thread_bot.send_message(ADMIN_ID, f"🔥 {name}\nЦена: {price}₽\n<a href='https://steamcommunity.com/market/listings/730/{urllib.parse.quote(name)}'>КУПИТЬ</a>", parse_mode="HTML")
                except: pass
                await asyncio.sleep(2) # Защита от бана

def run_parser():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(parser_worker())

# ==========================================
# 5. СЕРВЕР И ЗАПУСК
# ==========================================
async def handle_ping(request):
    return web.Response(text="OK", status=200)

async def on_startup(app):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    threading.Thread(target=run_parser, daemon=True).start()
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.on_startup.append(on_startup)
    web.run_app(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
    
