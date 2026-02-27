"""Wiederverwendbare Konstanten für das Alarm-Broker-Projekt."""

# Alarm Prioritäten
PRIORITY_CRITICAL = "P0"
PRIORITY_HIGH = "P1"
PRIORITY_MEDIUM = "P2"
PRIORITY_LOW = "P3"

PRIORITY_ALL = [PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW]

# Notification Tags
TAG_EMERGENCY = "notfall"
TAG_SILENT = "silent"

# API Default-Werte
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Default-Werte für Alarme
DEFAULT_SEVERITY = PRIORITY_CRITICAL

# Event-Typen
EVENT_ALARM_CREATED = "alarm.created"
EVENT_ALARM_ACKNOWLEDGED = "alarm.acknowledged"
EVENT_ALARM_RESOLVED = "alarm.resolved"
EVENT_ALARM_CANCELLED = "alarm.cancelled"
EVENT_ALARM_STATE_CHANGED = "alarm.state_changed"


# Notification Messages
def EMERGENCY_ALARM_TITLE(title: str) -> str:
    return f"NOTFALLALARM – {title}"


def ALARM_ACKNOWLEDGED_TITLE(title: str) -> str:
    return f"Alarm bestätigt: {title}"


def ALARM_RESOLVED_TITLE(title: str) -> str:
    return f"Alarm gelöst: {title}"


def ALARM_CANCELLED_TITLE(title: str) -> str:
    return f"Alarm storniert: {title}"
