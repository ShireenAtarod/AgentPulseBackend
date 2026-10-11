import random
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Agent, AgentSession, ExecutionEvent, SessionStatus


def run() -> None:
    settings = get_settings()
    rng = random.Random(settings.seed_random_seed + 17)
    delay = max(1, 60 / max(settings.simulator_rate_per_minute, 1))
    print(f"Simulator running: {settings.simulator_scenario}, {settings.simulator_rate_per_minute}/minute")
    while True:
        with SessionLocal() as db:
            agents = db.scalars(select(Agent).where(Agent.enabled.is_(True))).all()
            if agents:
                agent = rng.choice(agents)
                started = datetime.now(timezone.utc)
                scenario = settings.simulator_scenario
                duration = rng.randint(500, 1800)
                fail_chance = 0.03
                if scenario == "slow-execution":
                    duration *= 4
                if scenario == "elevated-error":
                    fail_chance = 0.20
                status = SessionStatus.failed if rng.random() < fail_chance else SessionStatus.completed
                session = AgentSession(
                    agent_id=agent.id,
                    synthetic_user_id=f"sim-{rng.randint(1000, 9999)}",
                    status=status,
                    started_at=started,
                    ended_at=started + timedelta(milliseconds=duration),
                    device_category=rng.choice(["desktop", "mobile", "tablet"]),
                    browser=rng.choice(["Chrome", "Safari", "Firefox", "Edge"]),
                    operating_system=rng.choice(["Windows", "macOS", "Linux", "iOS", "Android"]),
                    country=rng.choice(["US", "GB", "DE", "CA", "AU"]),
                    network_profile=rng.choice(["wifi", "fast-4g", "office-lan", "slow-4g"]),
                    execution_count=rng.randint(1, 6),
                    token_usage=rng.randint(200, 6000),
                    estimated_cost_usd=round(rng.randint(200, 6000) / 1_000_000 * 2.5, 6),
                    duration_ms=duration,
                    error_code="SIMULATED_ERROR" if status == SessionStatus.failed else None,
                    error_message="Optional simulator elevated-error scenario" if status == SessionStatus.failed else None,
                )
                db.add(session)
                db.flush()
                db.add(
                    ExecutionEvent(
                        session_id=session.id,
                        client_event_id=f"sim-{session.id}",
                        event_type="simulated_session",
                        timestamp=started,
                        duration_ms=duration,
                        outcome="error" if status == SessionStatus.failed else "success",
                        token_usage=session.token_usage,
                        details={"synthetic": True, "scenario": scenario},
                    )
                )
                db.commit()
        time.sleep(delay)


if __name__ == "__main__":
    run()

