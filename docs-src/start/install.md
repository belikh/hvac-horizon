---
title: Install
summary: Install HVAC Horizon with HACS, then add it from the Home Assistant settings.
section: start
order: 1
audience: users
status: draft
---

## Install with HACS

Add the repository to HACS as a custom integration, then install it.

1. Open HACS, select the three-dot menu, and select **Custom repositories**.
2. Enter `https://github.com/belikh/hvac-horizon` and select **Integration** as the category.
3. Select **HVAC Horizon**, then select **Download**.
4. Restart Home Assistant.

## Add the integration

1. Open **Settings > Devices and services**, then select **Add integration**.
2. Search for **HVAC Horizon**.
3. Select the AC climate entity, the AC power sensor, the room temperature sensors and the outdoor temperature sensor.
4. Select **Submit**. The integration starts in observe-only mode.

The power sensor must read the power of the whole AC unit in watts. A wall plug that reports the unit's draw works.

## Turn on control

Observe-only mode learns and forecasts. It sends no commands to the AC.

To let the integration set the AC, open the integration's settings, select **Configure**, and turn on **Control**.
