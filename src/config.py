import os
import yaml


def load_config(path: str = 'config.yml') -> dict:
    """Load config.yml and apply environment variable overrides for service hosts.

    Env vars take precedence so the same config.yml works across bare-metal dev,
    Docker Compose, and multi-cloud without modification:

        DATABASE_HOST    overrides database.host         (default: localhost)
        MINIO_ENDPOINT   overrides storage.endpoint_url  (default: http://localhost:9000)
        RABBITMQ_HOST    overrides rabbitmq.host          (default: localhost)
    """
    with open(path) as f:
        config = yaml.safe_load(f)

    db_host = os.getenv('DATABASE_HOST')
    if db_host:
        config.setdefault('database', {})['host'] = db_host

    minio_endpoint = os.getenv('MINIO_ENDPOINT')
    if minio_endpoint:
        config.setdefault('storage', {})['endpoint_url'] = minio_endpoint

    rmq_host = os.getenv('RABBITMQ_HOST')
    if rmq_host:
        config.setdefault('rabbitmq', {})['host'] = rmq_host

    return config
