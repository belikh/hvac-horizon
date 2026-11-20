.. raw:: html

   <p align="right">
     <picture>
       <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/belikh/design/latest/packages/graphics/logo/jupiter-logo-dark.svg">
       <img alt="jupiter.au" src="https://raw.githubusercontent.com/belikh/design/latest/packages/graphics/logo/jupiter-logo-light.svg" width="260">
     </picture>
   </p>

   <h1>HVAC Horizon</h1>

.. meta::
   :description: Forecasts the time, energy and cost for an air conditioner to reach its setpoint, learned from measured power.

HVAC Horizon is a Home Assistant custom integration. It forecasts how long an air conditioner takes to cool a room to its setpoint, and what the run costs. It learns from measured AC power, room temperature, outdoor temperature and the electricity price.

----

Install
-------

Add this repository to HACS as a custom repository, category *Integration*.

.. image:: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
   :target: https://hacs.xyz/
   :alt: HACS custom repository
 Install it, then restart Home Assistant. Add the integration from *Settings > Devices & Services*, and pick the AC, its power sensor, the room and outdoor temperature sensors and the price sensor.

The integration shows *learning* until it has enough samples to fit. The first forecast needs at least three hours with the AC on and three hours with it off, in several separate runs.

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
