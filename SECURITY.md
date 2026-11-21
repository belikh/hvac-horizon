# Security

## Report a problem

Use GitHub private vulnerability reporting on the repository: open
https://github.com/belikh/hvac-horizon/security/advisories/new, or choose **Security** then **Report a vulnerability**.
Do not open a public issue for a vulnerability. Include the version from `manifest.json`, the steps to reproduce and
what you saw. A diagnostics export is redacted (see below) and is safe to attach.

## The lock PIN

The lock stops HVAC Horizon from sending commands. The PIN unlocks it through the `set_lock` service.

- The PIN guards against accidental changes. It is not an access control.
- It is stored in the config entry in plain text, in `.storage/core.config_entries`. Anyone who can read that file can read the PIN.
- A PIN passed to `set_lock` from an automation sits in that automation's YAML and in its traces.
- Diagnostics redact the PIN and every configured entity id.
- The lock covers commands from HVAC Horizon only. It does not stop other automations, the Home Assistant UI, the device's own remote or direct control of the AC, heater or switches.
- With no PIN set, the lock cannot be turned on or off through the service.

## The optional AI investigator

The investigator is off until you pick a conversation agent in the Advanced setup section. It sends text to that agent with the
`conversation.process` service. The agent may run in the cloud, depending on the integration you chose. Calls are capped by
`ai_budget_per_day` (0 or empty allows none). The on-demand report has its own counter with the same limit.

Two kinds of call exist.

Mode change, sent when the AC mode changes, once per mode:

- The mode (`cool`, `off` or `heat`) and the reason text of the last decision.
- The median room temperature and the cool-above threshold, in degrees C.
- Whether anyone is home (as the sentence "Nobody is home, so the away setback applies.").
- Minutes a window has been open, when above 0.

Report, sent by the `generate_investigation_report` service:

- A fixed instruction asking for a 24-hour report.
- Number of readings and number of AC mode changes in the last 24 hours.
- Room temperature minimum, maximum and latest value, and outdoor minimum and maximum, in degrees C.
- The share of readings in cool or heat mode, and the time since the last mode change.
- Up to 50 mode changes, each as minutes ago and the mode.

The text holds no entity ids, device names, addresses, price data or the PIN. The conversation agent keeps whatever its own
integration keeps. The reply is stored in the status attributes `investigation_ai` and `investigation_report`.
