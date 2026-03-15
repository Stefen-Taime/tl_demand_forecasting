CREATE TABLE IF NOT EXISTS zone_predictions (
    id               BIGSERIAL PRIMARY KEY,
    target_hour      TIMESTAMP NOT NULL,
    generated_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    zone_id          INTEGER NOT NULL,
    zone_name        VARCHAR(100) NOT NULL,
    borough          VARCHAR(50),
    latitude         DOUBLE PRECISION,
    longitude        DOUBLE PRECISION,
    predicted_trips  DOUBLE PRECISION NOT NULL,
    actual_trips     DOUBLE PRECISION,
    absolute_error   DOUBLE PRECISION,
    model_name       VARCHAR(100) NOT NULL,
    model_version    VARCHAR(50) NOT NULL,
    model_alias      VARCHAR(50) NOT NULL DEFAULT 'champion',
    replay_mode      BOOLEAN NOT NULL DEFAULT TRUE,
    status           VARCHAR(20) NOT NULL DEFAULT 'predicted',
    created_at       TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (target_hour, zone_id, model_alias)
);

CREATE INDEX IF NOT EXISTS idx_zone_predictions_hour
  ON zone_predictions(target_hour DESC);

CREATE INDEX IF NOT EXISTS idx_zone_predictions_zone
  ON zone_predictions(zone_id);

CREATE TABLE IF NOT EXISTS replay_state (
    id           SMALLINT PRIMARY KEY,
    current_hour TIMESTAMP NOT NULL,
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
