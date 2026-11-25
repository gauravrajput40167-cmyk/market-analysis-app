import re
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
# Primary and Fallback Binance Public Endpoints
PRIMARY_BINANCE_URL = "https://data-api.binance.vision"
BACKUP_BINANCE_URL = "https://api.binance.com"
# Shared persistent session with connection pooling for sub-50ms response times
_session = requests.Session()
_retries = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
_adapter = HTTPAdapter(pool_connections=10, pool_maxsize=20, max_retries=_retries)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)
def _sanitize_symbol(symbol: str) -> str:
    """Normalize symbol string (e.g. 'btc/usdt' -> 'BTCUSDT')."""
    if not isinstance(symbol, str):
        return "BTCUSDT"
    cleaned = re.sub(r"[^A-Za-z0-9]", "", symbol).strip().upper()
    return cleaned if cleaned else "BTCUSDT"
def get_market_data(symbol: str, limit: int = 100) -> list:
    """
    Fetch real-time 1-minute Kline (candlestick) history from Binance Spot API.
    Maintains full backward compatibility with original dictionary structure:
    [{'time': 'HH:MM:SS', 'price': float, 'volume': float}, ...]
    """
    clean_symbol = _sanitize_symbol(symbol)
    limit = max(10, min(int(limit), 500))
    params = {
        "symbol": clean_symbol,
        "interval": "1m",
        "limit": limit
    }
    endpoints = [
        f"{PRIMARY_BINANCE_URL}/api/v3/klines",
        f"{BACKUP_BINANCE_URL}/api/v3/klines"
    ]
    for url in endpoints:
        try:
            response = _session.get(url, params=params, timeout=(3.05, 6))
            response.raise_for_status()
            candles = response.json()
            if not isinstance(candles, list):
                continue
            market_data = []
            for candle in candles:
                if not isinstance(candle, list) or len(candle) < 6:
                    continue
                open_time_ms = candle[0]
                close_price = float(candle[4])
                candle_volume = float(candle[5])
                market_data.append({
                    "time": datetime.fromtimestamp(open_time_ms / 1000).strftime("%H:%M:%S"),
                    "price": close_price,
                    "volume": candle_volume,
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": close_price,
                })
            if market_data:
                return market_data
        except Exception as err:
            # Fallback to secondary endpoint seamlessly
            continue
    print(f"[DataService] Warning: Failed to fetch market data for {clean_symbol} across all endpoints.", flush=True)
    return []
def get_24h_stats(symbol: str) -> dict:
    """
    Fetch 24-hour rolling price statistics from Binance Spot API.
    Returns:
    {
        'price_change_percent': float,
        'high_price': float,
        'low_price': float,
        'volume': float
    }
    """
    clean_symbol = _sanitize_symbol(symbol)
    params = {"symbol": clean_symbol}
    endpoints = [
        f"{PRIMARY_BINANCE_URL}/api/v3/ticker/24hr",
        f"{BACKUP_BINANCE_URL}/api/v3/ticker/24hr"
    ]
    for url in endpoints:
        try:
            response = _session.get(url, params=params, timeout=(3.05, 6))
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                continue
            return {
                "price_change_percent": float(data.get("priceChangePercent", 0.0)),
                "high_price": float(data.get("highPrice", 0.0)),
                "low_price": float(data.get("lowPrice", 0.0)),
                "volume": float(data.get("volume", 0.0)),
            }
        except Exception:
            continue
    print(f"[DataService] Warning: Failed to fetch 24h stats for {clean_symbol}.", flush=True)
    return {
        "price_change_percent": 0.0,
        "high_price": 0.0,
        "low_price": 0.0,
        "volume": 0.0
    }
# =========================================================
# STANDALONE DIAGNOSTIC TEST
# =========================================================
if __name__ == "__main__":
    print("Testing Binance Market Data Provider...")
    test_symbol = "BTCUSDT"
    print(f"Fetching 1m klines for {test_symbol}...")
    klines = get_market_data(test_symbol, limit=5)
    print(f"Received {len(klines)} candles. Latest price: {klines[-1]['price'] if klines else 'N/A'}")
    print(f"Fetching 24h stats for {test_symbol}...")
    stats = get_24h_stats(test_symbol)
    print(f"24h Stats: {stats}")
