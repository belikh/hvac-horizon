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

CONF_HUMIDITY = "humidity"
CONF_ENERGY = "energy"
STORE_VERSION = 2  # 2 adds setpoint, mode, rooms, humidity, price and energy fields

CONF_CONTROL = "control"  # False by default: observe only
CONF_WINDOW = "window"
CONF_PRESENCE = "presence"
CONF_AWAY_C = "away_c"
CONF_WINDOW_DELAY_MIN = "window_delay_min"
DEFAULT_AWAY_C = 27.0
DEFAULT_WINDOW_DELAY_MIN = 10.0
CONF_COMFORT_C = "comfort_c"
DEFAULT_COMFORT_C = 24.0
