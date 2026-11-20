"""Constants for HVAC Horizon."""

DOMAIN = "hvac_horizon"

CONF_CLIMATE = "climate"
CONF_POWER = "power"
CONF_ROOM_SENSORS = "room_sensors"
CONF_OUTDOOR = "outdoor"
CONF_PRICE = "price"

SAMPLE_INTERVAL_S = 300
KEEP_DAYS = 30
MAX_ENTRIES = KEEP_DAYS * 24 * 60 * 60 // SAMPLE_INTERVAL_S  # 8640 readings
FIT_EVERY_N_SAMPLES = 12  # refit about hourly
