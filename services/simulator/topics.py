"""MQTT konu adlarini tek yerden uretir"""

from config import get_settings

def _root() -> str:
    return get_settings().mqtt_topic_root

def room_telemetry(room_id: int) -> str:
    return f"{_root()}/telemetry/room/{room_id}"

def room_command(room_id: int) -> str:
    return f"{_root()}/cmd/room/{room_id}"

def room_command_filter() -> str :
    """Tun odalarin komutlarina abone olmak icin"""
    return f"{_root()}/cmd/room/+"

def environment_telemetry() -> str:
    return f"{_root()}/telemetry/environment"

def device_status(device_type: str, device_id: int | str) -> str :
    return f"{_root()}/status/{device_type}/{device_id}"