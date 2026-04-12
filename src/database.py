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
    conn = get_connection(config)
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_files (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    file_key      VARCHAR(512) NOT NULL UNIQUE,
                    camera_id     CHAR(1),
                    has_motion    BOOLEAN NOT NULL,
                    events        JSON,
                    processed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
        logging.info("Database schema verified.")
    finally:
        conn.close()
