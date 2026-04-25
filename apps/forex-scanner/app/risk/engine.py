"""Risk model for stop loss, take profit, and RR validation."""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import AppSettings
from app.core.types import DirectionBias, RawSetup, RiskPlan, TradingStyle


@dataclass(frozen=True)
class RiskDecision:
    """Risk plan plus diagnostic levels when the setup cannot be traded."""

    plan: RiskPlan | None
    rejection_reason: str | None
    diagnostic_plan: RiskPlan | None = None
    required_min_rr: float | None = None


class RiskEngine:
    """Convert raw setup candidates into conservative executable risk plans."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def plan(self, setup: RawSetup, style: TradingStyle) -> RiskDecision:
        """Build a conservative SL/TP plan or reject the setup."""

        min_rr = self.settings.styles[style].min_rr
        if setup.direction == DirectionBias.NO_TRADE:
            return RiskDecision(plan=None, rejection_reason="setup has no trade direction", required_min_rr=min_rr)

        stop_method, stop_loss = self._choose_stop(setup)
        if stop_loss is None:
            return RiskDecision(plan=None, rejection_reason="no valid stop-loss candidate", required_min_rr=min_rr)

        risk = _directional_risk(setup.direction, setup.entry, stop_loss)
        if risk <= 0.0:
            return RiskDecision(plan=None, rejection_reason="stop loss is on the wrong side of entry", required_min_rr=min_rr)

        target_method, take_profit = self._choose_target(setup, risk, min_rr)
        if take_profit is None:
            diagnostic_plan = self._diagnostic_plan(
                setup=setup,
                stop_method=stop_method,
                stop_loss=stop_loss,
                risk=risk,
                min_rr=min_rr,
                reason="no realistic take-profit candidate meets the minimum RR",
            )
            return RiskDecision(
                plan=None,
                rejection_reason="no realistic take-profit candidate meets the minimum RR",
                diagnostic_plan=diagnostic_plan,
                required_min_rr=min_rr,
            )

        rr = _risk_reward(setup.direction, setup.entry, stop_loss, take_profit)
        if rr < min_rr - 1e-9:
            tp1, tp2, tp3 = self._build_targets(setup, risk, min_rr, take_profit)
            diagnostic_plan = RiskPlan(
                entry=setup.entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                risk_reward=round(rr, 6),
                tp1_risk_reward=round(_risk_reward(setup.direction, setup.entry, stop_loss, tp1), 6),
                tp2_risk_reward=round(_risk_reward(setup.direction, setup.entry, stop_loss, tp2), 6),
                tp3_risk_reward=round(_risk_reward(setup.direction, setup.entry, stop_loss, tp3), 6),
                stop_method=stop_method,
                target_method=target_method,
                target_profile=self.settings.risk.target_profile,
                rejection_reason=f"risk/reward {rr:.2f} is below minimum {min_rr:.2f}",
            )
            return RiskDecision(
                plan=None,
                rejection_reason=f"risk/reward {rr:.2f} is below minimum {min_rr:.2f}",
                diagnostic_plan=diagnostic_plan,
                required_min_rr=min_rr,
            )

        tp1, tp2, tp3 = self._build_targets(setup, risk, min_rr, take_profit)
        return RiskDecision(
            plan=RiskPlan(
                entry=setup.entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                risk_reward=round(rr, 6),
                tp1_risk_reward=round(_risk_reward(setup.direction, setup.entry, stop_loss, tp1), 6),
                tp2_risk_reward=round(_risk_reward(setup.direction, setup.entry, stop_loss, tp2), 6),
                tp3_risk_reward=round(_risk_reward(setup.direction, setup.entry, stop_loss, tp3), 6),
                stop_method=stop_method,
                target_method=target_method,
                target_profile=self.settings.risk.target_profile,
            ),
            rejection_reason=None,
            required_min_rr=min_rr,
        )

    def _choose_stop(self, setup: RawSetup) -> tuple[str, float | None]:
        directional = _valid_stop_candidates(setup)
        if not directional:
            return "", None
        if self.settings.risk.conservative_stop:
            if setup.direction == DirectionBias.LONG:
                method, price = min(directional.items(), key=lambda item: item[1])
            else:
                method, price = max(directional.items(), key=lambda item: item[1])
        else:
            method, price = max(
                directional.items(),
                key=lambda item: _directional_risk(setup.direction, setup.entry, item[1]),
            )
        return method, price

    def _diagnostic_plan(
        self,
        setup: RawSetup,
        stop_method: str,
        stop_loss: float,
        risk: float,
        min_rr: float,
        reason: str,
    ) -> RiskPlan:
        target_method, take_profit = self._diagnostic_target(setup, risk, min_rr)
        rr = _risk_reward(setup.direction, setup.entry, stop_loss, take_profit)
        return RiskPlan(
            entry=setup.entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            tp1=take_profit,
            tp2=take_profit,
            tp3=take_profit,
            risk_reward=round(rr, 6),
            tp1_risk_reward=round(rr, 6),
            tp2_risk_reward=round(rr, 6),
            tp3_risk_reward=round(rr, 6),
            stop_method=stop_method,
            target_method=target_method,
            target_profile=self.settings.risk.target_profile,
            rejection_reason=reason,
        )

    def _diagnostic_target(self, setup: RawSetup, risk: float, min_rr: float) -> tuple[str, float]:
        candidates = _valid_target_candidates(setup)
        nearest_method, nearest_price = _nearest_directional_target(setup, candidates)
        if nearest_price is not None:
            return nearest_method, nearest_price

        if candidates:
            if setup.direction == DirectionBias.LONG:
                return min(candidates.items(), key=lambda item: item[1] - setup.entry)
            return min(candidates.items(), key=lambda item: setup.entry - item[1])

        fixed_target = setup.entry + risk * min_rr if setup.direction == DirectionBias.LONG else setup.entry - risk * min_rr
        return "fixed_rr", fixed_target

    def _choose_target(self, setup: RawSetup, risk: float, min_rr: float) -> tuple[str, float | None]:
        fixed_target = setup.entry + risk * min_rr if setup.direction == DirectionBias.LONG else setup.entry - risk * min_rr
        candidates = _valid_target_candidates(setup)

        candidates["fixed_rr"] = fixed_target
        valid = {
            method: price
            for method, price in candidates.items()
            if _risk_reward(
                setup.direction,
                setup.entry,
                setup.entry - risk if setup.direction == DirectionBias.LONG else setup.entry + risk,
                price,
            )
            >= min_rr - 1e-9
        }
        if not valid:
            return "", None
        profile = self.settings.risk.target_profile
        if profile == "aggressive":
            if setup.direction == DirectionBias.LONG:
                method, price = max(valid.items(), key=lambda item: item[1] - setup.entry)
            else:
                method, price = max(valid.items(), key=lambda item: setup.entry - item[1])
            return method, price
        if profile == "conservative":
            technical = {
                method: price
                for method, price in valid.items()
                if method.startswith("next_") or "bollinger" in method
            }
            if technical:
                if setup.direction == DirectionBias.LONG:
                    return min(technical.items(), key=lambda item: item[1] - setup.entry)
                return min(technical.items(), key=lambda item: setup.entry - item[1])
        if profile == "balanced" and "fixed_rr" in valid:
            return "fixed_rr", valid["fixed_rr"]
        if setup.direction == DirectionBias.LONG:
            method, price = min(valid.items(), key=lambda item: item[1] - setup.entry)
        else:
            method, price = min(valid.items(), key=lambda item: setup.entry - item[1])
        return method, price

    def _build_targets(self, setup: RawSetup, risk: float, min_rr: float, chosen_target: float) -> tuple[float, float, float]:
        fixed_one = setup.entry + risk if setup.direction == DirectionBias.LONG else setup.entry - risk
        fixed_min = setup.entry + risk * min_rr if setup.direction == DirectionBias.LONG else setup.entry - risk * min_rr
        fixed_extended = setup.entry + risk * max(min_rr * 1.45, min_rr + 0.5) if setup.direction == DirectionBias.LONG else setup.entry - risk * max(min_rr * 1.45, min_rr + 0.5)
        candidates = [fixed_one, fixed_min, chosen_target, fixed_extended]
        nearest_method, nearest_price = _nearest_directional_target(setup, _valid_target_candidates(setup))
        if nearest_method and nearest_price is not None:
            candidates.append(nearest_price)
        atr_target = _valid_target_candidates(setup).get("atr_extension")
        if atr_target is not None:
            candidates.append(atr_target)

        if setup.direction == DirectionBias.LONG:
            ordered = sorted({round(price, 10) for price in candidates if price > setup.entry})
        else:
            ordered = sorted({round(price, 10) for price in candidates if price < setup.entry}, reverse=True)
        while len(ordered) < 3:
            multiplier = len(ordered) + 1
            extension = setup.entry + risk * multiplier if setup.direction == DirectionBias.LONG else setup.entry - risk * multiplier
            ordered.append(round(extension, 10))
        return float(ordered[0]), float(ordered[min(1, len(ordered) - 1)]), float(ordered[min(2, len(ordered) - 1)])


def _valid_stop_candidates(setup: RawSetup) -> dict[str, float]:
    if setup.direction == DirectionBias.LONG:
        return {method: price for method, price in setup.stop_candidates.items() if price < setup.entry}
    if setup.direction == DirectionBias.SHORT:
        return {method: price for method, price in setup.stop_candidates.items() if price > setup.entry}
    return {}


def _valid_target_candidates(setup: RawSetup) -> dict[str, float]:
    if setup.direction == DirectionBias.LONG:
        return {method: price for method, price in setup.target_candidates.items() if price > setup.entry}
    if setup.direction == DirectionBias.SHORT:
        return {method: price for method, price in setup.target_candidates.items() if price < setup.entry}
    return {}


def _nearest_directional_target(setup: RawSetup, candidates: dict[str, float]) -> tuple[str, float | None]:
    technical = {
        method: price
        for method, price in candidates.items()
        if method.startswith("next_") or "bollinger" in method
    }
    if not technical:
        return "", None
    if setup.direction == DirectionBias.LONG:
        return min(technical.items(), key=lambda item: item[1] - setup.entry)
    return min(technical.items(), key=lambda item: setup.entry - item[1])


def _directional_risk(direction: DirectionBias, entry: float, stop_loss: float) -> float:
    if direction == DirectionBias.LONG:
        return entry - stop_loss
    if direction == DirectionBias.SHORT:
        return stop_loss - entry
    return 0.0


def _risk_reward(direction: DirectionBias, entry: float, stop_loss: float, take_profit: float) -> float:
    risk = _directional_risk(direction, entry, stop_loss)
    if risk <= 0.0:
        return 0.0
    if direction == DirectionBias.LONG:
        reward = take_profit - entry
    elif direction == DirectionBias.SHORT:
        reward = entry - take_profit
    else:
        reward = 0.0
    return max(0.0, reward / risk)
