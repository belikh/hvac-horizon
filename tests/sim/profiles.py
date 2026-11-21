"""The simulated rooms. Each runs 24 hours with control on."""

from __future__ import annotations

from dataclasses import replace

from tests.sim.harness import Profile
from tests.sim.house import AcSpec, Envelope, Weather

AC = "climate.sim_ac"
SMALL = "climate.sim_small"
LARGE = "climate.sim_large"

TIMBER = Envelope(mass_j_per_k=1.5e6, air_to_outdoor_w_per_k=50.0, mass_to_outdoor_w_per_k=50.0)
MASONRY = Envelope(mass_j_per_k=1.2e7, air_to_outdoor_w_per_k=30.0, mass_to_outdoor_w_per_k=40.0, air_to_mass_w_per_k=300.0)
HOT = Weather(mean_c=30.5, amplitude_c=7.5, noise_c=0.3)  # 23 to 38 C
WARM = Weather(mean_c=30.0, amplitude_c=5.0, noise_c=0.3)
MILD = Weather(mean_c=26.0, amplitude_c=4.0, noise_c=0.3)  # 22 to 30 C
COOL_NIGHT = Weather(mean_c=13.0, amplitude_c=8.0, noise_c=0.3)  # 5 C at 03:00, 21 C at 15:00

NORMAL_AC = AcSpec(cool_kw=3.5)

PROFILES: dict[str, Profile] = {
    p.name: p
    for p in [
        Profile("light_timber", TIMBER, WARM, ((AC, NORMAL_AC),)),
        Profile("heavy_masonry", MASONRY, WARM, ((AC, NORMAL_AC),)),
        Profile("oversized_ac", TIMBER, WARM, ((AC, AcSpec(cool_kw=5.5)),)),  # about 3 times the peak load
        Profile("grossly_oversized_ac", TIMBER, WARM, ((AC, AcSpec(cool_kw=8.0)),)),  # about 4 times the peak load
        Profile("undersized_ac", TIMBER, HOT, ((AC, AcSpec(cool_kw=1.2)),)),
        Profile("hot_day", TIMBER, HOT, ((AC, AcSpec(cool_kw=4.0)),)),
        Profile("mild_day", TIMBER, MILD, ((AC, NORMAL_AC),)),
        Profile("cold_night_heater", TIMBER, COOL_NIGHT, ((AC, NORMAL_AC),), heater_kw=2.5, start_hour=18.0, start_air_c=21.0, start_mass_c=21.0),
        Profile(
            "two_ac_fleet", TIMBER, HOT,
            ((SMALL, AcSpec(cool_kw=1.6)), (LARGE, AcSpec(cool_kw=3.6))),
            fleet=(
                {"device_id": "small", "kind": "cool", "entity_id": SMALL, "rate_k_per_h_per_kw": 4.0, "rated_w": 600.0, "power_sensor": "sensor.sim_small_power"},
                {"device_id": "large", "kind": "cool", "entity_id": LARGE, "rate_k_per_h_per_kw": 4.0, "rated_w": 1300.0, "power_sensor": "sensor.sim_large_power"},
            ),
        ),
    ]
}
