import asyncio
import logging
import time

import httpx

log = logging.getLogger(__name__)

# open.er-api.com — бесплатный, без ключа, база USD (1 USD = N валюты).
RATES_URL = "https://open.er-api.com/v6/latest/USD"

# Валюты бюджетов FreelanceHunt, которые нужно конвертировать из USD.
# USD — база (=1), её не запрашиваем.
CURRENCIES = ("UAH", "EUR", "PLN")


class RatesProvider:
    """Provides USD-based exchange rates (1 USD = N currency) for the currencies
    FreelanceHunt budgets use.

    Live rates are fetched from open.er-api.com and cached for ``ttl_sec``. When
    the API is unavailable (или возвращает мусор) the last good cache is reused;
    if there's no cache yet, the static ``fallback`` rates from config are used.
    """

    def __init__(
        self,
        fallback: dict[str, float],
        ttl_sec: float = 6 * 3600,
        timeout: float = 10.0,
        url: str = RATES_URL,
    ) -> None:
        self._fallback = dict(fallback)
        self._ttl = ttl_sec
        self._timeout = timeout
        self._url = url
        self._cache: dict[str, float] | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_rates(self) -> dict[str, float]:
        if self._fresh():
            assert self._cache is not None
            return self._cache
        async with self._lock:
            # Another task may have refreshed while we waited for the lock.
            if self._fresh():
                assert self._cache is not None
                return self._cache
            rates = await self._fetch()
            if rates is not None:
                self._cache = rates
                self._fetched_at = time.monotonic()
                return rates
            if self._cache is not None:
                log.warning("rates fetch failed, reusing stale cached rates")
                return self._cache
            log.warning("rates fetch failed and no cache yet, using fallback rates from config")
            return dict(self._fallback)

    def _fresh(self) -> bool:
        return self._cache is not None and (time.monotonic() - self._fetched_at) < self._ttl

    async def _fetch(self) -> dict[str, float] | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(self._url)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            log.exception("failed to fetch exchange rates from %s", self._url)
            return None

        if data.get("result") != "success":
            log.warning("rates API returned non-success result: %s", data.get("result"))
            return None
        api_rates = data.get("rates") or {}
        result: dict[str, float] = {}
        for cur in CURRENCIES:
            val = api_rates.get(cur)
            if not isinstance(val, (int, float)) or isinstance(val, bool) or val <= 0:
                log.warning("rates API missing/invalid rate for %s: %r", cur, val)
                return None
            result[cur] = float(val)
        log.info("fetched exchange rates: %s", result)
        return result
