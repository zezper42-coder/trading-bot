from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

import requests

from trading_bot.domain import OrderPlan, PreEarningsAnalysis, Signal


@dataclass(frozen=True)
class TelegramNotifier:
    bot_token: str
    chat_id: str
    disable_notification: bool = False
    message_thread_id: int | None = None
    timeout_seconds: float = 10.0
    session: requests.Session | None = None

    def send_text_message(
        self,
        *,
        text: str,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        response = self._session.post(
            self._url("sendMessage"),
            json=self._payload(
                text=text,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            ),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage feilet: {data}")

    def send_order_update(
        self,
        *,
        signal: Signal,
        plan: OrderPlan,
        timestamp: datetime,
        order_id: str,
        dry_run: bool,
    ) -> None:
        self.send_text_message(
            text=self._format_order_message(
                signal=signal,
                plan=plan,
                timestamp=timestamp,
                order_id=order_id,
                dry_run=dry_run,
            )
        )

    def send_earnings_watchlist(
        self,
        *,
        analyses: list[PreEarningsAnalysis],
        generated_at: datetime,
        limit: int,
    ) -> None:
        self.send_text_message(
            text=self._format_earnings_watchlist_message(
                analyses=analyses,
                generated_at=generated_at,
                limit=limit,
            )
        )

    @property
    def _session(self) -> requests.Session:
        return self.session or requests.Session()

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def _payload(
        self,
        *,
        text: str,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": self.disable_notification,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        elif self.message_thread_id is not None:
            payload["message_thread_id"] = self.message_thread_id
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return payload

    def _format_order_message(
        self,
        *,
        signal: Signal,
        plan: OrderPlan,
        timestamp: datetime,
        order_id: str,
        dry_run: bool,
    ) -> str:
        title = f"{'DRY RUN ' if dry_run else ''}{signal.action.value.upper()} {plan.instrument.symbol}"
        decision_reason = plan.signal_reason or signal.reason
        lines = [
            f"<b>{escape(title)}</b>",
            f"Begrunnelse: {escape(decision_reason)}",
            f"Tid: <code>{escape(timestamp.isoformat())}</code>",
            f"Pris: <code>{signal.price:.4f}</code>",
        ]
        if plan.qty is not None:
            lines.append(f"Qty: <code>{plan.qty:.6f}</code>")
        if plan.notional is not None:
            lines.append(f"Notional: <code>{plan.notional:.2f}</code>")
        lines.append(f"Ordre-ID: <code>{escape(order_id)}</code>")
        if abs(plan.risk_multiplier - 1.0) > 1e-9:
            lines.append(f"Risikofaktor: <code>{plan.risk_multiplier:.2f}x</code>")
        if plan.risk_per_trade_used is not None:
            lines.append(f"Risk/trade: <code>{plan.risk_per_trade_used:.4f}</code>")
        if signal.theme:
            lines.append(f"Tema: <code>{escape(signal.theme)}</code>")
        if signal.trade_score is not None:
            lines.append(f"Trade score: <code>{signal.trade_score:.4f}</code>")
        if signal.stop_price is not None:
            lines.append(f"Stop: <code>{signal.stop_price:.4f}</code>")
        if signal.anchor_price is not None:
            lines.append(f"Anchor: <code>{signal.anchor_price:.4f}</code>")
        if signal.event_id:
            lines.append(f"Event: <code>{escape(signal.event_id)}</code>")
        if signal.source:
            lines.append(f"Kilde: <code>{escape(signal.source)}</code>")
        if signal.exit_reason:
            lines.append(f"Exit: <code>{escape(signal.exit_reason)}</code>")
        if plan.capped_by_buying_power:
            lines.append("Buying power cap: <code>true</code>")
        return "\n".join(lines)

    def _format_earnings_watchlist_message(
        self,
        *,
        analyses: list[PreEarningsAnalysis],
        generated_at: datetime,
        limit: int,
    ) -> str:
        selected = analyses[:limit]
        lines = [
            "<b>Earnings Watchlist</b>",
            f"Tid: <code>{escape(generated_at.isoformat())}</code>",
            f"Antall kandidater: <code>{len(analyses)}</code>",
            "",
        ]
        for index, analysis in enumerate(selected, start=1):
            candidate = analysis.candidate
            reason = escape(analysis.reasons[0] if analysis.reasons else f"score {analysis.score:.1f}")
            lines.append(
                (
                    f"<b>{index}. {escape(candidate.symbol)}</b> "
                    f"<code>{analysis.score:.1f}</code> "
                    f"{escape(candidate.earnings_date.isoformat())} "
                    f"{escape(candidate.earnings_hour or 'tbd')}"
                )
            )
            lines.append(
                (
                    f"EPS <code>{candidate.eps_estimate:.2f}</code> | "
                    f"Revenue <code>{candidate.revenue_estimate:.0f}</code> | "
                    f"Pris <code>{candidate.last_price:.2f}</code>"
                )
            )
            lines.append(f"Begrunnelse: {reason}")
            lines.append("")
        return "\n".join(lines).strip()
