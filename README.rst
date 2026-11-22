.. raw:: html

   <p align="right">
     <picture>
       <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/belikh/design/latest/packages/graphics/logo/jupiter-logo-dark.svg">
       <img alt="jupiter.au" src="https://raw.githubusercontent.com/belikh/design/latest/packages/graphics/logo/jupiter-logo-light.svg" width="260">
     </picture>
   </p>

   <h1>HVAC Horizon</h1>

.. meta::
   :description: Learns how a room cools and heats, forecasts the time, energy and cost to a target, and controls the room's AC and heater.

HVAC Horizon is a Home Assistant custom integration for one room. It learns how fast the room cools and heats from the AC's power, the room temperature and the outdoor temperature. It forecasts the minutes, energy and cost to reach a target. With control on, it also drives the AC, an optional heater switch and other room devices.

Each entry creates one device with these entities:

- A room ``climate`` entity with a target temperature, the presets ``comfort``, ``eco``, ``boost`` and ``away``, and the modes ``off``, ``cool``, ``auto``, ``heat`` (with a heater), ``dry`` (with an AC dry mode or a dehumidifier) and ``fan_only`` (with an AC fan mode). In ``auto`` it has a low and a high target, and with a room humidity sensor a humidity target.
- 85 sensors. 12 are forecast sensors: minutes, energy and cost to the target, a status, the sample count and the forecast error among them. 73 are diagnostic sensors for the target, preset, heater and valve output, ventilation, humidity and the device dispatch. 43 of those are disabled until you enable them.

Services cover the lock, house mode, timed presets, a heater relay auto-tune, a what-if forecast and data export. Control is off after setup, so a new entry only observes.

----

Install
-------

Open HACS, search for *HVAC Horizon*, download it and restart Home Assistant. If the search finds nothing, add ``https://github.com/belikh/hvac-horizon`` under *Custom repositories* with the category *Integration* first.

.. image:: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
   :target: https://hacs.xyz/
   :alt: HACS custom repository

Open *Settings > Devices & services*, select *Add integration*, search for *HVAC Horizon* and pick the AC climate entity with its power sensor or rated power, or a heater switch for a room with heating only, and the room and outdoor temperature sensors, then choose *Finish now* in the menu, or open any of the optional sections first. To turn control on later, open the integration card, select the entry menu, choose *Reconfigure* and open *Advanced*.

The status sensor shows *learning* until the first fit. The fit needs at least three hours with the AC on and three hours with it off, in at least three separate runs.

Documentation: `Install <https://docs.jupiter.au/hvac-horizon/start/install/>`__, `Set up the integration <https://docs.jupiter.au/hvac-horizon/guides/set-up-the-integration/>`__, `Settings <https://docs.jupiter.au/hvac-horizon/reference/settings/>`__ and `Sensors <https://docs.jupiter.au/hvac-horizon/reference/sensors/>`__.

----

Privacy
-------

HVAC Horizon sends no data outside your Home Assistant. It makes no network requests of its own and has no account, no cloud service and no telemetry.

- It reads the entities you select and stores their readings in Home Assistant's ``.storage`` folder, up to 30 days. Removing the entry deletes that file.
- It calls the ``weather.get_forecasts`` service of the weather entity you select. That integration fetches its own forecast.
- With an AI conversation agent set, it sends that agent a short text with the room's recent temperatures, AC modes and actions. The agent's own integration decides where that text goes. The default is no agent and no calls.
- The diagnostics download replaces entity IDs and the lock PIN with ``**REDACTED**``.

----

Develop
-------

Run all tests in a virtual environment. The package imports Home Assistant, so plain ``python3`` cannot run them.

.. code-block:: sh

   python3 -m venv .venv
   .venv/bin/pip install -r requirements-test.txt
   .venv/bin/python -m pytest

----

Design
------

See ``docs-src/concepts/design.md`` for the model, the inputs and the assumptions of each forecast.

|

.. raw:: html

   <p align="right"><sub>jupiter.au</sub></p>
