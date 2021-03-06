"""Component to make instant statistics about your history."""
import datetime
import logging
import math

import voluptuous as vol

from homeassistant.components import history
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_STATE,
    CONF_TYPE,
    EVENT_HOMEASSISTANT_START,
    TIME_HOURS,
)
try:
    from homeassistant.const import PERCENTAGE
except ImportError:
    # Backwards compatibility with pre-0.117
    from homeassistant.const import UNIT_PERCENTAGE as PERCENTAGE
from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

DOMAIN = "history_stats_in_seconds"
CONF_START = "start"
CONF_END = "end"
CONF_DURATION = "duration"
CONF_PERIOD_KEYS = [CONF_START, CONF_END, CONF_DURATION]

CONF_TYPE_TIME = "time"
CONF_TYPE_RATIO = "ratio"
CONF_TYPE_COUNT = "count"
CONF_TYPE_KEYS = [CONF_TYPE_TIME, CONF_TYPE_RATIO, CONF_TYPE_COUNT]

CONF_TIME_FMT = "format"
CONF_TIME_H = "hours"
CONF_TIME_S = "seconds"
CONF_TIME_FMT_KEYS = [CONF_TIME_H, CONF_TIME_S]
DEFAULT_NAME = "unnamed statistics"
UNITS = {
    CONF_TYPE_TIME: TIME_HOURS,
    CONF_TYPE_RATIO: PERCENTAGE,
    CONF_TYPE_COUNT: "",
}
TIME_UNITS = {CONF_TIME_H: "h", CONF_TIME_S: "s"}
ICON = "mdi:chart-line"

ATTR_VALUE = "value"


def exactly_two_period_keys(conf):
    """Ensure exactly 2 of CONF_PERIOD_KEYS are provided."""
    if sum(param in conf for param in CONF_PERIOD_KEYS) != 2:
        raise vol.Invalid(
            "You must provide exactly 2 of the following: start, end, duration"
        )
    return conf


PLATFORM_SCHEMA = vol.All(
    PLATFORM_SCHEMA.extend(
        {
            vol.Required(CONF_ENTITY_ID): cv.entity_id,
            vol.Required(CONF_STATE): cv.string,
            vol.Optional(CONF_START): cv.template,
            vol.Optional(CONF_END): cv.template,
            vol.Optional(CONF_DURATION): cv.time_period,
            vol.Optional(CONF_TYPE, default=CONF_TYPE_TIME): vol.In(CONF_TYPE_KEYS),
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_TIME_FMT, default=CONF_TIME_H): vol.In(CONF_TIME_FMT_KEYS),
        }
    ),
    exactly_two_period_keys,
)


# noinspection PyUnusedLocal
def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the History Stats sensor."""
    entity_id = config.get(CONF_ENTITY_ID)
    entity_state = config.get(CONF_STATE)
    start = config.get(CONF_START)
    end = config.get(CONF_END)
    duration = config.get(CONF_DURATION)
    sensor_type = config.get(CONF_TYPE)
    name = config.get(CONF_NAME)
    tfmt = config.get(CONF_TIME_FMT)

    for template in [start, end]:
        if template is not None:
            template.hass = hass

    add_entities(
        [
            HistoryStatsSensor(
                hass, entity_id, entity_state, start, end, duration, sensor_type, name, tfmt
            )
        ]
    )

    return True


class HistoryStatsSensor(Entity):
    """Representation of a HistoryStats sensor."""

    def __init__(
        self, hass, entity_id, entity_state, start, end, duration, sensor_type, name, tfmt
    ):
        """Initialize the HistoryStats sensor."""
        self._entity_id = entity_id
        self._entity_state = entity_state
        self._duration = duration
        self._start = start
        self._end = end
        self._type = sensor_type
        self._name = name
        self._unit_of_measurement = UNITS[sensor_type]
        self._tfmt = tfmt
        if sensor_type == CONF_TYPE_TIME:
            self._unit_of_measurement = TIME_UNITS[tfmt]

        self._period = (datetime.datetime.now(), datetime.datetime.now())
        self.value = None
        self.count = None

        @callback
        def start_refresh(*args):
            """Register state tracking."""

            @callback
            def force_refresh(*args):
                """Force the component to refresh."""
                self.async_schedule_update_ha_state(True)

            force_refresh()
            async_track_state_change(self.hass, self._entity_id, force_refresh)

        # Delay first refresh to keep startup fast
        hass.bus.listen_once(EVENT_HOMEASSISTANT_START, start_refresh)

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        if self.value is None or self.count is None:
            return None

        if self._type == CONF_TYPE_TIME:
            return HistoryStatsHelper.value_duration(self.value, self._tfmt)

        if self._type == CONF_TYPE_RATIO:
            return HistoryStatsHelper.pretty_ratio(self.value, self._period)

        if self._type == CONF_TYPE_COUNT:
            return self.count

    @property
    def unit_of_measurement(self):
        """Return the unit the value is expressed in."""
        return self._unit_of_measurement

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def device_state_attributes(self):
        """Return the state attributes of the sensor."""
        if self.value is None:
            return {}

        hsh = HistoryStatsHelper
        return {ATTR_VALUE: hsh.pretty_duration(self.value, self._tfmt)}

    @property
    def icon(self):
        """Return the icon to use in the frontend, if any."""
        return ICON

    def update(self):
        """Get the latest data and updates the states."""
        # Get previous values of start and end
        p_start, p_end = self._period

        # Parse templates
        self.update_period()
        start, end = self._period

        # Convert times to UTC
        start = dt_util.as_utc(start)
        end = dt_util.as_utc(end)
        p_start = dt_util.as_utc(p_start)
        p_end = dt_util.as_utc(p_end)
        now = datetime.datetime.now()

        # Compute integer timestamps
        start_timestamp = math.floor(dt_util.as_timestamp(start))
        end_timestamp = math.floor(dt_util.as_timestamp(end))
        p_start_timestamp = math.floor(dt_util.as_timestamp(p_start))
        p_end_timestamp = math.floor(dt_util.as_timestamp(p_end))
        now_timestamp = math.floor(dt_util.as_timestamp(now))

        # If period has not changed and current time after the period end...
        if (
            start_timestamp == p_start_timestamp
            and end_timestamp == p_end_timestamp
            and end_timestamp <= now_timestamp
        ):
            # Don't compute anything as the value cannot have changed
            return

        # Get history between start and end
        history_list = history.state_changes_during_period(
            self.hass, start, end, str(self._entity_id)
        )

        if self._entity_id not in history_list.keys():
            return

        # Get the first state
        last_state = history.get_state(self.hass, start, self._entity_id)
        last_state = last_state is not None and last_state == self._entity_state
        last_time = start_timestamp
        elapsed = 0
        count = 0

        # Make calculations
        for item in history_list.get(self._entity_id):
            current_state = item.state == self._entity_state
            current_time = item.last_changed.timestamp()

            if last_state:
                elapsed += current_time - last_time
            if current_state and not last_state:
                count += 1

            last_state = current_state
            last_time = current_time

        # Count time elapsed between last history state and end of measure
        if last_state:
            measure_end = min(end_timestamp, now_timestamp)
            elapsed += measure_end - last_time

        # Save value in hours
        self.value = elapsed / 3600

        # Save counter
        self.count = count

    def update_period(self):
        """Parse the templates and store a datetime tuple in _period."""
        start = None
        end = None

        # Parse start
        if self._start is not None:
            try:
                start_rendered = self._start.render()
            except (TemplateError, TypeError) as ex:
                HistoryStatsHelper.handle_template_exception(ex, "start")
                return
            start = dt_util.parse_datetime(start_rendered)
            if start is None:
                try:
                    start = dt_util.as_local(
                        dt_util.utc_from_timestamp(math.floor(float(start_rendered)))
                    )
                except ValueError:
                    _LOGGER.error(
                        "Parsing error: start must be a datetime or a timestamp"
                    )
                    return

        # Parse end
        if self._end is not None:
            try:
                end_rendered = self._end.render()
            except (TemplateError, TypeError) as ex:
                HistoryStatsHelper.handle_template_exception(ex, "end")
                return
            end = dt_util.parse_datetime(end_rendered)
            if end is None:
                try:
                    end = dt_util.as_local(
                        dt_util.utc_from_timestamp(math.floor(float(end_rendered)))
                    )
                except ValueError:
                    _LOGGER.error(
                        "Parsing error: end must be a datetime or a timestamp"
                    )
                    return

        # Calculate start or end using the duration
        if start is None:
            start = end - self._duration
        if end is None:
            end = start + self._duration

        if start > dt_util.now():
            # History hasn't been written yet for this period
            return
        if dt_util.now() < end:
            # No point in making stats of the future
            end = dt_util.now()

        self._period = start, end


class HistoryStatsHelper:
    """Static methods to make the HistoryStatsSensor code lighter."""

    @staticmethod
    def pretty_duration(hours, fmt):
        """Format a duration in days, hours, minutes, seconds."""
        seconds = int(3600 * hours)
        if fmt == CONF_TIME_S:
            return "%ds" % seconds
        else:
            days, seconds = divmod(seconds, 86400)
            hours, seconds = divmod(seconds, 3600)
            minutes, seconds = divmod(seconds, 60)
            if days > 0:
                return "%dd %dh %dm" % (days, hours, minutes)
            if hours > 0:
                return "%dh %dm" % (hours, minutes)
            return "%dm" % minutes

    @staticmethod
    def value_duration(hours, fmt):
        """Return the duration in hours or seconds according to the configured fmt"""
        seconds = int(3600 * hours)
        if fmt == CONF_TIME_S:
            return "%d" % seconds
        else:
            return "%d" % hours

    @staticmethod
    def pretty_ratio(value, period):
        """Format the ratio of value / period duration."""
        if len(period) != 2 or period[0] == period[1]:
            return 0.0

        ratio = 100 * 3600 * value / (period[1] - period[0]).total_seconds()
        return round(ratio, 1)

    @staticmethod
    def handle_template_exception(ex, field):
        """Log an error nicely if the template cannot be interpreted."""
        if ex.args and ex.args[0].startswith("UndefinedError: 'None' has no attribute"):
            # Common during HA startup - so just a warning
            _LOGGER.warning(ex)
            return
        _LOGGER.error("Error parsing template for field %s", field)
        _LOGGER.error(ex)
