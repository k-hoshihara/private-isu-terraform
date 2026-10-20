from collections import defaultdict
from collections.abc import MutableMapping
from datetime import datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text
from ulid import ULID

from .middlewares import owner_auth_middleware
from .models import Chair, Owner, Ride
from .sql import engine
from .utils import (
    datetime_fromtimestamp_millis,
    secure_random_str,
    sum_sales,
    timestamp_millis,
)

router = APIRouter(prefix="/api/owner")


class OwnerPostOwnersRequest(BaseModel):
    name: str


class OwnerPostOwnersResponse(BaseModel):
    id: str
    chair_register_token: str


@router.post("/owners", status_code=HTTPStatus.CREATED)
def owner_post_owners(
    req: OwnerPostOwnersRequest, response: Response
) -> OwnerPostOwnersResponse:
    if req.name == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="some of required fields(name) are empty",
        )

    owner_id = str(ULID())
    access_token = secure_random_str(32)
    chair_register_token = secure_random_str(32)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO owners (id, name, access_token, chair_register_token) VALUES (:id, :name, :access_token, :chair_register_token)"
            ),
            {
                "id": owner_id,
                "name": req.name,
                "access_token": access_token,
                "chair_register_token": chair_register_token,
            },
        )

    response.set_cookie(path="/", key="owner_session", value=access_token)

    return OwnerPostOwnersResponse(
        id=owner_id, chair_register_token=chair_register_token
    )


class ChairSales(BaseModel):
    id: str
    name: str
    sales: int


class ModelSales(BaseModel):
    model: str
    sales: int


class OwnerGetSalesResponse(BaseModel):
    total_sales: int
    chairs: list[ChairSales]
    models: list[ModelSales]


@router.get("/sales")
def owner_get_sales(
    owner: Annotated[Owner, Depends(owner_auth_middleware)],
    since: int | None = None,
    until: int | None = None,
) -> OwnerGetSalesResponse:
    if since is None:
        since_dt = datetime_fromtimestamp_millis(0)
    else:
        since_dt = datetime_fromtimestamp_millis(since)

    if until is None:
        until_dt = datetime(9999, 12, 31, 23, 59, 59)
    else:
        until_dt = datetime_fromtimestamp_millis(until)

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT * FROM chairs WHERE owner_id = :owner_id"),
            {"owner_id": owner.id},
        ).fetchall()
        chairs = [Chair.model_validate(r) for r in rows]

        # 0240: 椅子ごとに 1 クエリ打たず、owner の椅子ぶんをまとめて 1 回で取る
        rows = conn.execute(
            text(
                "SELECT rides.* FROM rides JOIN ride_statuses ON rides.id = ride_statuses.ride_id WHERE chair_id IN (SELECT id FROM chairs WHERE owner_id = :owner_id) AND status = 'COMPLETED' AND updated_at BETWEEN :since AND :until + INTERVAL 999 MICROSECOND"
            ),
            {"owner_id": owner.id, "since": since_dt, "until": until_dt},
        ).fetchall()
        rides_by_chair: MutableMapping[str, list[Ride]] = defaultdict(list)
        for r in rows:
            rides_by_chair[r.chair_id].append(Ride.model_validate(r))

        res = OwnerGetSalesResponse(total_sales=0, chairs=[], models=[])
        model_sales_by_model: MutableMapping[str, int] = defaultdict(int)
        for chair in chairs:
            chair_sales = sum_sales(rides_by_chair.get(chair.id, []))
            res.total_sales += chair_sales
            res.chairs.append(
                ChairSales(id=chair.id, name=chair.name, sales=chair_sales)
            )
            model_sales_by_model[chair.model] += chair_sales

        model_sales = []
        for model, sales in model_sales_by_model.items():
            model_sales.append(ModelSales(model=model, sales=sales))

        res.models = model_sales

        return res


class ChairWithDetail(BaseModel):
    id: str
    owner_id: str
    name: str
    access_token: str
    model: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    total_distance: int
    total_distance_updated_at: datetime | None = None


class OwnerGetChairResponseChair(BaseModel):
    id: str
    name: str
    model: str
    active: bool
    registered_at: int
    total_distance: int
    total_distance_updated_at: int | None = None


class OwnerGetChairResponse(BaseModel):
    chairs: list[OwnerGetChairResponseChair]


@router.get(
    "/chairs",
    status_code=HTTPStatus.OK,
    response_model_exclude_none=True,
)
def owner_get_chairs(
    owner: Annotated[Owner, Depends(owner_auth_middleware)],
) -> OwnerGetChairResponse:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id,
                       owner_id,
                       name,
                       access_token,
                       model,
                       is_active,
                       created_at,
                       updated_at,
                       total_distance,
                       (SELECT MAX(cl.created_at)
                        FROM chair_locations cl
                        WHERE cl.chair_id = chairs.id) AS total_distance_updated_at
                FROM chairs
                WHERE owner_id = :owner_id
        """
            ),
            {"owner_id": owner.id},
        )
        chairs = [ChairWithDetail.model_validate(r) for r in rows.mappings()]

    res = OwnerGetChairResponse(chairs=[])
    for chair in chairs:
        c = OwnerGetChairResponseChair(
            id=chair.id,
            name=chair.name,
            model=chair.model,
            active=chair.is_active,
            registered_at=timestamp_millis(chair.created_at),
            total_distance=chair.total_distance,
            total_distance_updated_at=None,
        )
        if chair.total_distance_updated_at is not None:
            t = timestamp_millis(chair.total_distance_updated_at)
            c.total_distance_updated_at = t
        res.chairs.append(c)

    return res
