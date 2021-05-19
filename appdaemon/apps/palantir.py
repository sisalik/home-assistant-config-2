from datetime import datetime, timedelta
import math
import time

import hassapi as hass


SCREEN_SENSOR = "binary_sensor.siim_s_phone_interactive"
BATTERY_SENSOR = "sensor.siim_s_phone_battery_level"
PC_SENSOR = "sensor.pc_idle_time"
LOCATION_SENSOR = "sensor.siim_location"
TIMEZONE_SENSOR = "sensor.siim_s_phone_current_time_zone"
MAX_IDLE_TIME = 300  # Seconds
SLEEP_TIME = dict(hour=0)
WAKE_TIME = dict(hour=9)

class Palantir(hass.Hass):
    def initialize(self):
        self.phone_active = False
        self.pc_active = False
        self.last_seen = 0
        self.listen_state(self.phone_activity, SCREEN_SENSOR, immediate=True)
        self.listen_state(self.pc_activity, PC_SENSOR, immediate=True)
        self.run_minutely(self.update_sensors, None)

    def phone_activity(self, entity, attribute, old, new, kwargs):
        self.phone_active = new == "on"
        self.last_seen = time.time()  # Turning the screen off is also activity
        self.update_sensors()

    def pc_activity(self, entity, attribute, old, new, kwargs):
        if new == "unavailable":
            self.pc_active = False
        else:
            self.pc_active = int(new) < MAX_IDLE_TIME
        self.update_sensors()

    def update_sensors(self, kwargs=None):
        activity_percentage = self.calculate_activity()
        try:
            battery_percentage = float(self.get_state(BATTERY_SENSOR))
        except ValueError:
            battery_percentage = 0.0

        self.call_service(
            "mqtt/publish",
            topic="cloud/cmnd/palantir/RED",
            payload=round(activity_percentage, 1),
            retain=True,
        )
        self.call_service(
            "mqtt/publish",
            topic="cloud/cmnd/palantir/GREEN",
            payload=round(battery_percentage, 1),
            retain=True,
        )

    def calculate_activity(self):
        phone_location = self.get_state(LOCATION_SENSOR)
        # In some cirumstances, Siim can be assumed to be completely alert
        if (
            self.phone_active
            or self.pc_active
            or phone_location == "Work"
        ):
            # self.log("Siim is definitely active")
            activity_percentage = 100.0
            self.last_seen = time.time()
        else:
            # If his devices are idle, apply an exponential decay to the activity
            # probability value, starting from when the phone was last active.
            # Determine time constant
            utc_offset = self.get_state(TIMEZONE_SENSOR, "utc_offset")
            phone_time = datetime.utcnow() + timedelta(milliseconds=utc_offset)
            sleep_time = phone_time.replace(**SLEEP_TIME)
            wake_time = phone_time.replace(**WAKE_TIME)
            if phone_location == "Bed":
                # self.log("Siim is in bed")
                time_constant = 30.0
            elif sleep_time < phone_time < wake_time:
                # self.log("Siim is likely to be asleep")
                time_constant = 60.0
            else:
                # self.log("Who knows what Siim is up to")
                time_constant = 120.0
            # Calculate exponential decay using time constant and idle time
            idle_time = time.time() - self.last_seen
            activity_percentage = 100.0 * math.exp(-idle_time / 60.0 / time_constant)
            # self.log(
            #     "Idle for {:.1f} seconds; {:.1f}% active".format(
            #         idle_time, activity_percentage
            #     )
            # )
        return activity_percentage
