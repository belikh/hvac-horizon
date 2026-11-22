"""Investigator: the daily mode-change explanation and the on-demand report, both asked of the conversation agent."""

from __future__ import annotations

import statistics
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hvac_horizon.engine.investigator import (
    ActionRecord,
    CallBudget,
    DayReading,
    REPORT_INSTRUCTION,
    REPORT_WINDOW_MIN,
    day_context,
    day_summary,
    investigate,
)
from .const import CONF_AI_BUDGET, CONF_AI_CONVERSATION
from custom_components.hvac_horizon.room_model.collector import Entry
from custom_components.hvac_horizon.coordinator_common import _LOGGER


class InvestigatorMixin:
    # Instance attributes set in HorizonCoordinator.__init__ and used here.
    _action_log: list[ActionRecord]
    _ai_answer: str | None
    _ai_answer_mode: str | None
    _budget: Any
    _cool_above_c: float
    _entries: list[Entry]
    _last_ai_mode: str | None
    _report: dict[str, Any]
    _report_budget: Any
    _someone_home: bool
    _window_open_since: float | None
    data: dict[str, Any]
    entry: ConfigEntry
    hass: HomeAssistant

    async def _publish_investigation(self, now: float, result: dict[str, Any]) -> None:
        """Explain the AC action from its inputs. Ask the conversation agent when the mode changes, within the daily budget."""
        action_text = result.get("action")
        if action_text and (not self._action_log or self._action_log[-1].text != action_text):
            self._action_log = (self._action_log + [ActionRecord(now / 60.0, action_text)])[-20:]
        rooms = self._rooms()
        window_open_min = 0.0 if self._window_open_since is None else (now - self._window_open_since) / 60.0
        investigation = investigate(
            action_text,
            statistics.median(rooms) if rooms else None,
            self._cool_above_c,
            self._someone_home,
            window_open_min,
            self._action_log,
        )
        result["investigation"] = investigation
        mode_now = action_text.partition(":")[0] if action_text else None
        if mode_now and mode_now != self._last_ai_mode and self.entry.data.get(CONF_AI_CONVERSATION):
            answer = await self._ask_model(f"In one sentence, explain why the AC is {mode_now}. {investigation['answer']}", self._budget)
            # A failed or budget-blocked call clears the stored answer, so it never shows under a different mode.
            # The mode is recorded only after a reply, so the next tick asks again while the budget allows.
            self._ai_answer = answer or None
            self._ai_answer_mode = mode_now if answer else None
            if answer:
                self._last_ai_mode = mode_now
        result["investigation_ai"] = self._ai_answer
        result["investigation_ai_mode"] = self._ai_answer_mode
        result["ai_calls_today"] = self._budget.used(dt_util.now().date())
        result.update(self._report)

    def _day_readings(self, now_min: float) -> list[DayReading]:
        """Stored readings from the last 24 hours."""
        cutoff = now_min - REPORT_WINDOW_MIN
        return [DayReading(e.t / 60.0, e.room_c, e.outdoor_c, e.mode) for e in self._entries if e.t / 60.0 >= cutoff]

    async def async_generate_investigation_report(self) -> None:
        """Build the 24-hour report. Ask the conversation agent when one is set and the on-demand budget allows.

        Otherwise, or when the agent gives no answer, the report is the rule-based summary and its source is "rule".
        """
        now_min = dt_util.utcnow().timestamp() / 60.0
        readings = self._day_readings(now_min)
        text, source = day_summary(readings, now_min), "rule"
        if readings and self.entry.data.get(CONF_AI_CONVERSATION):
            answer = await self._ask_model(f"{REPORT_INSTRUCTION}\n\n{day_context(readings, now_min)}", self._report_budget)
            if answer:
                text, source = answer, "model"
        self._report = {"investigation_report": text, "investigation_report_source": source, "investigation_report_at": dt_util.now().isoformat()}
        if self.data is not None:
            self.data.update(self._report)
        self.async_update_listeners()

    async def _ask_model(self, text: str, budget: CallBudget) -> str | None:
        """One call to the conversation agent, if `budget` allows. Returns the reply text, or None."""
        agent = self.entry.data.get(CONF_AI_CONVERSATION)
        limit = int(self.entry.data.get(CONF_AI_BUDGET) or 0)
        if not agent or not budget.try_take(dt_util.now().date(), limit):
            return None
        try:
            response = await self.hass.services.async_call(
                "conversation", "process", {"agent_id": agent, "text": text}, blocking=True, return_response=True
            )
        except Exception:  # the agent may be missing or unavailable; the rule-based answer still stands
            _LOGGER.debug("conversation agent %s failed", agent, exc_info=True)
            return None
        speech = ((response or {}).get("response") or {}).get("speech", {}).get("plain", {}).get("speech")
        return str(speech) if speech else None
