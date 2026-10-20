from http import HTTPStatus
from typing import Annotated

from fastapi import Cookie, HTTPException
from sqlalchemy import text

from .models import Chair, Owner, User
from .sql import engine

# 0120: 認証の引き当てをプロセス内に持つ。
# access_token は一度発行されたら変わらないので、同じトークンなら同じ行。
# gunicorn は worker ごとなので、キャッシュも worker ごと（プロセスをまたがない）
_app_sessions: dict[str, User] = {}
_owner_sessions: dict[str, Owner] = {}
_chair_sessions: dict[str, Chair] = {}


def app_auth_middleware(app_session: Annotated[str | None, Cookie()] = None) -> User:
    if not app_session:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="app_session cookie is required"
        )

    cached = _app_sessions.get(app_session)
    if cached is not None:
        return cached

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM users WHERE access_token = :access_token"),
            {"access_token": app_session},
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="invalid access token"
        )
    user = User.model_validate(row)
    _app_sessions[app_session] = user
    return user


def owner_auth_middleware(
    owner_session: Annotated[str | None, Cookie()] = None,
) -> Owner:
    if not owner_session:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="owner_session cookie is required",
        )

    cached = _owner_sessions.get(owner_session)
    if cached is not None:
        return cached

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM owners WHERE access_token = :access_token"),
            {"access_token": owner_session},
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="invalid access token"
        )
    owner = Owner.model_validate(row)
    _owner_sessions[owner_session] = owner
    return owner


def chair_auth_middleware(
    chair_session: Annotated[str | None, Cookie()] = None,
) -> Chair:
    if not chair_session:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="chair_session cookie is required",
        )

    cached = _chair_sessions.get(chair_session)
    if cached is not None:
        return cached

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM chairs WHERE access_token = :access_token"),
            {"access_token": chair_session},
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED, detail="invalid access token"
        )
    chair = Chair.model_validate(row)
    _chair_sessions[chair_session] = chair
    return chair
