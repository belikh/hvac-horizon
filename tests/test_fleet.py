from custom_components.hvac_horizon.devices import RoomSample
from custom_components.hvac_horizon.fleet import COOL, ECONOMY, FAST, HEAT, Device, calibrate_rates, choose_devices, device_kw


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


def test_fast_orders_by_the_rate_the_device_delivers_not_by_rate_alone():
    # The small unit has the higher rate per kW but delivers 2.7 K/h. The big unit delivers 7.0 K/h.
    small = Device("small", COOL, rate_k_per_h_per_kw=3.0, rated_w=900.0)
    big = Device("big", COOL, rate_k_per_h_per_kw=2.0, rated_w=3500.0)
    assert choose_devices([small, big], COOL, gap_k=1.0, horizon_h=1.0, mode=FAST, price_per_kwh=0.3) == ["big"]


def test_economy_orders_by_cost_per_kelvin_and_differs_from_fast():
    small = Device("small", COOL, rate_k_per_h_per_kw=3.0, rated_w=900.0)
    big = Device("big", COOL, rate_k_per_h_per_kw=2.0, rated_w=3500.0)
    # Cost per K changed is price / rate: 0.1 for the small unit, 0.15 for the big one.
    assert choose_devices([small, big], COOL, gap_k=1.0, horizon_h=1.0, mode=ECONOMY, price_per_kwh=0.3) == ["small"]


def _simulate(rate: float, power_kw: float, steps: int = 80) -> list[RoomSample]:
    """Room with k=0.1 /h and g=0.3 K/h. The cooler runs in blocks of 10 intervals, a heater in other blocks."""
    k, g, temp = 0.1, 0.3, 27.0
    out = []
    for i in range(steps):
        outdoor = 30.0 + 4.0 * ((i % 40) / 40.0)
        cool_on = (i // 10) % 3 == 1
        heat_on = (i // 10) % 3 == 2 and i >= 40
        cool_kw = power_kw if cool_on else 0.0
        heat_kw = 1.5 if heat_on else 0.0
        nxt = temp + (5.0 / 60.0) * (k * (outdoor - temp) + g - rate * cool_kw + 2.0 * heat_kw)
        out.append(RoomSample(5.0, temp, nxt, outdoor, {"cool": cool_kw, "heat": heat_kw}))
        temp = nxt
    return out


def test_calibrate_recovers_each_device_rate_from_intervals_where_it_runs_alone():
    devices = [Device("cool", COOL, rated_w=900.0), Device("heat", HEAT, rated_w=1500.0)]
    fitted = calibrate_rates(_simulate(3.0, 0.9), devices)
    assert abs(fitted["cool"]["rate"] - 3.0) < 0.05
    assert abs(fitted["heat"]["rate"] - 2.0) < 0.05


def test_calibrate_leaves_a_device_that_ran_too_little_without_a_rate():
    devices = [Device("cool", COOL, rated_w=900.0), Device("never", COOL, rated_w=3500.0)]
    fitted = calibrate_rates(_simulate(3.0, 0.9), devices)
    assert "cool" in fitted
    assert "never" not in fitted


def test_calibrate_ignores_intervals_where_two_devices_ran_together():
    devices = [Device("cool", COOL, rated_w=900.0), Device("other", COOL, rated_w=900.0)]
    samples = [RoomSample(5.0, 25.0, 25.0, 30.0 + (i % 5), {"cool": 0.9, "other": 0.9}) for i in range(40)]
    assert calibrate_rates(samples, devices) == {}
