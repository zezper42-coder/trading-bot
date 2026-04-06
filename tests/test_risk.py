from trading_bot.domain import (
    AccountSnapshot,
    AssetClass,
    BrokerCapabilities,
    Instrument,
    OrderSide,
    Position,
    Signal,
    SignalAction,
)
from trading_bot.risk import RiskManager


def test_risk_manager_caps_order_to_buying_power() -> None:
    manager = RiskManager(
        risk_per_trade=0.005,
        cash_buffer=0.20,
        max_open_positions=2,
        min_notional_usd=50,
    )
    signal = Signal(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        action=SignalAction.BUY,
        price=100,
        reason="test",
        stop_price=95,
        target_leverage=10,
    )
    account = AccountSnapshot(equity=10_000, cash=5_000, buying_power=900)

    plan = manager.build_order(
        signal,
        account,
        position=None,
        open_positions_count=0,
        broker_capabilities=BrokerCapabilities("alpaca", 4.0, False),
    )

    assert plan is not None
    assert plan.capped_by_buying_power is True
    assert plan.qty == 9.0


def test_risk_manager_uses_position_qty_on_sell() -> None:
    manager = RiskManager(
        risk_per_trade=0.005,
        cash_buffer=0.20,
        max_open_positions=2,
        min_notional_usd=50,
    )
    signal = Signal(
        instrument=Instrument("BTC/USD", AssetClass.CRYPTO),
        action=SignalAction.SELL,
        price=50_000,
        reason="exit",
    )
    position = Position(
        symbol="BTC/USD",
        qty=0.25,
        market_value=12_500,
        avg_entry_price=48_000,
    )
    account = AccountSnapshot(equity=20_000, cash=2_000, buying_power=2_000)

    plan = manager.build_order(
        signal,
        account,
        position=position,
        open_positions_count=1,
        broker_capabilities=BrokerCapabilities("alpaca", 4.0, False),
    )

    assert plan is not None
    assert plan.qty == 0.25


def test_risk_manager_builds_short_order_for_negative_stock_signal() -> None:
    manager = RiskManager(
        risk_per_trade=0.005,
        cash_buffer=0.20,
        max_open_positions=2,
        min_notional_usd=50,
    )
    signal = Signal(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        action=SignalAction.SHORT,
        price=200,
        reason="negative surprise",
        stop_price=206,
        target_leverage=10,
    )
    account = AccountSnapshot(equity=10_000, cash=5_000, buying_power=1_500)

    plan = manager.build_order(
        signal,
        account,
        position=None,
        open_positions_count=0,
        broker_capabilities=BrokerCapabilities("alpaca", 4.0, False),
    )

    assert plan is not None
    assert plan.side is OrderSide.SELL
    assert plan.qty == 7.5


def test_risk_manager_covers_short_position_with_buy() -> None:
    manager = RiskManager(
        risk_per_trade=0.005,
        cash_buffer=0.20,
        max_open_positions=2,
        min_notional_usd=50,
    )
    signal = Signal(
        instrument=Instrument("TSLA", AssetClass.STOCK),
        action=SignalAction.COVER,
        price=190,
        reason="cover short",
    )
    position = Position(
        symbol="TSLA",
        qty=-3,
        market_value=570,
        avg_entry_price=200,
    )
    account = AccountSnapshot(equity=20_000, cash=5_000, buying_power=5_000)

    plan = manager.build_order(
        signal,
        account,
        position=position,
        open_positions_count=1,
        broker_capabilities=BrokerCapabilities("alpaca", 4.0, False),
    )

    assert plan is not None
    assert plan.side is OrderSide.BUY
    assert plan.qty == 3


def test_risk_manager_scales_position_size_with_signal_risk_multiplier() -> None:
    manager = RiskManager(
        risk_per_trade=0.0035,
        cash_buffer=0.20,
        max_open_positions=5,
        min_notional_usd=50,
    )
    account = AccountSnapshot(equity=10_000, cash=10_000, buying_power=10_000)
    broker_capabilities = BrokerCapabilities("alpaca", 4.0, False)

    base_signal = Signal(
        instrument=Instrument("AAA", AssetClass.STOCK),
        action=SignalAction.BUY,
        price=20,
        reason="earnings beat",
        stop_price=18,
        risk_multiplier=1.0,
    )
    strong_signal = Signal(
        instrument=Instrument("AAA", AssetClass.STOCK),
        action=SignalAction.BUY,
        price=20,
        reason="strong earnings beat",
        stop_price=18,
        risk_multiplier=2.0,
    )

    base_plan = manager.build_order(
        base_signal,
        account,
        position=None,
        open_positions_count=0,
        broker_capabilities=broker_capabilities,
    )
    strong_plan = manager.build_order(
        strong_signal,
        account,
        position=None,
        open_positions_count=0,
        broker_capabilities=broker_capabilities,
    )

    assert base_plan is not None
    assert strong_plan is not None
    assert strong_plan.qty > base_plan.qty
    assert strong_plan.risk_multiplier == 2.0


def test_risk_manager_uses_signal_risk_per_trade_override() -> None:
    manager = RiskManager(
        risk_per_trade=0.0035,
        cash_buffer=0.20,
        max_open_positions=5,
        min_notional_usd=50,
    )
    account = AccountSnapshot(equity=10_000, cash=10_000, buying_power=10_000)
    broker_capabilities = BrokerCapabilities("alpaca", 4.0, False)
    signal = Signal(
        instrument=Instrument("USO", AssetClass.STOCK),
        action=SignalAction.BUY,
        price=50,
        reason="oil policy trade",
        stop_price=48,
        risk_per_trade_override=0.01,
    )

    plan = manager.build_order(
        signal,
        account,
        position=None,
        open_positions_count=0,
        broker_capabilities=broker_capabilities,
    )

    assert plan is not None
    assert plan.risk_per_trade_used == 0.01
