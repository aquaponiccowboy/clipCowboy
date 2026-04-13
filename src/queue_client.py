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
        host=mq.get('host', 'localhost'),
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
