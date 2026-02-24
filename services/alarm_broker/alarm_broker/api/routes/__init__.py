from __future__ import annotations

from alarm_broker.api.routes.ack import router as ack_router
from alarm_broker.api.routes.admin import router as admin_router
from alarm_broker.api.routes.alarms import router as alarms_router
from alarm_broker.api.routes.health import router as health_router
from alarm_broker.api.routes.yealink import router as yealink_router

ALL_ROUTERS = [
    health_router,
    yealink_router,
    ack_router,
    alarms_router,
    admin_router,
]
