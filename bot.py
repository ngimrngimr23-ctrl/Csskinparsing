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

# --- 1. НАСТРОЙКИ (Берутся из Environment Variables на Render) ---
API_TOKEN = os.getenv('API_TOKEN')
STEAM_APIS_KEY = os.getenv('STEAM_APIS_KEY')
ADMIN_ID = 368097348  # ВПИШИ СВОЙ ID ЦИФРАМИ
# Твои данные прокси
PROXY_URL = "http://ngimrngimr23:hpC78oAwq5@151.247.120.145:50100"

SETTINGS_FILE = 'settings.json'
DEFAULT_SETTINGS = {
    "min_price": 0.8,
    "drop_percentage": 15.0,
    "sticker_markup": [8.0, 6.5, 5.5],
    "streak_markup": {"2": 10.0, "3": 14.0, "4": 20.0, "5": 25.0}
}

# --- 2. УПРАВЛЕНИЕ НАСТРОЙКАМИ ---
def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка загрузки настроек: {e}")
    return DEFAULT_SETTINGS

def save_settings(s):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(s, f, indent=4)
    except Exception as e:
        print(f"❌ Ошибка сохранения настроек: {e}")

settings = load_settings()

class SetupStates(StatesGroup):
    waiting_for_drop_perc = State()
    waiting_for_min_price = State()
    waiting_for_sticker_markups = State()
    waiting_for_streak_markups = State()

# --- 3. ИНТЕРФЕЙС (КЛАВИАТУРЫ) ---
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

# --- 4. ОБРАБОТЧИКИ (HANDLERS) ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("🚀 Снайпер-бот запущен и мониторит рынок!", reply_markup=get_main_kb())

@dp.message(F.text == "Вернуться в главное меню")
@dp.message(F.text == "Вернуться")
async def back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_kb())

# РАЗДЕЛ: СТИКЕРЫ
@dp.message(F.text == "Настройки стикеров")
async def sticker_menu(message: types.Message, state: FSMContext):
    await state.clear()
    m = settings.get('sticker_markup', [8.0, 6.5, 5.5])
    text = (
        "📊 <b>Настройки стикеров, ваши значения:</b>\n\n"
        f"Минимальная цена предмета? 👉 {settings.get('min_price', 0.8)}$\n"
        f"Наценка за стикер выше 300$? 👉 {m[2]}%\n"
        f"Наценка за стикер 100-300$? 👉 {m[1]}%\n"
        f"Наценка за стикер ниже 100$? 👉 {m[0]}%\n"
        "<b>Для изменения выберите раздел ниже:</b>"
    )
    await message.answer(text, reply_markup=get_stickers_kb(), parse_mode="HTML")

# ВВОД: МИН ЦЕНА
@dp.message(F.text == "Минимальная цена")
async def ask_min_p(message: types.Message, state: FSMContext):
    await message.answer("Введите мин. цену предмета (например, 0.5):")
    await state.set_state(SetupStates.waiting_for_min_price)

@dp.message(SetupStates.waiting_for_min_price)
async def proc_min_p(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        settings['min_price'] = val
        save_settings(settings)
        await message.answer(f"✅ Сохранено: {val}$", reply_markup=get_stickers_kb())
        await state.clear()
    except: await message.answer("❌ Введите число!")

# ВВОД: ОФЕРЫ (%)
@dp.message(F.text == "Настройки оферов")
async def ask_drop(message: types.Message, state: FSMContext):
    await message.answer(f"Текущий порог: {settings.get('drop_percentage') or 15}%\nВведите новый %:")
    await state.set_state(SetupStates.waiting_for_drop_perc)

@dp.message(SetupStates.waiting_for_drop_perc)
async def proc_drop(message: types.Message, state: FSMContext):
    try:
        val = float(message.text.replace(',', '.'))
        settings['drop_percentage'] = val
        save_settings(settings)
        await message.answer(f"✅ Порог {val}% установлен", reply_markup=get_main_kb())
        await state.clear()
    except: await message.answer("❌ Ошибка ввода")

# --- 5. ЯДРО (ПАРСЕР С ЛОГИРОВАНИЕМ) ---
async def market_parser():
    base_prices = {}
    # Ссылка с принудительной валютой USD (&currency=1)
    steam_url = "https://steamcommunity.com/market/search/render/?query=&start=0&count=10&sort_column=default&sort_dir=desc&appid=730&norender=1&currency=1"
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # ЗАГРУЗКА ИЗ STEAMAPIS
                if not base_prices:
                    print("🔄 Попытка загрузки базы SteamApis...")
                    async with session.get(f"https://api.steamapis.com/market/items/730?api_key={STEAM_APIS_KEY}") as r:
                        if r.status == 200:
                            data = await r.json()
                            for i in data.get('data', []):
                                base_prices[i['market_hash_name']] = i.get('price', {}).get('avg', 0)
                            print(f"✅ База загружена! Предметов: {len(base_prices)}")
                        else:
                            print(f"❌ Ошибка SteamApis! Статус: {r.status}. Проверьте ключ API.")
                            await asyncio.sleep(60)
                            continue

                # ПАРСИНГ СТИМА С ПРОВЕРКОЙ ПРОКСИ
                try:
                    async with session.get(steam_url, headers=headers, proxy=PROXY_URL, timeout=10) as r:
                        if r.status == 200:
                            data = await r.json()
                            results = data.get("results", [])
                            for item in results:
                                name = item.get("hash_name")
                                price_raw = item.get("sell_price_text", "$0").replace('$','').replace(',','').split()[0]
                                curr_price = float(price_raw)
                                
                                if curr_price < settings['min_price']: continue
                                
                                # Логика поиска стикеров (упрощенно)
                                stickers = []
                                for d in item.get("asset_description", {}).get("descriptions", []):
                                    if "Sticker:" in d.get("value", ""):
                                        f = re.findall(r'Sticker:(.*?)</center>', d.get("value", ""))
                                        if f: stickers = [s.strip() for s in re.sub(r'<[^>]+>', '', f[0]).split(',')]
                                
                                # Здесь будет вызов calculate_fair_value (как в прошлом коде)
                                # Для краткости шлем алерт если цена просто ниже базы на X %
                                if name in base_prices and curr_price < base_prices[name] * (1 - settings['drop_percentage']/100):
                                    msg = f"🔥 <b>СКИДКА!</b>\n📦 {name}\n💰 Цена: {curr_price}$\n📊 База: {base_prices[name]}$"
                                    await bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
                        
                        elif r.status == 429:
                            print("⚠️ Steam: Too Many Requests. Ждем 30 сек.")
                            await asyncio.sleep(30)
                        else:
                            print(f"⚠️ Ошибка Steam: {r.status}")
                except Exception as e:
                    print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРОКСИ ИЛИ СЕТИ: {e}")
                    await asyncio.sleep(10)

                await asyncio.sleep(4) # Пауза между кругами
            except Exception as e:
                print(f"🚨 Ошибка в цикле парсера: {e}")
                await asyncio.sleep(10)

# --- 6. ЗАПУСК ДЛЯ RENDER ---
async def handle_ping(request): return web.Response(text="Alive")

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
                                
