SET CHARACTER_SET_CLIENT = utf8mb4;
SET CHARACTER_SET_CONNECTION = utf8mb4;

USE isuride;

CREATE INDEX idx_ride_statuses_ride_id_created_at
  ON ride_statuses (ride_id, created_at);

CREATE INDEX idx_ride_statuses_ride_app_sent
  ON ride_statuses (ride_id, app_sent_at, created_at);
CREATE INDEX idx_ride_statuses_ride_chair_sent
  ON ride_statuses (ride_id, chair_sent_at, created_at);
CREATE INDEX idx_rides_user_created
  ON rides (user_id, created_at);
CREATE INDEX idx_rides_chair_updated
  ON rides (chair_id, updated_at);
CREATE INDEX idx_chair_locations_chair_created
  ON chair_locations (chair_id, created_at);
CREATE INDEX idx_coupons_used_by
  ON coupons (used_by);
CREATE INDEX idx_chairs_owner
  ON chairs (owner_id);
CREATE INDEX idx_chairs_is_active
  ON chairs (is_active);

-- 0140 の検証で見つけた 1 本。椅子の認証（chair_auth_middleware）が
-- SELECT * FROM chairs WHERE access_token = ? を毎回投げるため
CREATE UNIQUE INDEX idx_chairs_access_token
  ON chairs (access_token);

-- 0070: 椅子ごとの累積移動距離と、最後に報告された座標。
-- owner/chairs の窓関数（chair_locations 全体に LAG）をやめるため。
-- 必ず索引を張った「後」に置く（先に置くと窓関数が索引を使えず initialize が 20 秒かかる）
ALTER TABLE chairs ADD COLUMN total_distance INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chairs ADD COLUMN last_latitude INTEGER NULL;
ALTER TABLE chairs ADD COLUMN last_longitude INTEGER NULL;

-- 0070: 初期データぶんを 1 回だけ積む（以降は chair_post_coordinate が足していく）
UPDATE chairs c
LEFT JOIN (
    SELECT chair_id, SUM(IFNULL(distance, 0)) AS d
    FROM (
        SELECT chair_id,
               ABS(latitude - LAG(latitude) OVER (PARTITION BY chair_id ORDER BY created_at)) +
               ABS(longitude - LAG(longitude) OVER (PARTITION BY chair_id ORDER BY created_at)) AS distance
        FROM chair_locations
    ) t
    GROUP BY chair_id
) s ON s.chair_id = c.id
SET c.total_distance = IFNULL(s.d, 0),
    c.last_latitude  = (SELECT cl.latitude  FROM chair_locations cl
                         WHERE cl.chair_id = c.id ORDER BY cl.created_at DESC LIMIT 1),
    c.last_longitude = (SELECT cl.longitude FROM chair_locations cl
                         WHERE cl.chair_id = c.id ORDER BY cl.created_at DESC LIMIT 1);

-- 0120: 椅子の成績（完了数と評価の合計）を列で持つ
ALTER TABLE chairs ADD COLUMN completed_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chairs ADD COLUMN evaluation_sum INTEGER NOT NULL DEFAULT 0;

UPDATE chairs c
LEFT JOIN (
    SELECT rides.chair_id, COUNT(*) AS cnt, SUM(rides.evaluation) AS evalsum
    FROM rides
    WHERE rides.chair_id IS NOT NULL
      AND rides.evaluation IS NOT NULL
      AND (SELECT COUNT(*) FROM ride_statuses
           WHERE ride_statuses.ride_id = rides.id
             AND ride_statuses.status IN ('ARRIVED', 'CARRYING', 'COMPLETED')) = 3
    GROUP BY rides.chair_id
) s ON s.chair_id = c.id
SET c.completed_count = IFNULL(s.cnt, 0),
    c.evaluation_sum = IFNULL(s.evalsum, 0);

-- 0180: nearby-chairs の「今ライド中の椅子」を索引で引く
ALTER TABLE rides ADD INDEX idx_rides_evaluation_chair (evaluation, chair_id);
