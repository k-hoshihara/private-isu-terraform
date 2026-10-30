from http import HTTPStatus

from fastapi import APIRouter
from sqlalchemy import text

from .models import Chair, Ride
from .sql import engine

router = APIRouter(prefix="/api/internal")


# このAPIをインスタンス内から一定間隔で叩かせることで、椅子とライドをマッチングさせる
@router.get("/matching", status_code=HTTPStatus.NO_CONTENT)
def internal_get_matching() -> None:
    # 1 リクエストで最大 10 件さばく（マッチャは 0.05 秒ごとに叩く）
    for _ in range(10):
        if not match_one():
            return


def match_one() -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM rides WHERE chair_id IS NULL ORDER BY created_at LIMIT 1")
        ).fetchone()
        if row is None:
            return False
        ride = Ride.model_validate(row)

        # 「空いている椅子のうち、乗車位置にいちばん近い 1 台」を 1 クエリで取る。
        # 空いている = その椅子のやりかけのライド（chair_sent_at が 6 個そろっていない）が無い。
        # 座標は chairs に持たせてある（0070）ので chair_locations を引きに行かない。
        row = conn.execute(
            text(
                """
                SELECT * FROM chairs
                WHERE is_active = TRUE
                  AND last_latitude IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM rides
                    WHERE rides.chair_id = chairs.id
                      AND (SELECT COUNT(chair_sent_at)
                           FROM ride_statuses
                           WHERE ride_id = rides.id) <> 6)
                ORDER BY ABS(last_latitude - :pickup_latitude)
                       + ABS(last_longitude - :pickup_longitude)
                LIMIT 1
                """
            ),
            {
                "pickup_latitude": ride.pickup_latitude,
                "pickup_longitude": ride.pickup_longitude,
            },
        ).fetchone()
        if row is None:
            return False
        chair = Chair.model_validate(row)

        conn.execute(
            text("UPDATE rides SET chair_id = :chair_id WHERE id = :id"),
            {"chair_id": chair.id, "id": ride.id},
        )
    return True
