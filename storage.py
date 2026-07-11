import json
import os
import logging
from urllib.parse import quote

import aiohttp

logger = logging.getLogger("storage")

UPSTASH_URL = os.environ["UPSTASH_REDIS_REST_URL"].rstrip("/")
UPSTASH_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]

HEADERS = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}

DEFAULT_MARKUP = 15.0
DEFAULT_MIN_VALUE = 7.0


async def _cmd(session: aiohttp.ClientSession, *parts):
    path = "/".join(quote(str(p), safe="") for p in parts)
    url = f"{UPSTASH_URL}/{path}"
    async with session.get(url, headers=HEADERS, timeout=15) as resp:
        body = await resp.text()
        if resp.status != 200:
            logger.error("Upstash HTTP %s for %s -> %s", resp.status, path, body)
            raise RuntimeError(f"Upstash error {resp.status}: {body}")
        try:
            data = await resp.json(content_type=None)
        except Exception:
            logger.error("Upstash non-JSON response for %s -> %s", path, body)
            raise RuntimeError(f"Upstash bad response: {body}")
        if "result" not in data:
            logger.error("Upstash response missing 'result' for %s -> %s", path, body)
            raise RuntimeError(f"Upstash unexpected response: {body}")
        return data.get("result")


async def redis_get(session, key):
    return await _cmd(session, "get", key)


async def redis_set(session, key, value):
    return await _cmd(session, "set", key, value)


async def redis_setex(session, key, ttl_seconds, value):
    return await _cmd(session, "setex", key, ttl_seconds, value)


async def redis_exists(session, key):
    r = await _cmd(session, "exists", key)
    return bool(r)


# ---------- config helpers ----------

async def get_markup(session) -> float:
    v = await redis_get(session, "config:markup")
    return float(v) if v is not None else DEFAULT_MARKUP


async def set_markup(session, value: float):
    await redis_set(session, "config:markup", value)


async def get_min_value(session) -> float:
    v = await redis_get(session, "config:min_value")
    return float(v) if v is not None else DEFAULT_MIN_VALUE


async def set_min_value(session, value: float):
    await redis_set(session, "config:min_value", value)


async def get_listings_count(session) -> int:
    v = await redis_get(session, "config:listings_count")
    return int(v) if v is not None else 20


async def set_listings_count(session, value: int):
    await redis_set(session, "config:listings_count", value)


async def set_last_error(session, text: str):
    await redis_set(session, "debug:last_error", text)


async def get_last_error(session):
    return await redis_get(session, "debug:last_error")


# ---------- proxies ----------

async def get_proxies(session) -> list:
    v = await redis_get(session, "config:proxies")
    return json.loads(v) if v else []


async def set_proxies(session, proxies: list):
    await redis_set(session, "config:proxies", json.dumps(proxies))


async def add_proxies(session, new_proxies: list):
    proxies = await get_proxies(session)
    added = 0
    for p in new_proxies:
        if p not in proxies:
            proxies.append(p)
            added += 1
    await set_proxies(session, proxies)
    return proxies, added


async def remove_proxy_by_index(session, index: int):
    proxies = await get_proxies(session)
    if 0 <= index < len(proxies):
        removed = proxies.pop(index)
        await set_proxies(session, proxies)
        return proxies, removed
    return proxies, None


async def get_proxy_index(session) -> int:
    v = await redis_get(session, "config:proxy_index")
    return int(v) if v is not None else 0


async def set_proxy_index(session, idx: int):
    await redis_set(session, "config:proxy_index", idx)


async def get_current_proxy(session):
    """Возвращает текущий прокси (строка) или None, если прокси не настроены."""
    proxies = await get_proxies(session)
    if not proxies:
        return None
    idx = await get_proxy_index(session)
    return proxies[idx % len(proxies)]


async def rotate_proxy(session):
    """Переключается на следующий прокси по кругу. Возвращает новый прокси или None."""
    proxies = await get_proxies(session)
    if not proxies:
        return None
    idx = await get_proxy_index(session)
    idx = (idx + 1) % len(proxies)
    await set_proxy_index(session, idx)
    return proxies[idx]


async def get_skins(session) -> list:
    v = await redis_get(session, "config:skins")
    return json.loads(v) if v else []


async def add_skin(session, name: str):
    skins = await get_skins(session)
    if name not in skins:
        skins.append(name)
        await redis_set(session, "config:skins", json.dumps(skins))
    return skins


async def remove_skin(session, name: str):
    skins = await get_skins(session)
    if name in skins:
        skins.remove(name)
        await redis_set(session, "config:skins", json.dumps(skins))
    return skins


async def get_chat_ids(session) -> list:
    v = await redis_get(session, "config:chat_ids")
    return json.loads(v) if v else []


async def add_chat_id(session, chat_id: int):
    ids = await get_chat_ids(session)
    if chat_id not in ids:
        ids.append(chat_id)
        await redis_set(session, "config:chat_ids", json.dumps(ids))
    return ids


# ---------- sticker price cache ----------

async def get_cached_sticker_price(session, sticker_name: str):
    v = await redis_get(session, f"cache:sticker:{sticker_name}")
    return float(v) if v is not None else None


async def cache_sticker_price(session, sticker_name: str, price: float, ttl=14400):
    await redis_setex(session, f"cache:sticker:{sticker_name}", ttl, price)


# ---------- dedup sent alerts ----------

async def already_sent(session, listing_id: str) -> bool:
    return await redis_exists(session, f"sent:{listing_id}")


async def mark_sent(session, listing_id: str, ttl=86400):
    await redis_setex(session, f"sent:{listing_id}", ttl, "1")
