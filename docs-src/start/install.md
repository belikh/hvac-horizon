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
3. In the **Room** step, select the AC climate entity, the AC power sensor, the room temperature sensors and the outdoor temperature sensor. Select **Submit**.
4. In the **What next** menu, select **Finish now**. The integration starts in observe-only mode.

The menu also lists seven optional sections: comfort and presets, price, heater, occupancy and windows, air quality and humidity, devices and power limits, and advanced. Open a section to fill it in, or submit it empty to skip it. The menu returns after each section. See [[guides/set-up-the-integration]].

The power sensor must read the power of the whole AC unit in watts. A wall plug that reports the unit's draw works.

Each entry creates one device named `HVAC Horizon <climate name>`. The device holds the room climate entity and the sensors.

## Turn on control

Observe-only mode learns and forecasts. It sends no commands to the AC. The `control` field is off until you turn it on.

The integration has no options flow, so there is no **Configure** button. Change settings with the reconfigure action:

1. Open **Settings > Devices and services** and find the **HVAC Horizon** card.
2. Open the entry, select the three-dot menu, and select **Reconfigure**. The menu is titled **Edit HVAC Horizon**.
3. Select **Advanced**, turn on **Let HVAC Horizon control the AC (off = observe only)**, then select **Submit**.
4. Select **Save and close**.

The entry reloads and keeps the stored samples. See [[guides/set-up-the-integration]] for every field.
