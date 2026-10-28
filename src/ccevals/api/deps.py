"""Request dependencies: database session, authenticated principal and role checks."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ccevals.db import new_session
from ccevals.security import Principal, authenticate


def get_session() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_principal(request: Request, authorization: str | None = Header(default=None),
                  session: Session = Depends(get_session)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer API key",
                            headers={"WWW-Authenticate": "Bearer"})
    principal = authenticate(session, authorization[7:].strip())
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key",
                            headers={"WWW-Authenticate": "Bearer"})
    limiter = request.app.state.rate_limiter
    if not limiter.allow(principal.key_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded", headers={"Retry-After": "5"})
    return principal


def require(*roles: str) -> Callable[[Principal], Principal]:
    def check(principal: Principal = Depends(get_principal)) -> Principal:
        if not any(principal.has(r) for r in roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires one of the roles: {', '.join(roles)}")
        return principal

    return check


def any_role(principal: Principal = Depends(get_principal)) -> Principal:
    return principal
