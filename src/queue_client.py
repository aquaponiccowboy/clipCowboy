import os
import pika
import json
import logging

TRANSCODE_QUEUE = 'footage.transcode'
ANALYZE_QUEUE   = 'footage.analyze'
TRANSCODE_DLQ   = 'footage.transcode.dlq'
ANALYZE_DLQ     = 'footage.analyze.dlq'


def get_channel(config: dict):
    mq = config.get('rabbitmq', {})
    credentials = pika.PlainCredentials(
        mq.get('user', 'guest'),
        mq.get('password', 'guest')
    )
    params = pika.ConnectionParameters(
        host=os.getenv('RABBITMQ_HOST') or mq.get('host', 'localhost'),
        port=int(mq.get('port', 5672)),
        credentials=credentials,
        heartbeat=600,
        blocked_connection_timeout=300
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    for queue in [TRANSCODE_QUEUE, ANALYZE_QUEUE, TRANSCODE_DLQ, ANALYZE_DLQ]:
        channel.queue_declare(queue=queue, durable=True)

    return connection, channel


def get_queue_depths(config: dict) -> dict:
    """Return message counts for all pipeline queues."""
    conn, ch = get_channel(config)
    try:
        depths = {}
        for queue in [TRANSCODE_QUEUE, ANALYZE_QUEUE, TRANSCODE_DLQ, ANALYZE_DLQ]:
            result = ch.queue_declare(queue=queue, passive=True)
            depths[queue] = result.method.message_count
        return depths
    finally:
        conn.close()


def publish(channel, queue: str, message: dict, headers: dict = None):
    channel.basic_publish(
        exchange='',
        routing_key=queue,
        body=json.dumps(message),
        properties=pika.BasicProperties(
            delivery_mode=2,  # persistent
            headers=headers or {}
        )
    )
    logging.debug(f"Published to {queue}: {message}")
