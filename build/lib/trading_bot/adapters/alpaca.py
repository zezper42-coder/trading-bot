from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import (
    CryptoHistoricalDataClient,
    NewsClient,
    StockHistoricalDataClient,
)
from alpaca.data.requests import CryptoBarsRequest, NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import MarketOrderRequest

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
            start = end - timedelta(minutes=max(limit * 3, 60))
            request = CryptoBarsRequest(
                symbol_or_symbols=instrument.symbol,
                timeframe=TimeFrame.Minute,
                start=start,
                end=end,
                limit=limit,
            )
            response = self._get_crypto_data_client().get_crypto_bars(request)
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

    def submit_market_order(self, plan: OrderPlan):
        self._require_trading_client()
        order_request = MarketOrderRequest(
            symbol=plan.instrument.symbol,
            side=AlpacaOrderSide.BUY if plan.side is OrderSide.BUY else AlpacaOrderSide.SELL,
            time_in_force=(
                TimeInForce.DAY
                if plan.instrument.asset_class is AssetClass.STOCK
                else TimeInForce.GTC
            ),
            qty=plan.qty,
        )
        return self.trading_client.submit_order(order_request)

    def _require_trading_client(self) -> None:
        if self.trading_client is not None:
            return
        raise RuntimeError("Trading-klienten er ikke initialisert. Sjekk Alpaca-nøklene.")

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
