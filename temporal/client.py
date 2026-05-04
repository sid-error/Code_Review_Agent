"""
temporal/client.py — Temporal client factory.

Returns a connected AsyncClient pointed at localhost:7233.
The Temporal server (backed by Cassandra) is expected to be running via Docker:

    docker compose up -d

Once the containers are healthy (~60-90 s on first run), this client will
connect successfully.
"""

import asyncio
from temporalio.client import Client

TEMPORAL_HOST = "localhost:7233"
TASK_QUEUE = "code-review-queue"


async def get_client() -> Client:
    """Connect to the Temporal server and return a Client."""
    return await Client.connect(TEMPORAL_HOST)
