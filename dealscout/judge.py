"""Rule-based deal evaluation. M2 layers LLM analysis on top of this."""

from dealscout.models import Deal, PricePoint, WatchRule


def evaluate(rule: WatchRule, points: list[PricePoint]) -> Deal | None:
    if not points:
        return None
    best = min(points, key=lambda p: p.price)
    reasons = []
    if rule.max_price is not None and best.price <= rule.max_price:
        reasons.append(f"price {best.price:.2f} <= target {rule.max_price:.2f}")
    if rule.min_cut is not None and best.cut >= rule.min_cut:
        reasons.append(f"cut {best.cut}% >= target {rule.min_cut}%")
    if not reasons:
        return None
    assert rule.id is not None, "rule must be persisted (have an id) before evaluation"
    return Deal(watch_id=rule.id, title=rule.title, best=best, reason="; ".join(reasons))
