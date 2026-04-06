from __future__ import annotations

from trading_bot.adapters.saxo import SaxoBroker
from trading_bot.domain import AssetClass, Instrument, OrderPlan, OrderSide


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self) -> None:
        self.headers = {}
        self.requests: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, timeout: int = 15, **kwargs):
        self.requests.append((method, url, kwargs))
        if url.endswith("/port/v1/accounts/me"):
            return FakeResponse({"Data": [{"AccountKey": "abc-account"}]})
        if url.endswith("/port/v1/balances/me"):
            return FakeResponse(
                {
                    "TotalValue": 100000,
                    "CashBalance": 45000,
                    "AvailableFunds": 45000,
                }
            )
        if url.endswith("/ref/v1/instruments"):
            return FakeResponse(
                {
                    "Data": [
                        {
                            "Symbol": "EQNR",
                            "Uic": 1234,
                            "AssetType": "Stock",
                            "ExchangeId": "XOSL",
                        }
                    ]
                }
            )
        if url.endswith("/chart/v3/charts"):
            return FakeResponse(
                {
                    "Data": [
                        {
                            "Time": "2026-04-04T03:58:00Z",
                            "Open": 300,
                            "High": 301,
                            "Low": 299,
                            "Close": 300.5,
                            "Volume": 1000,
                        },
                        {
                            "Time": "2026-04-04T03:59:00Z",
                            "Open": 300.5,
                            "High": 302,
                            "Low": 300,
                            "Close": 301.5,
                            "Volume": 1200,
                        },
                    ]
                }
            )
        if url.endswith("/trade/v2/orders"):
            return FakeResponse({"OrderId": "order-1"})
        raise AssertionError(f"Uventet request: {method} {url}")


def test_saxo_broker_reads_account_snapshot() -> None:
    broker = SaxoBroker(
        access_token="token",
        environment="sim",
        account_key=None,
        default_exchange_id="XOSL",
        session=FakeSession(),
    )

    account = broker.get_account()

    assert account.equity == 100000
    assert account.cash == 45000
    assert account.buying_power == 45000


def test_saxo_broker_reads_recent_bars() -> None:
    broker = SaxoBroker(
        access_token="token",
        environment="sim",
        account_key="abc-account",
        default_exchange_id="XOSL",
        session=FakeSession(),
    )

    bars = broker.get_recent_bars(Instrument("EQNR", AssetClass.STOCK), limit=2)

    assert len(bars) == 2
    assert bars[-1].close == 301.5


def test_saxo_broker_places_market_order_with_whole_shares() -> None:
    session = FakeSession()
    broker = SaxoBroker(
        access_token="token",
        environment="sim",
        account_key="abc-account",
        default_exchange_id="XOSL",
        session=session,
    )

    result = broker.submit_market_order(
        OrderPlan(
            instrument=Instrument("EQNR", AssetClass.STOCK),
            side=OrderSide.BUY,
            qty=5.7,
            event_id="evt-1",
            signal_reason="test",
        )
    )

    assert result["OrderId"] == "order-1"
    method, url, kwargs = session.requests[-1]
    assert method == "POST"
    assert url.endswith("/trade/v2/orders")
    assert kwargs["json"]["Amount"] == 5
    assert kwargs["json"]["OrderType"] == "Market"
