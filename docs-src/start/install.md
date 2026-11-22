---
title: Install
summary: Install HVAC Horizon with HACS, add a room, and turn on control.
section: start
order: 10
audience: users
status: draft
---

## Install with HACS

[![Add to Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=belikh&repository=hvac-horizon&category=integration)

1. Select the button above. Home Assistant opens the repository in HACS.
2. Select **Download**, then choose the newest version.
3. Restart Home Assistant.

Without the button, open HACS, select the three-dot menu and **Custom repositories**. Enter `https://github.com/belikh/hvac-horizon` with the category **Integration**.

HVAC Horizon needs Home Assistant 2026.11.2 or later.

## Add a room

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hvac_horizon)

1. Select the button above. Or open **Settings > Devices & services**, select **Add integration** and search for **HVAC Horizon**.
2. In the **Area** step, select the Home Assistant area of the room and select **Submit**. The next forms then suggest the AC and the sensors found in that area. Leave it empty to pick every entity yourself.
3. In the **Room** step, check the suggested devices and sensors, add the outdoor sensor, then select **Submit**.

   | Field | Entity |
   | :-- | :-- |
   | Area | The area of the room. The room's thermostat and sensors are placed in it |
   | Air conditioner | The `climate` entity of the AC. Leave it empty for a room with heating only |
   | AC power sensor (W) | A power sensor in watts that reads the whole AC unit |
   | AC rated power (W) | The watts the AC draws while it runs, for an AC with no power sensor |
   | Heater switch | The switch of the heater. A room needs an AC or a heater switch |
   | Room temperature sensors | One or more temperature sensors in the room |
   | Outdoor temperature sensor | A temperature sensor outside |

4. In the **What next** menu, select **Finish now**.

Home Assistant creates one device named `HVAC Horizon` and the area's name. Without an area the name is the AC's, or the heater switch's in a room with no AC. The device holds the room thermostat and the sensors, and it is placed in the area. One AC or heater switch can have one entry. A second entry for the same device stops with `This device is already set up.`

## First run

The entry starts in observe-only mode. It takes a reading every 5 minutes and sends no commands. At setup it also reads up to 10 days of recorder history.

The Status sensor reads `learning` until the room model has enough data: 3 hours with the AC on, 3 hours with it off, and 3 separate AC runs. A room with heating only needs 12 intervals with the heater on. See [[guides/read-the-forecast]].

## Turn on control

With control on, HVAC Horizon sets the AC mode and setpoint and drives the other devices of the entry.

1. Open **Settings > Devices & services** and select **HVAC Horizon**.
2. On the entry, select **Configure**.
3. Select **Advanced** and turn on **Let HVAC Horizon control the room**. Select **Submit**.
4. Select **Save and close**. The room reloads and keeps its stored samples.

The room thermostat then shows its automatic mode: `heat_cool` in a room that can heat and cool, `auto` in a room that only cools. See [[guides/use-the-room-thermostat]].

:::warning Devices start when control is on
With control on and the automatic mode, the AC starts when the room is above the cool threshold. A heater switch starts when the room is below the heat threshold. Set the thermostat to `off` first if you want nothing to start.
:::

:::see-also
- [[guides/use-the-room-thermostat]]: modes, targets and presets.
- [[guides/set-up-the-integration]]: heaters, prices, presence and other devices.
:::
