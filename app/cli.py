import argparse
import getpass
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Agent, AgentSession, ExecutionEvent, Role, SessionStatus, User
from app.security import hash_password

AGENTS = [
    ("Triage Copilot", "Routes incoming support issues to the right team.", "support"),
    ("Revenue Analyst", "Summarizes sales activity and forecast risk.", "analytics"),
    ("Ops Sentinel", "Watches internal systems and explains incidents.", "operations"),
    ("Docs Curator", "Keeps knowledge base answers current.", "knowledge"),
    ("QA Runner", "Runs scripted checks and reports regressions.", "quality"),
    ("Workflow Builder", "Drafts automation plans for routine tasks.", "automation"),
]


def bootstrap_user() -> None:
    settings = get_settings()
    if not settings.initial_user_email or not settings.initial_user_password:
        raise SystemExit("Set INITIAL_USER_EMAIL and INITIAL_USER_PASSWORD before bootstrap-user.")
    with SessionLocal() as db:
        email = settings.initial_user_email.lower()
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email)
            db.add(user)
        user.password_hash = hash_password(settings.initial_user_password)
        user.role = Role(settings.initial_user_role)
        user.is_active = True
        db.commit()
        print(f"Bootstrap user ready: {email} ({user.role.value})")


def reset_password(email: str) -> None:
    normalized_email = email.lower()
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords did not match.")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == normalized_email))
        if not user:
            raise SystemExit(f"User not found: {normalized_email}")
        user.password_hash = hash_password(password)
        db.commit()
        print(f"Password reset for existing user: {normalized_email}")


def seed() -> None:
    settings = get_settings()
    rng = random.Random(settings.seed_random_seed)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        agents: list[Agent] = []
        for name, description, category in AGENTS:
            agent = db.scalar(select(Agent).where(Agent.name == name))
            if not agent:
                agent = Agent(name=name, description=description, category=category, enabled=True)
                db.add(agent)
            agents.append(agent)
        db.flush()
        existing_sessions = db.scalar(select(AgentSession).limit(1))
        if existing_sessions:
            print("Seed skipped: sessions already exist. Use a fresh database for deterministic reseeding.")
            db.commit()
            return
        devices = ["desktop", "desktop", "desktop", "mobile", "mobile", "tablet"]
        browsers = ["Chrome", "Chrome", "Safari", "Firefox", "Edge"]
        systems = {"desktop": ["Windows", "macOS", "Linux"], "mobile": ["iOS", "Android"], "tablet": ["iPadOS", "Android"]}
        countries = ["US", "GB", "DE", "FR", "CA", "AU", "BR", "IN", "JP", "NL"]
        networks = ["fast-4g", "wifi", "wifi", "office-lan", "slow-4g", "lossy"]
        event_types = ["plan", "tool_call", "reasoning", "final_response"]
        for i in range(settings.seed_session_count):
            agent = rng.choice(agents)
            started = now - timedelta(minutes=rng.randint(0, 60 * 24 * 45), seconds=rng.randint(0, 59))
            device = rng.choice(devices)
            browser = rng.choice(browsers)
            network = rng.choice(networks)
            base_latency = {"office-lan": 700, "wifi": 1000, "fast-4g": 1300, "slow-4g": 2400, "lossy": 3600}[network]
            duration = max(120, int(rng.gauss(base_latency, base_latency * 0.35)))
            fail_probability = 0.03 + (0.06 if network in {"slow-4g", "lossy"} else 0)
            status = SessionStatus.failed if rng.random() < fail_probability else SessionStatus.completed
            if rng.random() < 0.01:
                status = SessionStatus.cancelled
            executions = rng.randint(1, 8)
            tokens = max(30, int(rng.gauss(900, 350))) * executions
            session = AgentSession(
                agent_id=agent.id,
                synthetic_user_id=f"user-{rng.randint(1000, 9999)}",
                status=status,
                started_at=started,
                ended_at=started + timedelta(milliseconds=duration),
                device_category=device,
                browser=browser,
                operating_system=rng.choice(systems[device]),
                country=rng.choice(countries),
                network_profile=network,
                execution_count=executions,
                token_usage=tokens,
                estimated_cost_usd=round(tokens / 1_000_000 * 2.5, 6),
                duration_ms=duration,
                error_code="MODEL_TIMEOUT" if status == SessionStatus.failed else None,
                error_message="Synthetic timeout caused by slow network profile" if status == SessionStatus.failed else None,
            )
            db.add(session)
            db.flush()
            for n in range(rng.randint(2, 5)):
                db.add(
                    ExecutionEvent(
                        session_id=session.id,
                        client_event_id=f"seed-{i}-{n}",
                        event_type=rng.choice(event_types),
                        timestamp=started + timedelta(milliseconds=int(duration * (n + 1) / 5)),
                        duration_ms=max(20, int(duration / max(executions, 1) * rng.uniform(0.5, 1.4))),
                        outcome="error" if status == SessionStatus.failed and n == 3 else "success",
                        token_usage=max(0, int(tokens / executions)),
                        details={"synthetic": True, "seed": settings.seed_random_seed},
                    )
                )
            if i and i % 2500 == 0:
                db.commit()
                print(f"Seeded {i} sessions")
        db.commit()
        print(f"Seed complete: {settings.seed_session_count} synthetic sessions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["bootstrap-user", "reset-password", "seed"])
    parser.add_argument("email", nargs="?")
    args = parser.parse_args()
    if args.command == "bootstrap-user":
        bootstrap_user()
    if args.command == "reset-password":
        if not args.email:
            raise SystemExit("Usage: agentpulse reset-password <email>")
        reset_password(args.email)
    if args.command == "seed":
        seed()


if __name__ == "__main__":
    main()

