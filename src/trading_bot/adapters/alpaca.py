from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from alpaca.data.enums import DataFeed
from alpaca.data.historical import (
    CryptoHistoricalDataClient,
    NewsClient,
    StockHistoricalDataClient,
)
from alpaca.data.requests import (
    CryptoBarsRequest,
    NewsRequest,
    StockBarsRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass as TradingAssetClass
from alpaca.trading.enums import AssetStatus
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import GetAssetsRequest, LimitOrderRequest, MarketOrderRequest

from trading_bot.domain import (
    AccountSnapshot,
    AssetClass,
    Bar,
    BrokerCapabilities,
    HeadlineContext,
    OrderPlan,
    OrderSide,
    Position,
    canonical_symbol,
)


class AlpacaBroker:
    def __init__(
        self,
        api_key: str | None,
        api_secret: str | None,
        paper: bool,
        stock_feed: DataFeed,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.stock_feed = stock_feed
        self.stock_data_client = None
        self.crypto_data_client = None
        self.news_client = None
        self.trading_client = None
        if api_key and api_secret:
            self.trading_client = TradingClient(api_key, api_secret, paper=paper)

    def get_account(self) -> AccountSnapshot:
        self._require_trading_client()
        account = self.trading_client.get_account()
        return AccountSnapshot(
            equity=float(account.equity),
            cash=float(account.cash),
            buying_power=float(account.buying_power),
        )

    def get_broker_capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="alpaca",
            max_leverage=4.0,
            supports_crypto_margin=False,
        )

    def get_all_positions(self) -> dict[str, Position]:
        self._require_trading_client()
        positions = {}
        for position in self.trading_client.get_all_positions():
            positions[canonical_symbol(position.symbol)] = Position(
                symbol=position.symbol,
                qty=float(position.qty),
                market_value=float(position.market_value),
                avg_entry_price=float(position.avg_entry_price),
            )
        return positions

    def list_active_tradable_us_equities(self):
        self._require_trading_client()
        assets = self.trading_client.get_all_assets(
            GetAssetsRequest(
                status=AssetStatus.ACTIVE,
                asset_class=TradingAssetClass.US_EQUITY,
            )
        )
        return tuple(asset for asset in assets if getattr(asset, "tradable", False))

    def get_recent_bars(self, instrument, limit: int) -> list[Bar]:
        end = datetime.now(timezone.utc)
        if instrument.asset_class is AssetClass.STOCK:
            start = end - timedelta(minutes=max(limit * 5, 60))
            request = StockBarsRequest(
                symbol_or_symbols=instrument.symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=limit,
                feed=self.stock_feed,
            )
            response = self._get_stock_data_client().get_stock_bars(request)
        else:
            return self._get_recent_crypto_bars(instrument.symbol, limit=limit)
        return self._response_to_bars(response.df, instrument.symbol, limit=limit)

    def get_historical_bars(self, instrument, start: datetime, end: datetime, timeframe: TimeFrame) -> list[Bar]:
        if instrument.asset_class is AssetClass.STOCK:
            request = StockBarsRequest(
                symbol_or_symbols=instrument.symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                feed=self.stock_feed,
            )
            response = self._get_stock_data_client().get_stock_bars(request)
        else:
            request = CryptoBarsRequest(
                symbol_or_symbols=instrument.symbol,
                timeframe=timeframe,
                start=start,
                end=end,
            )
            response = self._get_crypto_data_client().get_crypto_bars(request)
        return self._response_to_bars(response.df, instrument.symbol)

    def get_historical_stock_bars_batch(
        self,
        symbols: list[str],
        *,
        start: datetime,
        end: datetime,
        timeframe: TimeFrame,
        limit: int | None = None,
    ) -> dict[str, list[Bar]]:
        if not symbols:
            return {}
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            feed=self.stock_feed,
        )
        response = self._get_stock_data_client().get_stock_bars(request)
        return self._response_to_bar_map(response.df, limit=limit)

    def get_latest_stock_trades(self, symbols: list[str]) -> dict[str, object]:
        if not symbols:
            return {}
        request = StockLatestTradeRequest(
            symbol_or_symbols=symbols,
            feed=self.stock_feed,
        )
        response = self._get_stock_data_client().get_stock_latest_trade(request)
        return dict(response)

    def get_recent_headlines(
        self,
        instrument,
        since: datetime,
        limit: int = 5,
    ) -> tuple[HeadlineContext, ...]:
        if not self.api_key or not self.api_secret:
            return ()
        request = NewsRequest(
            start=since,
            end=datetime.now(timezone.utc),
            symbols=instrument.symbol,
            limit=limit,
        )
        response = self._get_news_client().get_news(request)
        news_items = getattr(response, "news", [])
        headlines: list[HeadlineContext] = []
        for item in news_items:
            headlines.append(
                HeadlineContext(
                    headline=item.headline,
                    source=item.source,
                    created_at=item.created_at,
                )
            )
        return tuple(headlines)

    def get_latest_market_price(self, instrument) -> tuple[float, datetime] | None:
        if instrument.asset_class is AssetClass.STOCK:
            latest = self.get_latest_stock_trades([instrument.symbol]).get(instrument.symbol)
            if latest is None:
                return None
            price = _object_float(latest, "price")
            timestamp = _object_datetime(latest, "timestamp")
            if price is None or timestamp is None:
                return None
            return price, timestamp
        return self._get_latest_crypto_price(instrument.symbol)

    def submit_market_order(self, plan: OrderPlan):
        self._require_trading_client()
        order_side = AlpacaOrderSide.BUY if plan.side is OrderSide.BUY else AlpacaOrderSide.SELL
        if plan.limit_price is not None:
            order_request = LimitOrderRequest(
                symbol=plan.instrument.symbol,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                qty=plan.qty,
                limit_price=round(plan.limit_price, 2),
                extended_hours=plan.extended_hours,
            )
        else:
            order_request = MarketOrderRequest(
                symbol=plan.instrument.symbol,
                side=order_side,
                time_in_force=(
                    TimeInForce.DAY
                    if plan.instrument.asset_class is AssetClass.STOCK
                    else TimeInForce.GTC
                ),
                qty=plan.qty,
                extended_hours=plan.extended_hours if plan.instrument.asset_class is AssetClass.STOCK else None,
            )
        return self.trading_client.submit_order(order_request)

    def cancel_all_orders(self) -> list[dict]:
        response = requests.delete(
            f"{self._trading_base_url()}/v2/orders",
            headers=self._trading_headers(),
            timeout=20,
        )
        response.raise_for_status()
        if not response.text:
            return []
        return response.json()

    def close_all_positions(self) -> list[dict]:
        response = requests.delete(
            f"{self._trading_base_url()}/v2/positions",
            headers=self._trading_headers(),
            timeout=20,
        )
        response.raise_for_status()
        if not response.text:
            return []
        return response.json()

    def list_open_orders(self) -> list[dict]:
        response = requests.get(
            f"{self._trading_base_url()}/v2/orders",
            headers=self._trading_headers(),
            params={"status": "open", "direction": "desc", "limit": 100},
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def _require_trading_client(self) -> None:
        if self.trading_client is not None:
            return
        raise RuntimeError("Trading-klienten er ikke initialisert. Sjekk Alpaca-nøklene.")

    def _trading_base_url(self) -> str:
        return "https://paper-api.alpaca.markets" if self.paper else "https://api.alpaca.markets"

    def _trading_headers(self) -> dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Trading-klienten er ikke initialisert. Sjekk Alpaca-nøklene.")
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    def _get_stock_data_client(self) -> StockHistoricalDataClient:
        if self.stock_data_client is not None:
            return self.stock_data_client
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Stock-data fra Alpaca krever API-nøkler.")
        self.stock_data_client = StockHistoricalDataClient(self.api_key, self.api_secret)
        return self.stock_data_client

    def _get_crypto_data_client(self) -> CryptoHistoricalDataClient:
        if self.crypto_data_client is not None:
            return self.crypto_data_client
        self.crypto_data_client = CryptoHistoricalDataClient(self.api_key, self.api_secret)
        return self.crypto_data_client

    def _get_recent_crypto_bars(self, symbol: str, *, limit: int) -> list[Bar]:
        try:
            return self._get_recent_crypto_bars_via_rest(symbol, limit=limit)
        except requests.RequestException:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=max(limit * 3, 60))
            request = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=limit,
            )
            response = self._get_crypto_data_client().get_crypto_bars(request)
            return self._response_to_bars(response.df, symbol, limit=limit)

    def _get_recent_crypto_bars_via_rest(
        self,
        symbol: str,
        *,
        limit: int,
    ) -> list[Bar]:
        headers = {}
        if self.api_key and self.api_secret:
            headers["APCA-API-KEY-ID"] = self.api_key
            headers["APCA-API-SECRET-KEY"] = self.api_secret
        response = requests.get(
            "https://data.alpaca.markets/v1beta3/crypto/us/bars",
            headers=headers,
            params={
                "symbols": symbol,
                "timeframe": "1Min",
                "limit": limit,
                "sort": "desc",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        items = list(reversed(payload.get("bars", {}).get(symbol, [])))
        bars: list[Bar] = []
        for item in items[-limit:]:
            bars.append(
                Bar(
                    timestamp=datetime.fromisoformat(item["t"].replace("Z", "+00:00")),
                    open=float(item["o"]),
                    high=float(item["h"]),
                    low=float(item["l"]),
                    close=float(item["c"]),
                    volume=float(item["v"]),
                )
            )
        return bars

    def _get_latest_crypto_price(self, symbol: str) -> tuple[float, datetime] | None:
        headers = {}
        if self.api_key and self.api_secret:
            headers["APCA-API-KEY-ID"] = self.api_key
            headers["APCA-API-SECRET-KEY"] = self.api_secret

        trade_response = requests.get(
            "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades",
            headers=headers,
            params={"symbols": symbol},
            timeout=20,
        )
        trade_response.raise_for_status()
        trade_payload = trade_response.json()
        trades = trade_payload.get("trades", {})
        if isinstance(trades, dict):
            trade = trades.get(symbol)
            if isinstance(trade, dict):
                price = _mapping_float(trade, "p")
                timestamp = _mapping_datetime(trade, "t")
                if price is not None and timestamp is not None:
                    return price, timestamp

        quote_response = requests.get(
            "https://data.alpaca.markets/v1beta3/crypto/us/latest/quotes",
            headers=headers,
            params={"symbols": symbol},
            timeout=20,
        )
        quote_response.raise_for_status()
        quote_payload = quote_response.json()
        quotes = quote_payload.get("quotes", {})
        if not isinstance(quotes, dict):
            return None
        quote = quotes.get(symbol)
        if not isinstance(quote, dict):
            return None
        ask = _mapping_float(quote, "ap")
        bid = _mapping_float(quote, "bp")
        timestamp = _mapping_datetime(quote, "t")
        if ask is None or bid is None or timestamp is None:
            return None
        return ((ask + bid) / 2.0), timestamp

    def _get_news_client(self) -> NewsClient:
        if self.news_client is not None:
            return self.news_client
        self.news_client = NewsClient(self.api_key, self.api_secret)
        return self.news_client

    def _response_to_bars(self, df: pd.DataFrame, symbol: str, limit: int | None = None) -> list[Bar]:
        if df.empty:
            return []

        frame = df.sort_index()
        if isinstance(frame.index, pd.MultiIndex):
            try:
                frame = frame.xs(symbol)
            except KeyError:
                return []

        if limit is not None:
            frame = frame.tail(limit)

        bars: list[Bar] = []
        for timestamp, row in frame.iterrows():
            bars.append(
                Bar(
                    timestamp=timestamp.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        return bars


def _object_float(payload: object, attr_name: str) -> float | None:
    value = getattr(payload, attr_name, None)
    if value in {None, ""} and isinstance(payload, dict):
        value = payload.get(attr_name)
    if value in {None, ""}:
        return None
    return float(value)


def _object_datetime(payload: object, attr_name: str) -> datetime | None:
    value = getattr(payload, attr_name, None)
    if value is None and isinstance(payload, dict):
        value = payload.get(attr_name)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _mapping_float(payload: dict, key: str) -> float | None:
    value = payload.get(key)
    if value in {None, ""}:
        return None
    return float(value)


def _mapping_datetime(payload: dict, key: str) -> datetime | None:
    value = payload.get(key)
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    def _response_to_bar_map(self, df: pd.DataFrame, limit: int | None = None) -> dict[str, list[Bar]]:
        if df.empty:
            return {}
        frame = df.sort_index()
        if not isinstance(frame.index, pd.MultiIndex):
            return {}
        bars_by_symbol: dict[str, list[Bar]] = {}
        for symbol in frame.index.get_level_values(0).unique():
            symbol_frame = frame.xs(symbol)
            if limit is not None:
                symbol_frame = symbol_frame.tail(limit)
            bars_by_symbol[symbol] = [
                Bar(
                    timestamp=timestamp.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
                for timestamp, row in symbol_frame.iterrows()
            ]
        return bars_by_symbol
