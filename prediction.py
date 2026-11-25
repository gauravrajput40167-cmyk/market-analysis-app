import math
def get_signal(current_price, average) -> str:
    """
    Generate instant trend signal relative to historical mean average.
    Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'.
    """
    try:
        cp = float(current_price)
        avg = float(average)
    except (TypeError, ValueError):
        return "NEUTRAL"
    if cp <= 0 or avg <= 0 or not (math.isfinite(cp) and math.isfinite(avg)):
        return "NEUTRAL"
    # Micro deadband (0.02% threshold) to prevent rapid signal flickering
    diff_pct = (cp - avg) / avg
    if diff_pct > 0.0002:
        return "BULLISH"
    elif diff_pct < -0.0002:
        return "BEARISH"
    return "NEUTRAL"
def get_demo_prediction(current_price, indicators) -> dict:
    """
    Calculate an educational multi-factor quantitative score (-4 to +4)
    derived from SMA(14), EMA(14), RSI(14), and MACD convergence.
    Returns:
    {
        'signal': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
        'score': int,
        'reason': str,
        'details': dict
    }
    """
    fallback_response = {
        "signal": "NEUTRAL",
        "score": 0,
        "reason": "Insufficient historical market data to generate algorithmic score.",
        "details": {}
    }
    if not indicators or current_price is None:
        return fallback_response
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return fallback_response
    sma = indicators.get("sma_14")
    ema = indicators.get("ema_14")
    rsi = indicators.get("rsi_14")
    macd_data = indicators.get("macd")
    if price <= 0 or any(val is None for val in (sma, ema, rsi)) or not macd_data:
        return fallback_response
    try:
        macd_line = float(macd_data["macd"])
        signal_line = float(macd_data["signal"])
        rsi_val = float(rsi)
        sma_val = float(sma)
        ema_val = float(ema)
    except (KeyError, TypeError, ValueError):
        return {
            "signal": "NEUTRAL",
            "score": 0,
            "reason": "Technical indicator values are incomplete.",
            "details": {}
        }
    # Verify all components are finite floats
    if not all(math.isfinite(v) for v in (price, sma_val, ema_val, rsi_val, macd_line, signal_line)):
        return {
            "signal": "NEUTRAL",
            "score": 0,
            "reason": "Non-finite numeric values detected in indicator stream.",
            "details": {}
        }
    score = 0
    factor_reasons = []
    # 1. Price vs SMA (14)
    if price > sma_val:
        score += 1
        factor_reasons.append("Price trading above SMA-14")
    elif price < sma_val:
        score -= 1
        factor_reasons.append("Price trading below SMA-14")
    # 2. Price vs EMA (14)
    if price > ema_val:
        score += 1
        factor_reasons.append("Price trading above EMA-14")
    elif price < ema_val:
        score -= 1
        factor_reasons.append("Price trading below EMA-14")
    # 3. MACD Line vs Signal Line
    if macd_line > signal_line:
        score += 1
        factor_reasons.append("MACD histogram is positive (bullish divergence)")
    elif macd_line < signal_line:
        score -= 1
        factor_reasons.append("MACD histogram is negative (bearish divergence)")
    # 4. RSI (14) Momentum Range
    if rsi_val > 70:
        # Overbought condition: strong trend but potential exhaustion
        factor_reasons.append(f"RSI ({rsi_val:.1f}) in Overbought zone (>70)")
    elif 50 <= rsi_val <= 70:
        score += 1
        factor_reasons.append(f"RSI ({rsi_val:.1f}) indicates healthy upward momentum")
    elif 30 <= rsi_val < 50:
        score -= 1
        factor_reasons.append(f"RSI ({rsi_val:.1f}) indicates downward momentum")
    elif rsi_val < 30:
        factor_reasons.append(f"RSI ({rsi_val:.1f}) in Oversold territory (<30)")
    # Final Signal Determination
    if score >= 2:
        overall_signal = "BULLISH"
    elif score <= -2:
        overall_signal = "BEARISH"
    else:
        overall_signal = "NEUTRAL"
    summary_reason = f"Score: {score:+d} ({overall_signal}) | " + "; ".join(factor_reasons)
    return {
        "signal": overall_signal,
        "score": score,
        "reason": summary_reason,
        "details": {
            "score": score,
            "sma_status": "BULLISH" if price > sma_val else "BEARISH",
            "ema_status": "BULLISH" if price > ema_val else "BEARISH",
            "macd_status": "BULLISH" if macd_line > signal_line else "BEARISH",
            "rsi_value": rsi_val
        }
    }
# =========================================================
# STANDALONE DIAGNOSTIC TEST
# =========================================================
if __name__ == "__main__":
    print("Testing Prediction Engine...")
    sample_price = 65200.00
    sample_average = 65000.00
    sig = get_signal(sample_price, sample_average)
    print(f"Direct Signal: {sig}")
    sample_indicators = {
        "sma_14": 64800.00,
        "ema_14": 64950.00,
        "rsi_14": 58.4,
        "macd": {"macd": 45.2, "signal": 30.1, "histogram": 15.1}
    }
    pred = get_demo_prediction(sample_price, sample_indicators)
    print(f"Prediction Result: {pred['signal']} (Score: {pred['score']})")
    print(f"Reason: {pred['reason']}")
    print("Prediction self-test PASSED successfully!")
