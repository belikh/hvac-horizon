from custom_components.hvac_horizon.fleet import COOL, ECONOMY, FAST, HEAT, Device, choose_devices, device_kw


def test_running_device_without_sensor_draws_its_rated_watts():
    assert device_kw(Device("ac", COOL, rated_w=900.0), None, running=True) == 0.9


def test_sensor_reading_wins_over_rated_watts():
    d = Device("ac", COOL, rated_w=900.0, power_sensor="sensor.ac_power")
    assert device_kw(d, 720.0, running=True) == 0.72


def test_idle_device_draws_nothing():
    assert device_kw(Device("ac", COOL, rated_w=900.0), 500.0, running=False) == 0.0


def test_economy_uses_the_small_unit_for_a_small_gap():
    small = Device("small", COOL, rate_k_per_h_per_kw=2.0, rated_w=900.0)
    big = Device("big", COOL, rate_k_per_h_per_kw=2.0, rated_w=3500.0)
    # 1 K over 1 h needs 1 K/h. The small unit supplies 1.8 K/h.
    assert choose_devices([small, big], COOL, gap_k=1.0, horizon_h=1.0, mode=ECONOMY, price_per_kwh=0.3) == ["small"]


def test_economy_adds_the_big_unit_when_the_small_one_cannot_close_the_gap():
    small = Device("small", COOL, rate_k_per_h_per_kw=2.0, rated_w=900.0)
    big = Device("big", COOL, rate_k_per_h_per_kw=2.0, rated_w=3500.0)
    # 5 K over 1 h needs 5 K/h. Small gives 1.8, big gives 7.0.
    assert choose_devices([small, big], COOL, gap_k=5.0, horizon_h=1.0, mode=ECONOMY, price_per_kwh=0.3) == ["small", "big"]


def test_fast_picks_the_highest_rate_first():
    small = Device("small", COOL, rate_k_per_h_per_kw=2.0, rated_w=900.0)
    big = Device("big", COOL, rate_k_per_h_per_kw=2.0, rated_w=3500.0)
    assert choose_devices([small, big], COOL, gap_k=1.0, horizon_h=1.0, mode=FAST, price_per_kwh=0.3)[0] == "big"


def test_no_gap_means_no_device():
    d = Device("ac", COOL, rate_k_per_h_per_kw=2.0, rated_w=900.0)
    assert choose_devices([d], COOL, gap_k=0.0, horizon_h=1.0, mode=FAST, price_per_kwh=0.3) == []


def test_device_without_calibration_or_watts_is_not_chosen():
    uncal = Device("uncal", COOL, rated_w=900.0)
    nowatts = Device("nowatts", COOL, rate_k_per_h_per_kw=2.0)
    assert choose_devices([uncal, nowatts], COOL, gap_k=1.0, horizon_h=1.0, mode=FAST, price_per_kwh=0.3) == []


def test_only_devices_of_the_asked_kind_are_chosen():
    ac = Device("ac", COOL, rate_k_per_h_per_kw=2.0, rated_w=900.0)
    ir = Device("ir", HEAT, rate_k_per_h_per_kw=1.5, rated_w=1500.0)
    assert choose_devices([ac, ir], HEAT, gap_k=1.0, horizon_h=1.0, mode=FAST, price_per_kwh=0.3) == ["ir"]
