import os

import psycopg
from psycopg.rows import dict_row
from redis import Redis

def database():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

def queue():
    return Redis.from_url(os.environ["REDIS_URL"], socket_timeout=7)

def notify():
    from redis.exceptions import RedisError
    try:
        queue().lpush("provision", "work")
        queue().ltrim("provision", 0, 99)
    except RedisError:
        # PostgreSQL holds the durable queue. The worker also polls it.
        pass
