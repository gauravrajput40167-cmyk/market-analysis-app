from data import get_market_data
from indicators import calculate_average, calculate_indicators
from prediction import get_signal, get_demo_prediction

print("===== MARKET PREDICTION APP =====")

symbol = "BTCUSDT"
market_data = get_market_data(symbol)

if not market_data:
    print("Market data unavailable. Please try again.")
else:
    prices = [
        item["price"]
        for item in market_data
        if "price" in item
    ]

    current_price = prices[-1] if prices else None
    average = calculate_average(market_data)
    indicators = calculate_indicators(market_data)

    signal = (
        get_signal(current_price, average)
        if current_price is not None
        else "NEUTRAL"
    )

    demo_prediction = get_demo_prediction(
        current_price,
        indicators
    )

    print("Market Symbol:", symbol)
    print("Candles Received:", len(market_data))
    print("Current Price:", current_price)
    print("Average Price:", average)
    print("Basic Market Signal:", signal)
    print("Technical Indicators:", indicators)
    print(" Analysis:", demo_prediction)
