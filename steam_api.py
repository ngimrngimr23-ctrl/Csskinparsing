import asyncio
import json
import random
import re
import logging
from urllib.parse import quote

import aiohttp

logger = logging.getLogger("steam_api")

APPID = 730
LISTINGS_URL = "https://steamcommunity.com/market/listings/{appid}/{name}/render"
PRICE_URL = "https://steamcommunity.com/market/priceoverview/"

STICKER_RE = re.compile(r"Sticker:\s*(.+)")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/javascript,*/*",
}


async def _jitter(base=1.5, spread=1.5):
    await asyncio.sleep(base + random.random() * spread)


async def fetch_listings(session: aiohttp.ClientSession, market_hash_name: str, count: int = 20):
    """Возвращает список dict: {listing_id, price_total (float, $), stickers: [names]}

    Бросает исключение при реальном сбое запроса (бан/рейт-лимит/битый ответ),
    чтобы вызывающий код (main.py) видел настоящую причину через set_last_error,
    а не тихо получал пустой список. Пустой список [] возвращается ТОЛЬКО когда
    запрос прошёл успешно, но среди полученных лотов нет ни одного с наклейками.
    """
    url = LISTINGS_URL.format(appid=APPID, name=quote(market_hash_name, safe=""))
    params = {"query": "", "start": "0", "count": str(count), "currency": "1", "format": "json"}
    request_headers = {
        **HEADERS,
        "Referer": url,
        "X-Requested-With": "XMLHttpRequest",
    }

    last_status = None
    last_exc = None
    data = None

    for attempt in range(3):
        try:
            async with session.get(url, params=params, headers=request_headers, timeout=20) as resp:
                last_status = resp.status
                if resp.status == 429 or resp.status == 403:
                    logger.warning("Rate limited on listings (%s), status=%s", market_hash_name, resp.status)
                    await asyncio.sleep(15 + attempt * 15)
                    continue
                body_text = await resp.text()
                if resp.status != 200:
                    logger.warning("Bad status %s for %s -> %s", resp.status, market_hash_name, body_text[:300])
                    raise RuntimeError(f"Steam HTTP {resp.status} для '{market_hash_name}': {body_text[:200]}")
                try:
                    data = json.loads(body_text)
                except Exception:
                    preview = body_text[:200].replace("\n", " ").strip()
                    logger.warning(
                        "Non-JSON response for %s (status %s): %r", market_hash_name, resp.status, preview
                    )
                    last_exc = RuntimeError(
                        f"Steam вернул не-JSON при статусе 200 (похоже на HTML-стену/капчу/пустой ответ): "
                        f"{preview or '(пустое тело)'}"
                    )
                    await asyncio.sleep(10)
                    continue
                break
        except RuntimeError:
            raise
        except Exception as e:
            last_exc = e
            logger.warning("Error fetching listings for %s: %s", market_hash_name, e)
            await asyncio.sleep(10)
    else:
        if last_status in (429, 403):
            raise RuntimeError(
                f"Steam заблокировал/зарейтлимитил запросы для '{market_hash_name}' "
                f"(статус {last_status} после 3 попыток). Похоже IP хостинга забанен Steam."
            )
        raise RuntimeError(
            f"Не удалось получить лоты для '{market_hash_name}' после 3 попыток: {last_exc}"
        )

    if data is None:
        raise RuntimeError(f"Пустой ответ Steam для '{market_hash_name}'")

    if not data.get("success"):
        raise RuntimeError(f"Steam вернул success=false для '{market_hash_name}': {str(data)[:200]}")

    listinginfo = data.get("listinginfo", {})
    assets = data.get("assets", {})
    app_assets = assets.get(str(APPID), {})

    results = []
    for listing_id, info in listinginfo.items():
        try:
            asset_ref = info.get("asset", {})
            contextid = str(asset_ref.get("contextid"))
            asset_id = str(asset_ref.get("id"))
            asset = app_assets.get(contextid, {}).get(asset_id)
            if not asset:
                continue

            price_cents = info.get("converted_price", 0) + info.get("converted_fee", 0)
            price_total = price_cents / 100.0

            stickers = []
            for desc in asset.get("descriptions", []):
                value = desc.get("value", "")
                m = STICKER_RE.search(value)
                if m:
                    raw = m.group(1)
                    raw = re.sub(r"<[^>]+>", "", raw)
                    parts = [p.strip() for p in raw.split(",") if p.strip()]
                    stickers.extend(parts)

            if stickers:
                results.append({
                    "listing_id": listing_id,
                    "price_total": price_total,
                    "stickers": stickers,
                })
        except Exception as e:
            logger.warning("Error parsing listing %s: %s", listing_id, e)
            continue

    return results


async def fetch_sticker_price(session: aiohttp.ClientSession, sticker_name: str):
    """Возвращает lowest_price наклейки в $ или None"""
    full_name = f"Sticker | {sticker_name}"
    params = {"appid": str(APPID), "currency": "1", "market_hash_name": full_name}

    for attempt in range(2):
        try:
            async with session.get(PRICE_URL, params=params, headers=HEADERS, timeout=15) as resp:
                if resp.status == 429 or resp.status == 403:
                    await asyncio.sleep(15)
                    continue
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                break
        except Exception as e:
            logger.warning("Error fetching sticker price for %s: %s", full_name, e)
            await asyncio.sleep(5)
    else:
        return None

    if not data or not data.get("success"):
        return None

    price_str = data.get("lowest_price") or data.get("median_price")
    if not price_str:
        return None

    cleaned = re.sub(r"[^\d.,]", "", price_str).replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None
        
