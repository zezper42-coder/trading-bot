from datetime import timezone

from alpaca.data.enums import DataFeed

from trading_bot.adapters.alpaca import AlpacaBroker
from trading_bot.domain import AssetClass, Instrument


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def test_recent_crypto_bars_use_rest_endpoint(monkeypatch) -> None:
    seen = {}

    def fake_get(url, *, headers, params, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        seen["timeout"] = timeout
        return _FakeResponse(
            {
                "bars": {
                    "BTC/USD": [
                        {
                            "t": "2026-04-05T12:27:00Z",
                            "o": 66907.2,
                            "h": 66918.0,
                            "l": 66905.0,
                            "c": 66917.7525,
                            "v": 0.000146,
                        },
                        {
                            "t": "2026-04-05T12:26:00Z",
                            "o": 66907.2,
                            "h": 66910.0,
                            "l": 66900.0,
                            "c": 66907.2,
                            "v": 0.0,
                        },
                    ]
                }
            }
        )

    monkeypatch.setattr("trading_bot.adapters.alpaca.requests.get", fake_get)

    broker = AlpacaBroker("key-id", "secret-key", paper=True, stock_feed=DataFeed.IEX)
    bars = broker.get_recent_bars(Instrument("BTC/USD", AssetClass.CRYPTO), limit=2)

    assert seen["url"] == "https://data.alpaca.markets/v1beta3/crypto/us/bars"
    assert seen["headers"]["APCA-API-KEY-ID"] == "key-id"
    assert seen["headers"]["APCA-API-SECRET-KEY"] == "secret-key"
    assert seen["params"]["symbols"] == "BTC/USD"
    assert seen["params"]["timeframe"] == "1Min"
    assert seen["params"]["sort"] == "desc"
    assert "start" not in seen["params"]
    assert "end" not in seen["params"]
    assert seen["timeout"] == 20

    assert len(bars) == 2
    assert bars[0].timestamp.tzinfo is timezone.utc
    assert bars[0].close == 66907.2
    assert bars[1].close == 66917.7525
