import asyncio
import json
import logging
import aiohttp
import re
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# --- 1. НАСТРОЙКИ И КЛЮЧИ ---
API_TOKEN = os.getenv('API_TOKEN')
STEAM_APIS_KEY = os.getenv('STEAM_APIS_KEY', 'd3UBKCHmu7CHGLiokpc6UR-mOtU')
ADMIN_ID = 368097348  # ЗАМЕНИ НА СВОЙ ID
PROXY_URL = "http://ngimrngimr23:hpC78oAwq5@151.247.120.145:50100"
SETTINGS_FILE = 'settings.json'

DEFAULT_SETTINGS = {
    "min_price": 0.8,
    "drop_percentage": 15.0,
    "sticker_markup": [8.0, 6.5, 5.5],
    "streak_markup": {2: 10.0, 3: 14.0, 4: 20.0, 5: 25.0}
}

# --- 2. ЛОГИКА ХРАНЕНИЯ ---
def load_settings():
    try:
        with open(SETTINGS_FILE, 'r') as f: return json.load(f)
    except: return DEFAULT_SETTINGS

def save_settings(s):
    with open(SETTINGS_FILE, 'w') as f: json.dump(s, f, indent=4)

settings = load_settings()

class SetupStates(StatesGroup):
    waiting_for_drop_perc = State()
    waiting_for_sticker_markups = State()

# --- 3. КЛАВИАТУРЫ ---
def get_main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Общие настройки"), KeyboardButton(text="Настройки стикеров")],
        [KeyboardButton(text="Настройки флоатов"), KeyboardButton(text="Настройки агентов")],
        [KeyboardButton(text="Настройки брелоков"), KeyboardButton(text="Настройки гемов")],
        [KeyboardButton(text="Настройки оферов"), KeyboardButton(text="Вернуться")]
    ], resize_keyboard=True)

def get_stickers_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Минимальная цена"), KeyboardButton(text="Наценки за стикеры")],
        [KeyboardButton(text="Наценки за стрики"), KeyboardButton(text="Потертости стриков")],
        [KeyboardButton(text="Вернуться в главное меню")]
    ], resize_keyboard=True)

# --- 4. ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- 5. ОБРАБОТЧИКИ (HANDLERS) ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("✅ Бот запущен и готов к работе!", reply_markup=get_main_kb())

@dp.message(F.text == "Вернуться в главное меню")
@dp.message(F.text == "Вернуться")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню:", reply_markup=get_main_kb())

# СТИКЕРЫ (ИСПРАВЛЕНО: &lt; и &gt;)
@dp.message(F.text == "Настройки стикеров")
async def sticker_menu(message: types.Message, state: FSMContext):
    await state.clear()
    markups = settings.get('sticker_markup', [8.0, 6.5, 5.5])
    text = (
        "📊 <b>Настройки стикеров, ваши значения:</b>\n\n"
        f"Минимальная цена предмета? 👉 {settings.get('min_price', 0.8)}$\n"
        f"Процент наценки за стикер при цене стикера выше 300$? 👉 {markups[2]}%\n"
        f"Процент наценки за стикер при цене стикера 100-300$? 👉 {markups[1]}%\n"
        f"Процент наценки за стикер при цене стикера ниже 100$? 👉 {markups[0]}%\n"
        "Как считать наценку за стрики? 👉 2-10.0%; 3-14.0%; 4-20.0%; 5-25.0%\n"
        "Считать потёртости в стриках? 👉 Нет\n\n"
        "<b>Для изменения настроек выберите раздел в меню ниже.</b>"
    )
    await message.answer(text, reply_markup=get_stickers_kb(), parse_mode="HTML")

# ОФЕРЫ
@dp.message(F.text == "Настройки оферов")
async def setup_offers(message: types.Message, state: FSMContext):
    await message.answer(f"Текущий порог: {settings.get('drop_percentage', 15.0)}%\nВведите новый % падения:")
    await state.set_state(SetupStates.waiting_for_drop_perc)

# ФЛОАТЫ
@dp.message(F.text == "Настройки флоатов")
async def float_settings(message: types.Message):
    text = (
        "🟢 <b>Настройки флоатов</b>\n"
        "Максимальный процент наценки за выгодный флоат.\n"
        "Минимальная цена предмета с редким флоатом.\n"
        "Дополнительные ограничения по интервалам флоатов.\n"
    )
    await message.answer(text, parse_mode="HTML")

# АГЕНТЫ
@dp.message(F.text == "Настройки агентов")
async def agent_settings(message: types.Message):
    text = (
        "✅ <b>Настройки агентов</b>\n"
        "Минимальный процент прибыли для показа.\n"
        "Наценка за нашивки.\n"
    )
    await message.answer(text, parse_mode="HTML")

# БРЕЛОКИ
@dp.message(F.text == "Настройки брелоков")
async def charm_settings(message: types.Message):
    text = (
        "✅ <b>Настройки брелоков</b>\n"
        "Максимальный коэффициент переплаты.\n"
        "Диапазоны паттернов.\n"
    )
    await message.answer(text, parse_mode="HTML")

# ГЕМЫ
@dp.message(F.text == "Настройки гемов")
async def gem_settings(message: types.Message):
    text = (
        "💎 <b>Настройки гемов</b>\n"
        "Минимальный процент прибыли.\n"
        "Использовать Artificer's Chisel?\n"
        "Использовать Master Artificer's Hammer?\n"
        "Наценка за камни.\n"
    )
    await message.answer(text, parse_mode="HTML")

# ОБЩИЕ
@dp.message(F.text == "Общие настройки")
async def general_settings(message: types.Message):
    await message.answer("⚙️ <b>Общие настройки бота</b>\nЗдесь можно настроить глобальные фильтры.", parse_mode="HTML")

# --- 6. ПАРСЕР И МАТЕМАТИКА ---
def calculate_fair_value(name, stickers, base_prices):
    skin_base = base_prices.get(name, 0)
    if skin_base == 0: return 0
    overpay = 0
    counts = {}
    for s in stickers:
        f_name = f"Sticker | {s}"
        p = base_prices.get(f_name, 0)
        if p > 0:
            counts[s] = counts.get(s, 0) + 1
            if p >= 300: pct = settings['sticker_markup'][2]
            elif 100 <= p < 300: pct = settings['sticker_markup'][1]
            else: pct = settings['sticker_markup'][0]
            overpay += (p * (pct / 100))
    for s, c in counts.items():
        if c >= 2: overpay += (overpay * (settings['streak_markup'].get(c, 0) / 100))
    return skin_base + overpay

async def market_parser():
    base_prices = {}
    url = "https://steamcommunity.com/market/search/render/?query=&start=0&count=10&sort_column=default&sort_dir=desc&appid=730&norender=1&currency=1"
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                if not base_prices:
                    async with session.get(f"https://api.steamapis.com/market/items/730?api_key={STEAM_APIS_KEY}") as r:
                        if r.status == 200:
                            for i in (await r.json()).get('data', []):
                                base_prices[i['market_hash_name']] = i.get('price', {}).get('avg', 0)
                            print("✅ База цен загружена")
                
                async with session.get(url, headers=headers, proxy=PROXY_URL) as r:
                    if r.status == 200:
                        for item in (await r.json()).get("results", []):
                            h_name = item.get("hash_name")
                            p_raw = item.get("sell_price_text", "$0").replace('$','').replace(',','').split()[0]
                            curr_p = float(p_raw)
                            
                            stks = []
                            for d in item.get("asset_description", {}).get("descriptions", []):
                                if "Sticker:" in d.get("value", ""):
                                    f = re.findall(r'Sticker:(.*?)</center>', d.get("value", ""))
                                    if f: stks = [s.strip() for s in re.sub(r'<[^>]+>', '', f[0]).split(',')]
                            
                            fair = calculate_fair_value(h_name, stks, base_prices)
                            if fair > curr_p + 0.1:
                                await bot.send_message(ADMIN_ID, f"🔥 <b>ПРОФИТ!</b>\n{h_name}\nЦена: {curr_p}$\nСправедливая: {round(fair,2)}$", parse_mode="HTML")
                await asyncio.sleep(4)
            except Exception as e:
                print(f"Ошибка: {e}")
                await asyncio.sleep(10)

# --- 7. ЗАПУСК ---
async def handle_ping(request): return web.Response(text="Bot is alive!")

async def main():
    asyncio.create_task(market_parser())
    asyncio.create_task(dp.start_polling(bot))
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 10000).start()
    while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
    
