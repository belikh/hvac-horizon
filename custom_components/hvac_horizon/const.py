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
CONF_SCHEDULE = "schedule"  # optional entity; its next_event attribute is the pre-cool deadline
CONF_MIN_ON_MIN = "min_on_min"
CONF_MIN_OFF_MIN = "min_off_min"
CONF_CO2 = "co2"
CONF_PM25 = "pm25"
CONF_PRICE_CHEAP = "price_cheap"
CONF_PRICE_EXPENSIVE = "price_expensive"
DEFAULT_PRICE_CHEAP = 0.15
DEFAULT_PRICE_EXPENSIVE = 0.40
CONF_SCHEDULE_BLOCKS = "schedule_blocks"  # list of {start_h, end_h, preset}; empty means comfort all day
CONF_HEATER_SWITCH = "heater_switch"  # optional switch for the heater plug
CONF_FAN = "fan"  # optional switch for the room fan
CONF_DEHUMIDIFIER = "dehumidifier"  # optional switch for a dehumidifier
CONF_VACATION = "vacation"  # optional input_boolean or switch that enables vacation mode
CONF_VACATION_C = "vacation_c"
DEFAULT_VACATION_C = 16.0
