"""Launch a one-shot worker run when a job is enqueued (the wake channel).

The hosted worker runs ``splitsmith worker --one-shot`` with restart
policy NEVER: it drains the queue and exits, so no idle process holds a
connection that keeps the Neon compute awake. Something must therefore
start it when work arrives. The API process is the only enqueuer, so
after a successful defer it fires the configured :class:`WorkerLauncher`.

The queue of record stays in Postgres (Procrastinate); a launcher is
only the wake signal and must be safe to lose - the serve-boot pending
re-check and the 6-hourly safety cron on the worker service are the
nets. The only implementation today is Railway (GraphQL
``serviceInstanceRedeploy``); the seam exists so non-Railway workers
(webhook/ntfy to a home machine, GitHub Actions) can be added without
touching the enqueue path.

Configuration comes entirely from env vars (:func:`build_worker_launcher`);
when they are absent the launcher is disabled and enqueue behaves exactly
as before, so local / docker-compose deployments never talk to Railway.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel

logger = logging.getLogger(__name__)

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"

# Selects the launcher implementation in build_worker_launcher();
# "railway" is the only one today.
ENV_WORKER_LAUNCHER = "SPLITSMITH_WORKER_LAUNCHER"
ENV_TRIGGER_TOKEN = "SPLITSMITH_WORKER_TRIGGER_TOKEN"
ENV_WORKER_SERVICE_ID = "SPLITSMITH_WORKER_SERVICE_ID"
ENV_WORKER_ENVIRONMENT_ID = "SPLITSMITH_WORKER_ENVIRONMENT_ID"
# Railway injects this into every deployed container; the SPLITSMITH_
# variable above exists only to override it outside Railway.
ENV_RAILWAY_ENVIRONMENT_ID = "RAILWAY_ENVIRONMENT_ID"


class RailwayLauncherConfig(BaseModel):
    """Railway coordinates + auth for starting a one-shot worker run."""

    token: str
    service_id: str
    environment_id: str
    api_url: str = RAILWAY_API_URL
    cooldown_seconds: float = 30.0


def load_railway_config() -> RailwayLauncherConfig | None:
    """Read the Railway launcher config from env vars; ``None`` disables it."""
    token = os.environ.get(ENV_TRIGGER_TOKEN, "").strip()
    service_id = os.environ.get(ENV_WORKER_SERVICE_ID, "").strip()
    environment_id = (
        os.environ.get(ENV_WORKER_ENVIRONMENT_ID, "").strip()
        or os.environ.get(ENV_RAILWAY_ENVIRONMENT_ID, "").strip()
    )
    if not (token and service_id and environment_id):
        return None
    return RailwayLauncherConfig(token=token, service_id=service_id, environment_id=environment_id)
