from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import text
from ulid import ULID

from .app_handlers import get_latest_ride_status
from .middlewares import chair_auth_middleware
from .models import Chair, ChairLocation, Owner, Ride, RideStatus, User
from .sql import engine
from .utils import secure_random_str, timestamp_millis

router = APIRouter(prefix="/api/chair")


class ChairPostChairsRequest(BaseModel):
    name: str
    model: str
    chair_register_token: str


class ChairPostChairsResponse(BaseModel):
    id: str
    owner_id: str


@router.post("/chairs", status_code=HTTPStatus.CREATED)
def chair_post_chairs(
    req: ChairPostChairsRequest, resp: Response
) -> ChairPostChairsResponse:
    if req.name == "" or req.model == "" or req.chair_register_token == "":
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail="some of required fields(name, model, chair_register_token) are empty",
        )

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM owners WHERE chair_register_token = :chair_register_token"
            ),
            {"chair_register_token": req.chair_register_token},
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=HTTPStatus.UNAUTHORIZED,
                detail="invalid chair_register_token",
            )
        owner = Owner.model_validate(row)

    chair_id = str(ULID())
    access_token = secure_random_str(32)

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chairs (id, owner_id, name, model, is_active, access_token) VALUES (:id, :owner_id, :name, :model, :is_active, :access_token)",
            ),
            {
                "id": chair_id,
                "owner_id": owner.id,
                "name": req.name,
                "model": req.model,
                "is_active": False,
                "access_token": access_token,
            },
        )

    resp.set_cookie(path="/", key="chair_session", value=access_token)
    return ChairPostChairsResponse(id=chair_id, owner_id=owner.id)


class PostChairActivityRequest(BaseModel):
    is_active: bool


@router.post("/activity", status_code=HTTPStatus.NO_CONTENT)
def chair_post_activity(
    chair: Annotated[Chair, Depends(chair_auth_middleware)],
    req: PostChairActivityRequest,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE chairs SET is_active = :is_active WHERE id = :id"),
            {"is_active": req.is_active, "id": chair.id},
        )


class Coordinate(BaseModel):
    latitude: int
    longitude: int


class ChairPostCoordinateResponse(BaseModel):
    recorded_at: int


@router.post("/coordinate")
def chair_post_coordinate(
    chair: Annotated[Chair, Depends(chair_auth_middleware)],
    req: Coordinate,
) -> ChairPostCoordinateResponse:
    with engine.begin() as conn:
        chair_location_id = str(ULID())
        conn.execute(
            text(
                "INSERT INTO chair_locations (id, chair_id, latitude, longitude) VALUES (:id, :chair_id, :latitude, :longitude)"
            ),
            {
                "id": chair_location_id,
                "chair_id": chair.id,
                "latitude": req.latitude,
                "longitude": req.longitude,
            },
        )
        # 累積移動距離と最後の座標を 1 文で更新する。
        # 前の座標を別の SELECT で読むと、同じ椅子に 2 つ同時に届いたときに差分を取りこぼす。
        conn.execute(
            text(
                "UPDATE chairs SET total_distance = total_distance + IFNULL(ABS(:latitude - last_latitude) + ABS(:longitude - last_longitude), 0), last_latitude = :latitude, last_longitude = :longitude WHERE id = :id"
            ),
            {"latitude": req.latitude, "longitude": req.longitude, "id": chair.id},
        )

        row = conn.execute(
            text("SELECT * FROM chair_locations WHERE id = :id"),
            {"id": chair_location_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
        location = ChairLocation.model_validate(row)

        row = conn.execute(
            text(
                "SELECT * FROM rides WHERE chair_id = :chair_id ORDER BY updated_at DESC LIMIT 1"
            ),
            {"chair_id": chair.id},
        ).fetchone()
        if row is not None:
            ride = Ride.model_validate(row)
            ride_status = get_latest_ride_status(conn, ride_id=ride.id)
            if ride_status != "COMPLETED" and ride_status != "CANCELLED":
                if (
                    req.latitude == ride.pickup_latitude
                    and req.longitude == ride.pickup_longitude
                    and ride_status == "ENROUTE"
                ):
                    conn.execute(
                        text(
                            "INSERT INTO ride_statuses (id, ride_id, status) VALUES (:id, :ride_id, :status)"
                        ),
                        {"id": str(ULID()), "ride_id": ride.id, "status": "PICKUP"},
                    )

                if (
                    req.latitude == ride.destination_latitude
                    and req.longitude == ride.destination_longitude
                    and ride_status == "CARRYING"
                ):
                    conn.execute(
                        text(
                            "INSERT INTO ride_statuses (id, ride_id, status) VALUES (:id, :ride_id, :status) "
                        ),
                        {"id": str(ULID()), "ride_id": ride.id, "status": "ARRIVED"},
                    )

    return ChairPostCoordinateResponse(
        recorded_at=timestamp_millis(location.created_at)
    )


class SimpleUser(BaseModel):
    id: str
    name: str


class ChairGetNotificationResponseData(BaseModel):
    ride_id: str
    user: SimpleUser
    pickup_coordinate: Coordinate
    destination_coordinate: Coordinate
    status: str


class ChairGetNotificationResponse(BaseModel):
    data: ChairGetNotificationResponseData | None = None
    retry_after_ms: int | None = None


# 0190: 椅子の通知で使うユーザーの表示名（実行中は変わらない）
_chair_notification_users: dict[str, SimpleUser] = {}


@router.get("/notification", response_model_exclude_none=True)
def chair_get_notification(
    chair: Annotated[Chair, Depends(chair_auth_middleware)],
) -> ChairGetNotificationResponse:
    with engine.connect() as conn:
        ride_status = ""
        row = conn.execute(
            text(
                "SELECT * FROM rides WHERE chair_id = :chair_id ORDER BY updated_at DESC LIMIT 1"
            ),
            {"chair_id": chair.id},
        ).fetchone()

        if row is None:
            return ChairGetNotificationResponse(data=None, retry_after_ms=30)

        ride = Ride.model_validate(row)
        yet_sent_ride_status: RideStatus | None = None
        # 「まだ椅子に送っていない状態」と「最新の状態」を 1 往復で取る
        rows = conn.execute(
            text(
                "(SELECT 0 AS kind, ride_statuses.* FROM ride_statuses"
                " WHERE ride_id = :ride_id AND chair_sent_at IS NULL ORDER BY created_at ASC LIMIT 1)"
                " UNION ALL "
                "(SELECT 1 AS kind, ride_statuses.* FROM ride_statuses"
                " WHERE ride_id = :ride_id ORDER BY created_at DESC LIMIT 1)"
            ),
            {"ride_id": ride.id},
        ).fetchall()
        if not rows:
            raise HTTPException(
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="no rows in result set",
            )
        unsent = [r for r in rows if r.kind == 0]
        if unsent:
            yet_sent_ride_status = RideStatus.model_validate(unsent[0])
            ride_status = yet_sent_ride_status.status
        else:
            ride_status = [r for r in rows if r.kind == 1][0].status

        # 0190: ユーザーの表示名は実行中に変わらないので、プロセス内に持つ。
        # 通知のたびに FOR SHARE（共有ロック）で 1 行読むのをやめる
        user = _chair_notification_users.get(ride.user_id)
        if user is None:
            row = conn.execute(
                text("SELECT id, firstname, lastname FROM users WHERE id = :id"),
                {"id": ride.user_id},
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
            user = SimpleUser(id=row.id, name=f"{row.firstname} {row.lastname}")
            _chair_notification_users[ride.user_id] = user

        if yet_sent_ride_status:
            conn.execute(
                text(
                    "UPDATE ride_statuses SET chair_sent_at = CURRENT_TIMESTAMP(6) WHERE id = :id"
                ),
                {"id": yet_sent_ride_status.id},
            )
            # 書いたときだけ COMMIT する
            conn.commit()

    return ChairGetNotificationResponse(
        data=ChairGetNotificationResponseData(
            ride_id=ride.id,
            user=user,
            pickup_coordinate=Coordinate(
                latitude=ride.pickup_latitude, longitude=ride.pickup_longitude
            ),
            destination_coordinate=Coordinate(
                latitude=ride.destination_latitude, longitude=ride.destination_longitude
            ),
            status=ride_status,
        ),
        retry_after_ms=30,
    )


class PostChairRidesRideIDStatusRequest(BaseModel):
    status: str


@router.post("/rides/{ride_id}/status", status_code=HTTPStatus.NO_CONTENT)
def chair_post_ride_status(
    chair: Annotated[Chair, Depends(chair_auth_middleware)],
    ride_id: str,
    req: PostChairRidesRideIDStatusRequest,
) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM rides WHERE id = :id FOR UPDATE"), {"id": ride_id}
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=HTTPStatus.NOT_FOUND, detail="ride not found"
            )
        ride = Ride.model_validate(row)

        if ride.chair_id != chair.id:
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST, detail="not assigned to this ride"
            )

        match req.status:
            # Acknowledge the ride
            case "ENROUTE":
                conn.execute(
                    text(
                        "INSERT INTO ride_statuses (id, ride_id, status) VALUES (:id, :ride_id, :status)"
                    ),
                    {"id": str(ULID()), "ride_id": ride.id, "status": "ENROUTE"},
                )
            # After Picking up user
            case "CARRYING":
                ride_status = get_latest_ride_status(conn, ride.id)
                if ride_status != "PICKUP":
                    raise HTTPException(
                        status_code=HTTPStatus.BAD_REQUEST,
                        detail="chair has not arrived yet",
                    )
                conn.execute(
                    text(
                        "INSERT INTO ride_statuses (id, ride_id, status) VALUES (:id, :ride_id, :status)"
                    ),
                    {"id": str(ULID()), "ride_id": ride.id, "status": "CARRYING"},
                )
            case _:
                raise HTTPException(
                    status_code=HTTPStatus.BAD_REQUEST, detail="invalid status"
                )
