import hassapi as hass


class PresenceLightController(hass.Hass):
    def initialize(self):
        self._current_room = None
        self._prev_room = None
        self._location_entity = None
        self._off_delay = None
        self._rooms = {}
        self._scenes = {}
        self._delayed_off_handlers = {}

        self._parse_config()
        self._set_initial_room()
        # Listen to the script being called which acts as the interface between HA and
        # AppDaemon
        self.listen_event(
            self.app_service_call,
            event="call_service",
            service="ad_presence_light_controller",
        )

    def light_level_changed(self, entity, attribute, old, new, kwargs):
        room = self._rooms[kwargs["room_name"]]
        if room == self._current_room and room.is_dark:
            for light in room.auto_lights:
                if light.state != "on":
                    light.turn_on()

    def motion_detected(self, entity, attribute, old, new, kwargs):
        room = self._rooms[kwargs["room_name"]]
        if new == "on":
            if room != self._current_room:
                self._enter_new_room(room)
        elif new == "off":
            active_rooms = self._active_rooms
            if len(active_rooms) == 1 and active_rooms[0] != self._current_room:
                self._enter_new_room(active_rooms[0])
        else:
            self.log(f"{entity} state changed to {new}", level="WARNING")

    def app_service_call(self, event_name, data, kwargs):
        service_data = data["service_data"]
        self.log(f"Service called with data: {data}", level="DEBUG")
        try:
            assert "method" in service_data, "Missing data field: 'method'"
        except AssertionError as exception:
            self.log(f"Service data invalid: {exception}", level="ERROR")
            return
        if service_data["method"] == "reset_room":
            self.log("Resetting the lights in the current room")
            self._reset_room()
        elif service_data["method"] == "adjust_light_level":
            self.log(
                f"Adjusting light level in the current room by {service_data['delta']}"
            )
            self._change_scene(service_data["delta"])

    def _reset_room(self):
        if not self._current_room or not self._current_room.is_dark:
            return
        for light in self._current_room.lights:
            if light.is_auto:
                light.turn_on()
            else:
                light.turn_off()

    def _change_scene(self, delta):
        try:
            delta = int(delta)
        except ValueError:
            raise ValueError("Unable to convert delta argument to integer")
        current_scene_switch = self.get_state("input_text.lighting_mode_current")
        current_scene = current_scene_switch.split(".")[1].split("_")[0]
        all_scenes = ["off", "low", "active", "max"]
        current_index = all_scenes.index(current_scene)
        new_index = min(len(all_scenes) - 1, max(0, current_index + delta))
        new_scene_switch = f"switch.{all_scenes[new_index]}_light_mode"
        self.turn_on(new_scene_switch)
        self._reset_room()

    def _enter_new_room(self, room):
        self.log(f"Presence changed {self._current_room} -> {room}")
        self._location_entity.select_option(room.name)
        self._prev_room = self._current_room
        self._current_room = room
        # Initially, the previous room is undefined
        if self._prev_room is None:
            if room.is_dark:
                for light in room.auto_lights:
                    light.turn_on()
            return
        # prev_room_lights = set(self._prev_room.auto_lights)  # Turn off auto lights
        prev_room_lights = set(self._prev_room.lights)  # Turn off all lights
        cur_room_lights = set(self._current_room.auto_lights)
        # Turn on all lights in the current room
        if room.is_dark:
            for light in cur_room_lights:
                light.turn_on()
        else:
            self.log(f"Sufficient daylight in {room} not to turn on lights")
        # Turn off all the lights that aren't part of the current room
        # Cancel any existing handlers for delayed switch-off
        existing_handler = self._delayed_off_handlers.pop(self._prev_room, None)
        if existing_handler:
            self.cancel_timer(existing_handler)
        self._delayed_off_handlers[self._prev_room] = self.run_in(
            self._delayed_switch_off,
            self._off_delay,
            room_name=self._prev_room.name,
        )
        self.log(
            f"Waiting {self._off_delay} s to turn off lights in {self._prev_room}"
        )

    def _set_initial_room(self):
        active_rooms = self._active_rooms
        if len(active_rooms) == 0:
            self.log("No motion sensors currently active")
        elif len(active_rooms) == 1:
            self.log("Found 1 active motion sensor; assuming presence there")
            self._enter_new_room(active_rooms[0])
        else:
            self.log("Multiple rooms with active motion sensors found")

    def _delayed_switch_off(self, kwargs):
        room = self._rooms[kwargs["room_name"]]
        if room != self._current_room:
            self.log(f"Still no presence in {room}; turning the lights off")
            for light in set(room.lights) - set(self._current_room.lights):
                light.turn_off()
        else:
            self.log(f"Presence in {room}; keeping the lights on")

    @property
    def _active_rooms(self):
        return [
            room for room in self._rooms.values() if room.motion_detected
        ]

    def _parse_config(self):
        self._location_entity = InputSelect(self, self.args["location_entity"])
        self._off_delay = self.args["off_delay"]
        # Parse rooms
        for room_dict in self.args["rooms"]:
            room = Room(self, room_dict)
            self._rooms[room.name] = room
        # Parse scenes
        for scene in self.args["scenes"]:
            self.log(f"Registering scene {scene}")


class Room:
    def __init__(self, controller, config):
        self._controller = controller
        self.name = config["name"]
        controller.log(f"Registering room '{self}'")
        self.lights = [Light(controller, light) for light in config["lights"]]
        # Set up light sensor
        self._light_sensor = Entity(controller, config["light_sensor"]["entity"])
        self._light_threshold = None
        if self._light_sensor.entity_id != "sun.sun":
            self._light_threshold = config["light_sensor"]["threshold"]
        controller.listen_state(
            controller.light_level_changed,
            self._light_sensor.entity_id,
            room_name=self.name,
        )
        # Set up motion sensor
        self._motion_sensor = Entity(controller, config["motion_sensor"])
        controller.listen_state(
            controller.motion_detected,
            self._motion_sensor.entity_id,
            room_name=self.name,
        )

    @property
    def auto_lights(self):
        return (light for light in self.lights if light.is_auto)

    @property
    def is_dark(self):
        if self._controller.get_state("sun.sun") == "below_horizon":
            return True
        if self._light_sensor.entity_id == "sun.sun":
            return False  # The previous statement returns if the sun is below horizon
        return float(self._light_sensor.state) < self._light_threshold

    @property
    def motion_detected(self):
        return self._motion_sensor.state == "on"

    def __str__(self):
        return self.name


class Entity:
    def __init__(self, controller, entity_id):
        self._controller = controller
        self.entity_id = entity_id

    @property
    def state(self):
        return self._controller.get_state(self.entity_id)

    def turn_on(self):
        self._controller.turn_on(self.entity_id)
        self._controller.log(f"Turned on {self}")

    def turn_off(self):
        self._controller.turn_off(self.entity_id)
        self._controller.log(f"Turned off {self}")

    def __str__(self):
        return self.entity_id

    def __hash__(self):
        return hash(self.entity_id)

    def __eq__(self, other):
        return (
            self.__class__ == other.__class__
            and self.entity_id == other.entity_id
        )


class Light(Entity):
    def __init__(self, controller, entity_id):
        self._controller = controller
        if isinstance(entity_id, str):
            self.entity_id = entity_id
            self._on_brightness = None
        elif isinstance(entity_id, dict):
            self.entity_id = entity_id["entity"]
            self._on_brightness = entity_id["on_brightness"]
        # Construct the entity_id for the auto mode toggle switch and check it exists
        light_name = self.entity_id.split(".")[1]
        self._auto_mode_toggle = "input_boolean.auto_" + light_name
        if not controller.get_state(self._auto_mode_toggle):
            controller.log(
                f"Expected toggle entity '{self._auto_mode_toggle}' not found for "
                f"'{entity_id}'",
                level="ERROR",
            )

    def turn_on(self):
        if self._on_brightness is not None:
            self._controller.turn_on(self.entity_id, brightness=self._on_brightness)
        else:
            self._controller.turn_on(self.entity_id)
        self._controller.log(f"Turned on {self}")

    @property
    def is_auto(self):
        return self._controller.get_state(self._auto_mode_toggle) == "on"


class InputSelect(Entity):
    def select_option(self, option):
        return self._controller.select_option(self.entity_id, option)
