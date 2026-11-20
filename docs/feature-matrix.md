# Feature matrix against the competitors

Status: `wired` = in the control or status path. `pure` = pure function with tests, not yet wired. `missing` = not built.
Sources: VT = Versatile Thermostat, RM = RoomMind, CA = ClimateAdvisor, PID = smart_thermostat.

| Feature | Source | Status |
| :-- | :-- | :-- |
| Cooling control via climate entity | all | wired |
| Window handling with delay | VT, RM, CA | wired |
| Presence handling | VT, RM, CA | wired (occupancy published) |
| Eco, boost, away presets | VT, RM, CA | wired |
| Learned room model (k, c, g) | RM, CA | wired |
| Time-to-setpoint and cost forecast | ours | wired |
| Observe-only mode | CA | wired |
| Built-in PID with trapezoidal integral and feedforward | PID | pure |
| Outdoor cooling limit | RM | wired |
| Minimum on and off times | VT | wired |
| Price-shifted target | RM | wired |
| Scheduled presets | RM, CA | wired (config field pending) |
| Heating failure detection | VT | wired (switch config) |
| Sensor stall detection | VT | wired |
| Safety output on stall | VT | wired |
| Day class | CA | wired |
| Daily briefing | CA | wired |
| Night plan | CA | wired |
| Learning suggestion | CA | wired |
| Vacation mode | RM | wired |
| Air quality ventilation (on-off and proportional) | RM | wired (status) |
| Humidity band and dry mode dehumidify | RM | wired |
| Mould risk and surface humidity | RM | wired |
| Solar gain | RM | wired (optional sensor) |
| Comfort band and compliance | CA | wired |
| Heatwave detection | CA | wired |
| Auto fan speed | VT | wired (status) |
| Drying decision | CA | wired (status) |
| Price fallback | RM | wired |
| Keep-alive | VT | pure |
| Valve exercise | RM | pure |
| Whole-house fan rule | CA | pure |
| Comfort-energy blend | RM | pure |
| Forecast day counts and heatwave streak | CA | pure |
| Cover action | RM | pure |
| Presence mode from several sources | RM | pure |
| Auto-tuner (relay test, tuning rules) | PID | missing |
| Heater output PWM and polarity | PID | missing |
| Presets as named climate modes | VT | missing |
| Services (what-if, reset learning, export) | VT, CA | missing |
| Diagnostics with redaction | VT, CA | missing |
| Accuracy page | CA | missing |
| Hardware: TRV valves, boiler, covers, whole-house fan, second AC, humidifier | VT, RM | missing |
