logger.info("Hello")
entity_id = data.get('entity_id')
if entity_id is not None:
    service_data = {'entity_id': entity_id}
    hass.services.call('light', 'turn_on', service_data, False)