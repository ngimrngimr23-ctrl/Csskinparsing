import asyncio
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


async def fetch_listings(session: aiohttp.ClientSession, market_hash_name: str, count: int = 20, proxy: str = None):
    """Возвращает список dict: {listing_id, price_total (float, $), stickers: [names]}
    Кидает RuntimeError при сбое прокси/рейтлимите — вызывающий код должен ротировать прокси."""
    url = LISTINGS_URL.format(appid=APPID, name=quote(market_hash_name, safe=""))
    params = {"query": "", "start": "0", "count": str(count), "currency": "1", "format": "json"}

    last_err = None
    for attempt in range(2):
        try:
            async with session.get(url, params=params, headers=HEADERS, proxy=proxy, timeout=20) as resp:
                if resp.status == 429 or resp.status == 403:
                    logger.warning("Rate limited on listings (%s) via %s, status=%s", market_hash_name, proxy, resp.status)
                    last_err = f"HTTP {resp.status} (rate limited)"
                    await asyncio.sleep(5)
                    continue
                if resp.status != 200:
                    logger.warning("Bad status %s for %s", resp.status, market_hash_name)
                    return []
                data = await resp.json(content_type=None)
                break
        except (asyncio.TimeoutError, aiohttp.ClientProxyConnectionError, aiohttp.ClientHttpProxyError, aiohttp.ClientConnectorError) as e:
            logger.warning("Proxy/connection error for %s via %s: %s", market_hash_name, proxy, e)
            raise RuntimeError(f"Proxy error: {e}")
        except Exception as e:
            logger.warning("Error fetching listings for %s: %s", market_hash_name, e)
            last_err = str(e)
            await asyncio.sleep(5)
    else:
        raise RuntimeError(f"Listings fetch failed after retries: {last_err}")

    if not data or not data.get("success"):
        return []

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


async def test_proxy(session: aiohttp.ClientSession, proxy: str, timeout: int = 12):
    """Быстрая проверка прокси через реальный запрос к Steam. Возвращает (ok, detail)."""
    test_url = PRICE_URL
    params = {"appid": str(APPID), "currency": "1", "market_hash_name": "Sticker | Test"}
    try:
        async with session.get(test_url, params=params, headers=HEADERS, proxy=proxy, timeout=timeout) as resp:
            if resp.status == 200:
                return True, f"OK (HTTP {resp.status})"
            return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


async def fetch_sticker_price(session: aiohttp.ClientSession, sticker_name: str, proxy: str = None):
    """Возвращает lowest_price наклейки в $ или None.
    Кидает RuntimeError при сбое прокси/рейтлимите."""
    full_name = f"Sticker | {sticker_name}"
    params = {"appid": str(APPID), "currency": "1", "market_hash_name": full_name}

    last_err = None
    for attempt in range(2):
        try:
            async with session.get(PRICE_URL, params=params, headers=HEADERS, proxy=proxy, timeout=15) as resp:
                if resp.status == 429 or resp.status == 403:
                    last_err = f"HTTP {resp.status} (rate limited)"
                    await asyncio.sleep(5)
                    continue
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                break
        except (asyncio.TimeoutError, aiohttp.ClientProxyConnectionError, aiohttp.ClientHttpProxyError, aiohttp.ClientConnectorError) as e:
            logger.warning("Proxy/connection error for sticker %s via %s: %s", full_name, proxy, e)
            raise RuntimeError(f"Proxy error: {e}")
        except Exception as e:
            logger.warning("Error fetching sticker price for %s: %s", full_name, e)
            last_err = str(e)
            await asyncio.sleep(5)
    else:
        raise RuntimeError(f"Sticker price fetch failed after retries: {last_err}")

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
