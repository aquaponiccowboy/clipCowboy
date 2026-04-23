import pymysql
import logging


def get_connection(config: dict):
    db = config.get('database', {})
    return pymysql.connect(
        host=db.get('host', 'localhost'),
        port=int(db.get('port', 3306)),
        user=db.get('user'),
        password=db.get('password'),
        database=db.get('name'),
        autocommit=True
    )


def ensure_schema(config: dict):
    model_version = config.get('model', {}).get('path', 'models/yolov8n.pt')
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_files (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    file_key      VARCHAR(512) NOT NULL,
                    model_version VARCHAR(256) NOT NULL DEFAULT '',
                    camera_id     CHAR(1),
                    has_objects   BOOLEAN NOT NULL,
                    disposition   ENUM('archived', 'quarantined') NOT NULL,
                    events        JSON,
                    processed_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_file_model (file_key, model_version)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dlq_files (
                    id        INT AUTO_INCREMENT PRIMARY KEY,
                    file_key  VARCHAR(512) NOT NULL UNIQUE,
                    queue     VARCHAR(64)  NOT NULL,
                    error     TEXT,
                    failed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration: model_version column
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'processed_files'
                  AND COLUMN_NAME  = 'model_version'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    "ALTER TABLE processed_files "
                    "ADD COLUMN model_version VARCHAR(256) NOT NULL DEFAULT %s",
                    (model_version,)
                )
                cursor.execute("ALTER TABLE processed_files DROP INDEX file_key")
                cursor.execute(
                    "ALTER TABLE processed_files "
                    "ADD UNIQUE KEY uq_file_model (file_key, model_version)"
                )
                logging.info("Migrated processed_files: added model_version column.")

            # Migration: sort_prefix column (NULL = unsorted, set by sort_archive.py)
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'processed_files'
                  AND COLUMN_NAME  = 'sort_prefix'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute(
                    "ALTER TABLE processed_files "
                    "ADD COLUMN sort_prefix VARCHAR(128) DEFAULT NULL"
                )
                logging.info("Migrated processed_files: added sort_prefix column.")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gallery (
                    id          INT AUTO_INCREMENT PRIMARY KEY,
                    name        VARCHAR(128)  NOT NULL,
                    category    VARCHAR(64)   NOT NULL,
                    embedding   MEDIUMBLOB    NOT NULL,
                    embed_model VARCHAR(128)  NOT NULL,
                    source_file VARCHAR(512),
                    enrolled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_gallery_cat (category)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS clip_scores (
                    id                INT AUTO_INCREMENT PRIMARY KEY,
                    file_key          VARCHAR(512) NOT NULL,
                    model_version     VARCHAR(256) NOT NULL,
                    score             FLOAT        NOT NULL,
                    n_events          INT          NOT NULL,
                    duration_sec      FLOAT,
                    detection_rate    FLOAT,
                    avg_objects       FLOAT,
                    label_diversity   INT,
                    mean_confidence   FLOAT,
                    temporal_coverage FLOAT,
                    scored_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_score (file_key, model_version)
                )
            """)

        logging.info("Database schema verified.")
    finally:
        conn.close()
