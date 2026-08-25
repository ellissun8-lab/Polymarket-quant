"""Pure BTC-5m market rotation validation and overlap schedules."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RotationSchedule:
    prediscover_ms:int;subscribe_next_ms:int;window_end_ms:int;unsubscribe_old_ms:int

def validate_btc5m_market(market):
    prefix="btc-updown-5m-"
    if not market.slug.startswith(prefix):raise ValueError("outside BTC-5m universe")
    try:start_s=int(market.slug[len(prefix):])
    except ValueError as exc:raise ValueError("malformed BTC-5m slug") from exc
    if start_s%300:raise ValueError("unaligned BTC-5m slug")
    if market.market_start_ms!=start_s*1000 or market.market_end_ms!=start_s*1000+300000:raise ValueError("market window disagrees with slug")
    if len(market.tokens)!=2 or {x[1] for x in market.tokens}!={"Up","Down"}:raise ValueError("invalid token outcome mapping")
    return True

def rotation_schedule(end_ms,prediscovery_seconds=60,overlap_seconds=15,grace_seconds=5):
    return RotationSchedule(end_ms-int(prediscovery_seconds*1000),end_ms-int(overlap_seconds*1000),end_ms,end_ms+int(grace_seconds*1000))

