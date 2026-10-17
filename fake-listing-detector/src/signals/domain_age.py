from datetime import datetime, timezone
from urllib.parse import urlparse

def check_domain_age(domain: str) -> dict:
    if not domain:
        return {"status": "unknown", "message": "Domain unavailable; no domain-age inference was made.", "age_days": None, "creation_date": None}
    try:
        import whois
        result = whois.whois(domain)
        created = result.creation_date
        if isinstance(created, list):
            created = next((x for x in created if isinstance(x, datetime)), None)
        if not isinstance(created, datetime):
            return {"status": "unknown", "message": "WHOIS did not return a usable creation date.", "age_days": None, "creation_date": None}
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = max(0, (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).days)
        return {"status": "available", "message": f"WHOIS creation date: {created.date().isoformat()} ({age} days ago).", "age_days": age, "creation_date": created.date().isoformat()}
    except Exception as exc:
        return {"status": "unavailable", "message": f"Domain-age lookup unavailable ({type(exc).__name__}); missing data is not treated as suspicious.", "age_days": None, "creation_date": None}
