import math
def _get_prices(market_data):
    """
    Extract clean, valid, positive closing prices from market data.
    Accepts both list of candle dictionaries [{'price': ...}] and flat lists of numbers.
    Filters out None, NaN, and negative values.
    """
    if not market_data:
        return []
    prices = []
    for item in market_data:
        try:
            if isinstance(item, (int, float)):
                p = float(item)
            elif isinstance(item, dict):
                p = float(item.get("price") if "price" in item else item.get("close", 0.0))
            else:
                continue
            # Ensure valid, positive, finite number (p == p handles NaN detection)
            if p > 0 and p == p and not math.isinf(p):
                prices.append(p)
        except (KeyError, TypeError, ValueError):
            continue
    return prices
def calculate_average(market_data):
    """Calculate the simple mean price across all available candles."""
    prices = _get_prices(market_data)
    if not prices:
        return 0.0
    return sum(prices) / len(prices)
def calculate_sma(prices, period=14):
    """
    Calculate Simple Moving Average (SMA) of the most recent N periods.
    Returns float or None if insufficient price history.
    """
    values = [float(p) for p in prices if p > 0 and p == p]
    if period < 1 or len(values) < period:
        return None
    return sum(values[-period:]) / period
def calculate_ema(prices, period=14):
    """
    Calculate Exponential Moving Average (EMA).
    Formula: Multiplier = 2 / (period + 1)
             EMA = (Price_today * Multiplier) + (EMA_yesterday * (1 - Multiplier))
    """
    values = [float(p) for p in prices if p > 0 and p == p]
    if period < 1 or len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    # Seed EMA with initial SMA
    ema = sum(values[:period]) / period
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
    return ema
def calculate_rsi(prices, period=14):
    """
    Calculate Relative Strength Index (RSI) using Wilder's Smoothed Moving Average (RMA).
    Standard indicator used across TradingView, Binance, and Bloomberg Terminal.
    Returns float between 0.0 and 100.0, or None if insufficient history.
    """
    values = [float(p) for p in prices if p > 0 and p == p]
    if period < 1 or len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    # Initial average gain / loss
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder's smoothing technique
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Bound strictly between 0 and 100
    return max(0.0, min(100.0, rsi))
def calculate_macd(prices, fast_period=12, slow_period=26, signal_period=9):
    """
    Calculate Moving Average Convergence Divergence (MACD).
    Returns dict:
    {
        'macd': float,       # Fast EMA (12) - Slow EMA (26)
        'signal': float,     # 9-period EMA of MACD line
        'histogram': float   # MACD Line - Signal Line
    }
    or None if insufficient data.
    """
    values = [float(p) for p in prices if p > 0 and p == p]
    if not (0 < fast_period < slow_period and signal_period > 0):
        raise ValueError("Invalid MACD period parameters.")
    min_required = slow_period + signal_period - 1
    if len(values) < min_required:
        return None
    mult_fast = 2.0 / (fast_period + 1.0)
    mult_slow = 2.0 / (slow_period + 1.0)
    fast_ema = sum(values[:fast_period]) / fast_period
    slow_ema = sum(values[:slow_period]) / slow_period
    macd_history = []
    for i, price in enumerate(values):
        if i >= fast_period:
            fast_ema = (price - fast_ema) * mult_fast + fast_ema
        if i >= slow_period:
            slow_ema = (price - slow_ema) * mult_slow + slow_ema
        if i >= slow_period - 1:
            macd_history.append(fast_ema - slow_ema)
    if len(macd_history) < signal_period:
        return None
    # Calculate 9-period EMA of MACD Line (Signal Line)
    signal_line = sum(macd_history[:signal_period]) / signal_period
    mult_signal = 2.0 / (signal_period + 1.0)
    for val in macd_history[signal_period:]:
        signal_line = (val - signal_line) * mult_signal + signal_line
    latest_macd = macd_history[-1]
    histogram = latest_macd - signal_line
    return {
        "macd": latest_macd,
        "signal": signal_line,
        "histogram": histogram
    }
def calculate_bollinger_bands(prices, period=20, num_std=2):
    """
    Calculate Bollinger Bands (Upper, Middle SMA, Lower, and Bandwidth).
    Returns dict or None.
    """
    values = [float(p) for p in prices if p > 0 and p == p]
    if period < 2 or len(values) < period:
        return None
    slice_vals = values[-period:]
    sma = sum(slice_vals) / period
    variance = sum((x - sma) ** 2 for x in slice_vals) / period
    std_dev = math.sqrt(variance)
    upper_band = sma + (std_dev * num_std)
    lower_band = sma - (std_dev * num_std)
    return {
        "upper": upper_band,
        "middle": sma,
        "lower": lower_band,
        "std_dev": std_dev,
        "bandwidth": (upper_band - lower_band) / sma if sma else 0.0
    }
def calculate_indicators(market_data):
    """
    Main aggregator: Computes full technical indicator suite from market candles.
    Returns:
    {
        'price_count': int,
        'sma_14': float or None,
        'ema_14': float or None,
        'rsi_14': float or None,
        'macd': dict or None,
        'bollinger': dict or None
    }
    """
    prices = _get_prices(market_data)
    return {
        "price_count": len(prices),
        "sma_14": calculate_sma(prices, 14),
        "ema_14": calculate_ema(prices, 14),
        "rsi_14": calculate_rsi(prices, 14),
        "macd": calculate_macd(prices),
        "bollinger": calculate_bollinger_bands(prices, 20),
    }
# =========================================================
# STANDALONE DIAGNOSTIC TEST
# =========================================================
if __name__ == "__main__":
    print("Testing Indicators Module...")
    # Simulated prices sequence (50 candles)
    test_prices = [
        60000 + (i * 25) + (100 if i % 2 == 0 else -80)
        for i in range(50)
    ]
    indicators = calculate_indicators(test_prices)
    print(f"Candles Processed: {indicators['price_count']}")
    print(f"SMA (14): {indicators['sma_14']:.2f}")
    print(f"EMA (14): {indicators['ema_14']:.2f}")
    print(f"RSI (14): {indicators['rsi_14']:.2f}")
    if indicators['macd']:
        print(f"MACD: {indicators['macd']['macd']:.4f} | Signal: {indicators['macd']['signal']:.4f} | Hist: {indicators['macd']['histogram']:+.4f}")
    if indicators['bollinger']:
        print(f"Bollinger Bands: Upper={indicators['bollinger']['upper']:.2f}, Mid={indicators['bollinger']['middle']:.2f}, Lower={indicators['bollinger']['lower']:.2f}")
    print("All indicator calculations completed successfully!")
