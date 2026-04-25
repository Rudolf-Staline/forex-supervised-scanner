"""Execution adapter interface.

The scanner does not place live trades. This interface exists so paper trading
and any future broker integration can share the same small contract.
"""

from __future__ import annotations

from typing import Protocol

from app.execution.models import BrokerAccountState, ExecutionOrder, OrderRequest


class ExecutionAdapter(Protocol):
    """Minimal order-management contract for paper and future broker adapters."""

    def create_order_intent(self, request: OrderRequest) -> OrderRequest:
        """Validate and return the broker-neutral order intent."""

    def place_order(self, request: OrderRequest) -> ExecutionOrder:
        """Place a new order and return its tracked state."""

    def modify_order(
        self,
        order_id: str,
        *,
        stop_loss: float | None = None,
        take_profit: float | None = None,
    ) -> ExecutionOrder:
        """Modify stop or target on a pending/active order."""

    def close_order(self, order_id: str, exit_price: float, reason: str = "manual") -> ExecutionOrder:
        """Close an active order at the supplied price."""

    def partial_close_order(self, order_id: str, exit_price: float, fraction: float, reason: str = "manual_partial") -> ExecutionOrder:
        """Partially close an active order at the supplied price."""

    def cancel_order(self, order_id: str) -> ExecutionOrder:
        """Cancel a pending order."""

    def sync_positions(self) -> list[ExecutionOrder]:
        """Return current open orders/positions."""

    def query_order_status(self, order_id: str) -> ExecutionOrder:
        """Return the latest known order state."""

    def query_account_state(self) -> BrokerAccountState:
        """Return the current account or simulated account state."""

    def reconcile(self) -> list[ExecutionOrder]:
        """Run adapter-specific reconciliation and return tracked orders."""
