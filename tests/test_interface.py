"""Tests for the BACnet driver interface: configuration, register creation, setup, and proxy messaging."""
import json
import logging

import pytest

from volttron.driver.base.config import RemoteConfig
from volttron.driver.base.driver_exceptions import DriverConfigError
from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetPointConfig, BacnetRemoteConfig, BACnetRegister

from tests.conftest import TOPIC, point, serialized


class TestPointConfig:
    def test_legacy_csv_headers(self):
        cfg = BacnetPointConfig(**{'Volttron Point Name': 'Temp', 'Units': 'degF', 'Writable': 'FALSE',
                                   'BACnet Object Type': 'analogInput', 'Index': '3', 'Property': '',
                                   'COV Flag': '', 'Write Priority': ''})
        assert (cfg.object_type, cfg.instance, cfg.property) == ('analogInput', 3, 'present-value')
        assert cfg.cov_flag is False and cfg.write_priority == 16 and cfg.array_index is None

    def test_aliases_and_bounds(self):
        cfg = BacnetPointConfig(volttron_point_name='x', bacnet_object_type='binaryValue', index=1, cov_flag=True,
                                write_priority=8, array_index='')
        assert cfg.object_type == 'binaryValue' and cfg.cov_flag is True and cfg.write_priority == 8
        with pytest.raises(ValueError):
            BacnetPointConfig(volttron_point_name='x', object_type='binaryValue', instance=1, write_priority=17)


class TestRemoteConfig:
    def test_computed_intervals_and_setters(self):
        cfg = BacnetRemoteConfig(driver_type='bacnet', device_address='10.0.0.5', device_id=7,
                                 ping_retry_interval=2.5, cov_lifetime=60, time_synchronization_interval=3600)
        assert cfg.ping_retry_interval.total_seconds() == 2.5
        assert cfg.cov_lifetime.total_seconds() == 60 and cfg.time_synchronization_interval.total_seconds() == 3600
        cfg.time_synchronization_interval = cfg.cov_lifetime
        assert cfg.time_synchronization_seconds == 60.0
        assert BacnetRemoteConfig(driver_type='bacnet', device_address='h', device_id=1).time_synchronization_interval is None

    def test_priority_bounds(self):
        with pytest.raises(ValueError):
            BacnetRemoteConfig(driver_type='bacnet', device_address='h', device_id=1, min_priority=0)


class TestCreateRegister:
    @pytest.mark.parametrize('object_type, python_type', [
        ('analogInput', float), ('analogOutput', float), ('analogValue', float),
        ('binaryInput', bool), ('binaryOutput', bool), ('binaryValue', bool),
        ('multiStateInput', int), ('multiStateOutput', int), ('multiStateValue', int),
    ])
    def test_python_type_from_object_type(self, make_interface, object_type, python_type):
        reg = make_interface().create_register(point('p', object_type, 4))
        assert isinstance(reg, BACnetRegister) and reg.python_type is python_type
        assert reg.get_register_type() == ('byte', True) and reg.instance_number == 4

    def test_writable_priority_and_cov_carry_over(self, interface):
        sp = interface.point_map[TOPIC('Setpoint')]
        assert sp.read_only is False and sp.priority == 8 and sp.is_cov is False
        assert interface.point_map[TOPIC('Temp')].is_cov is True
        modes = interface.point_map[TOPIC('Modes')]
        assert modes.array_index == 2 and modes.property == 'state-text'

    def test_priority_below_minimum_rejected(self, make_interface):
        with pytest.raises(DriverConfigError, match='lower than'):
            make_interface(min_priority=8).create_register(point('p', 'analogValue', 1, writable=True, write_priority=4))

    def test_unknown_object_type_rejected(self, make_interface):
        with pytest.raises(KeyError):
            make_interface().create_register(point('p', 'calendar', 1))


class TestSetup:
    def test_constructor_wires_the_shared_manager(self, interface, ppm, driver_agent):
        assert interface.ppm is ppm and ppm.started == 1
        assert 'RECEIVE_COV' in ppm.callbacks
        driver_agent.core.spawn.assert_called_once_with(ppm.select_loop)

    def test_finalize_setup_launches_proxy_pings_and_subscribes(self, interface, ppm):
        interface.finalize_setup(initial_setup=True)
        key, options = ppm.launch
        assert key == ('10.0.0.1/24', 0)
        assert options['local_interface'] == '10.0.0.1/24' and options['bacnet_port'] == 0
        assert options['apdu_timeout'] == 3.0 and options['apdu_retries'] == 3 and options['batch_read_timeout'] == 60.0
        assert ppm.registration_waits == [interface.config.registration_timeout] * 2      # ping + one COV point
        methods = [m for m, _, _ in ppm.sent]
        assert methods == ['WHO_IS', 'SETUP_COV']
        who_is = ppm.payloads('WHO_IS')[0]
        assert who_is == {'device_instance_low': 7, 'device_instance_high': 7, 'dest': '10.0.0.5'}
        cov = ppm.payloads('SETUP_COV')[0]
        assert cov == {'subscription_key': TOPIC('Temp'), 'device_address': '10.0.0.5',
                       'monitored_object_identifier': 'analogInput:1', 'property_identifier': 'present-value',
                       'issue_confirmed_notifications': True, 'lifetime': 180.0, 'remote_id': interface.remote_id.hex}
        assert [e for _, _, e in ppm.sent] == [False, False]                             # fire-and-forget

    def test_finalize_setup_without_initial_flag_skips_ping(self, interface, ppm):
        interface.finalize_setup()
        assert [m for m, _, _ in ppm.sent] == ['SETUP_COV']

    def test_time_synchronization_setup_and_teardown(self, make_interface, ppm):
        iface = make_interface(time_synchronization_interval=600)
        iface.proxy_peer = ppm.peer
        iface.finalize_setup()
        assert ppm.payloads('SETUP_TIME_SYNCHRONIZATION') == [{'device_address': '10.0.0.5', 'interval': 600.0,
                                                               'time_zone': 'UTC'}]
        assert iface.time_synchronization_active is True
        iface.config.time_synchronization_seconds = None                 # reconfigured without an interval
        iface.setup_time_synchronization()
        assert ppm.payloads('SETUP_TIME_SYNCHRONIZATION')[-1]['interval'] is None
        assert iface.time_synchronization_active is False
        iface.setup_time_synchronization()                               # inactive and unconfigured: nothing sent
        assert len(ppm.payloads('SETUP_TIME_SYNCHRONIZATION')) == 2

    def test_ping_failure_schedules_retry(self, interface, ppm, driver_agent):
        def failing_send(peer, message):
            raise RuntimeError('not registered')
        ppm.send = failing_send
        interface.ping_target()
        assert interface.scheduled_ping == 'scheduled'
        when, func = driver_agent.core.schedule.call_args.args
        assert func == interface.ping_target
        interface.schedule_ping()                                        # already scheduled: no second schedule
        assert driver_agent.core.schedule.call_count == 1

    def test_unique_remote_id(self):
        cfg = RemoteConfig(driver_type='bacnet', device_address='10.0.0.5', device_id=7, local_interface='10.0.0.1/24')
        assert BACnet.unique_remote_id('ahu.config', cfg) == ('10.0.0.5', 7)

    def test_register_count(self, interface):
        assert interface.register_count == 4


class TestReadsAndWrites:
    def test_get_point_payload(self, interface, ppm):
        ppm.queue(serialized(68.25))
        assert interface.get_point(TOPIC('Modes')) == 68.25
        [(method, payload, expected)] = ppm.sent
        assert method == 'READ_PROPERTY' and expected is True
        assert payload == {'device_address': '10.0.0.5', 'object_identifier': 'multiStateValue, 3',
                           'property_identifier': 'state-text', 'property_array_index': 2}
        ppm.queue(serialized('Temp'))
        assert interface.get_point(TOPIC('Temp'), on_property='object-name') == 'Temp'
        assert ppm.payloads('READ_PROPERTY')[-1]['property_identifier'] == 'object-name'

    def test_get_point_not_initialized(self, make_interface):
        iface = make_interface([point('p', 'analogInput', 1)])
        with pytest.raises(Exception, match='not initialized'):
            iface.get_point(TOPIC('p'))

    def test_set_point_payload_and_priority_default(self, interface, ppm):
        ppm.queue(serialized(None))
        assert interface.set_point(TOPIC('Setpoint'), 65.0) is None      # BACnet write acks carry no value
        [(method, payload, _)] = ppm.sent
        assert method == 'WRITE_PROPERTY'
        assert payload == {'device_address': '10.0.0.5', 'object_identifier': 'analogValue, 1',
                           'property_identifier': 'present-value', 'value': 65.0, 'priority': 8,
                           'property_array_index': None}
        ppm.queue(serialized(None))
        interface.set_point(TOPIC('Setpoint'), 66.0, priority=12, on_property='relinquish-default')
        assert ppm.payloads()[-1]['priority'] == 12 and ppm.payloads()[-1]['property_identifier'] == 'relinquish-default'

    def test_set_point_rejections(self, interface, ppm):
        with pytest.raises(IOError, match='read only'):
            interface.set_point(TOPIC('Temp'), 1.0)
        with pytest.raises(IOError, match='priority lower than the minimum'):
            interface.set_point(TOPIC('Setpoint'), 1.0, priority=3)
        assert ppm.sent == []

    def test_set_point_error_from_proxy(self, interface, ppm):
        ppm.queue(serialized({}, {TOPIC('Setpoint'): {'error': 'ErrorPDU', 'error_code': 'write-access-denied'}}))
        with pytest.raises(RuntimeError, match='write-access-denied'):
            interface.set_point(TOPIC('Setpoint'), 65.0)

    def test_revert_point_and_all_write_null(self, interface, ppm):
        interface.revert_point(TOPIC('Setpoint'))
        assert ppm.payloads('WRITE_PROPERTY') == [{'device_address': '10.0.0.5', 'object_identifier': 'analogValue, 1',
                                                   'property_identifier': 'present-value', 'value': None, 'priority': 8,
                                                   'property_array_index': None}]
        ppm.sent.clear()
        interface.revert_all()
        reverted = {p['object_identifier'] for p in ppm.payloads('WRITE_PROPERTY')}
        assert reverted == {'analogValue, 1', 'binaryValue, 1'}          # writable points only
        assert all(p['value'] is None for p in ppm.payloads('WRITE_PROPERTY'))

    def test_revert_all_continues_past_failures(self, interface, ppm, caplog):
        ppm.queue(serialized({}, {TOPIC('Setpoint'): {'error': 'ErrorPDU', 'error_code': 'unknown-object'}}), serialized(None))
        with caplog.at_level(logging.WARNING):
            interface.revert_all()
        assert len(ppm.payloads('WRITE_PROPERTY')) == 2 and 'Error while reverting point' in caplog.text

    def test_get_multiple_points_on_property(self, interface, ppm):
        """on_property applies to every point of a batch read, as it does for a single get_point; without it each
        register's configured property is read."""
        ppm.queue(serialized({TOPIC('Temp'): 70.0, TOPIC('Modes'): 'Auto'}))
        interface.get_multiple_points([TOPIC('Temp'), TOPIC('Modes')])
        specs = ppm.payloads('BATCH_READ')[-1]['read_specifications']
        assert {t: s['property'] for t, s in specs.items()} == {TOPIC('Temp'): 'present-value',
                                                               TOPIC('Modes'): 'state-text'}
        ppm.queue(serialized({TOPIC('Temp'): 'Temp', TOPIC('Modes'): 'Modes'}))
        results, errors = interface.get_multiple_points([TOPIC('Temp'), TOPIC('Modes')], on_property='object-name')
        specs = ppm.payloads('BATCH_READ')[-1]['read_specifications']
        assert all(s['property'] == 'object-name' for s in specs.values()) and len(specs) == 2
        assert specs[TOPIC('Modes')]['array_index'] == 2     # other fields still come from the register
        assert results == {TOPIC('Temp'): 'Temp', TOPIC('Modes'): 'Modes'} and errors == {}

    def test_reads_ignore_write_only_keywords(self, interface, ppm):
        """A confirming read after a write receives the write's keywords: on_property selects what to read back and
        priority, meaningless for a read, is ignored rather than rejected."""
        ppm.queue(serialized(66.0))
        assert interface.get_point(TOPIC('Setpoint'), priority=12, on_property='relinquish-default') == 66.0
        assert ppm.payloads('READ_PROPERTY')[-1]['property_identifier'] == 'relinquish-default'
        ppm.queue(serialized({TOPIC('Setpoint'): 66.0}))
        results, errors = interface.get_multiple_points([TOPIC('Setpoint')], priority=12,
                                                        on_property='relinquish-default')
        assert results == {TOPIC('Setpoint'): 66.0} and errors == {}
        assert ppm.payloads('BATCH_READ')[-1]['read_specifications'][TOPIC('Setpoint')]['property'] == 'relinquish-default'

    def test_set_multiple_points_writes_each(self, interface, ppm):
        results, errors = interface.set_multiple_points([(TOPIC('Setpoint'), 65.0), (TOPIC('Temp'), 1.0)])
        assert results == {TOPIC('Setpoint'): None} and 'read only' in errors[TOPIC('Temp')]
        assert len(ppm.payloads('WRITE_PROPERTY')) == 1


class TestCOV:
    def test_receive_push_publishes_values(self, interface, driver_agent):
        raw = json.dumps({'result': {TOPIC('Temp'): 70.5}, 'error': {}}).encode('utf8')
        interface.receive_push.__wrapped__(interface, None, raw)
        driver_agent.publish_push.assert_called_once_with({TOPIC('Temp'): 70.5})

    def test_receive_push_logs_errors_and_publishes_nothing(self, interface, driver_agent, caplog):
        raw = json.dumps({'result': {}, 'error': {TOPIC('Temp'): 'subscription failed'}}).encode('utf8')
        with caplog.at_level(logging.WARNING):
            interface.receive_push.__wrapped__(interface, None, raw)
        assert 'Error received with pushed values' in caplog.text and not driver_agent.publish_push.called

    def test_establish_cov_subscription_uses_lifetime_seconds(self, interface, ppm):
        from datetime import timedelta
        interface.establish_cov_subscription(interface.point_map[TOPIC('Temp')], TOPIC('Temp'), timedelta(minutes=2))
        assert ppm.payloads('SETUP_COV')[0]['lifetime'] == 120.0


from pydantic import ValidationError
from volttron.driver.base.config import RemoteConfig
from volttron.driver.interfaces.bacnet.bacnet import BacnetRemoteConfig, DriverRole

SERVED = {'driver_type': 'bacnet', 'driver_role': 'server', 'local_interface': '127.0.0.1/32', 'bacnet_port': 3, 'served_port': 47850,
          'device_id': 4242, 'object_name': 'rooftop-unit'}
SERVED_POINTS = [
    point('Temp', 'analogInput', 1, writable=True, data_source='server', default_value='68.25', cov_increment=0.1),
    point('Setpoint', 'analogValue', 1, writable=True, data_source='server', default_value='72.5'),
    point('Fan', 'binaryValue', 1, writable=True, data_source='server', units='', default_value='off'),
    point('Mode', 'multiStateValue', 1, writable=True, data_source='server', units='', number_of_states=3, remote_writable=False),
]


class TestServerRole:
    def test_role_values_and_fields(self):
        cfg = BacnetRemoteConfig(**SERVED)
        assert cfg.driver_role is DriverRole.server and cfg.is_server and cfg.target_address is None
        assert cfg.served_fields() == {'role': 'server', 'local_interface': '127.0.0.1/32', 'port': 47850, 'device_id': 4242,
                                       'object_name': 'rooftop-unit'}
        assert cfg.proxy_key() == ('127.0.0.1/32', 3) and cfg.resolved_proxy_port == 47811
        assert BacnetRemoteConfig(driver_type='bacnet', driver_role='outstation', served_port=5, device_id=1).resolved_served_port == 47813
        assert BacnetRemoteConfig(driver_type='bacnet', device_address='h', device_id=1).driver_role is DriverRole.client
        with pytest.raises(ValidationError, match='device_address'):
            BacnetRemoteConfig(driver_type='bacnet', device_id=1)
        with pytest.raises(ValidationError, match='served_port'):
            BacnetRemoteConfig(driver_type='bacnet', driver_role='server', device_id=1)
        with pytest.raises(ValidationError, match='must differ'):
            BacnetRemoteConfig(driver_type='bacnet', driver_role='server', device_id=1, bacnet_port=3, served_port=47811)

    def test_served_rows(self, make_interface):
        iface = make_interface(SERVED_POINTS, **SERVED)
        reg = {t.rsplit('/', 1)[-1]: r for t, r in iface.point_map.items()}
        assert (reg['Temp'].remote_writable, reg['Temp'].initial_value, reg['Temp'].cov_increment) == (False, 68.25, 0.1)
        assert (reg['Setpoint'].remote_writable, reg['Setpoint'].initial_value) == (True, 72.5)
        assert (reg['Fan'].remote_writable, reg['Fan'].initial_value) == (True, False)
        assert (reg['Mode'].remote_writable, reg['Mode'].number_of_states, reg['Mode'].initial_value) == (False, 3, None)
        row = BacnetPointConfig(**{'Volttron Point Name': 'x', 'Object Type': 'analogValue', 'Instance': '2', 'Data Source': 'server',
                                   'Remote Writable': 'FALSE', 'Starting Value': '5', 'Number Of States': '', 'COV Increment': '0.5'})
        assert row.is_served and row.resolved_remote_writable is False and row.default_value == '5' and row.cov_increment == 0.5

    def test_prepare_registry_config_defaults_served_rows(self, make_interface):
        iface = make_interface(**SERVED)
        rows = [{'Volttron Point Name': 'a', 'Object Type': 'analogValue', 'Instance': 1},
                {'Volttron Point Name': 'b', 'Object Type': 'analogInput', 'Instance': 2, 'Writable': 'FALSE', 'Data Source': 'static'}]
        prepared = iface.prepare_registry_config(rows, RemoteConfig(**SERVED))
        assert prepared[0] == {**rows[0], 'data_source': 'server', 'writable': True} and prepared[1] == rows[1]
        assert iface.prepare_registry_config(rows, RemoteConfig(driver_type='bacnet', device_address='h', device_id=1)) == rows

    def test_registration_describes_the_served_device_and_skips_client_setup(self, make_interface, ppm):
        iface = make_interface(SERVED_POINTS, **SERVED)
        ppm.queue(serialized({'server': '127.0.0.1/32:47850:4242', 'points': 4, 'seeded': 0, 'port': 47850}))
        iface.finalize_setup(initial_setup=True)
        assert ppm.launch == (('127.0.0.1/32', 3), {**iface.config.proxy_launch_options(), 'persistent': True})
        (method, payload, _), = ppm.sent                                            # no WHO_IS, no SETUP_COV
        assert method == 'REGISTER_REMOTE'
        assert {k: v for k, v in payload.items() if k != 'points'} == {'role': 'server', 'local_interface': '127.0.0.1/32', 'port': 47850,
                                                                        'device_id': 4242, 'object_name': 'rooftop-unit', 'values': {},
                                                                        'remote_id': iface.remote_id.hex}
        by_topic = {p['topic']: p for p in payload['points']}
        assert by_topic[TOPIC('Temp')] == {'topic': TOPIC('Temp'), 'object_type': 'analogInput', 'instance': 1, 'property': 'present-value',
                                           'units': 'degF', 'remote_writable': False, 'initial_value': 68.25, 'number_of_states': 2,
                                           'cov_increment': 0.1, 'object_name': ''}
        assert by_topic[TOPIC('Mode')]['number_of_states'] == 3 and by_topic[TOPIC('Fan')]['initial_value'] is False

    def test_served_reads_writes_and_revert(self, make_interface, ppm, driver_agent):
        iface = make_interface(SERVED_POINTS, **SERVED)
        iface.proxy_peer = ppm.peer
        ppm.queue(serialized({TOPIC('Temp'): 68.25, TOPIC('Fan'): True}))
        results, errors = iface.get_multiple_points([TOPIC('Temp'), TOPIC('Fan')])
        assert results == {TOPIC('Temp'): 68.25, TOPIC('Fan'): True} and errors == {}
        assert ppm.payloads('READ_POINTS') == [{'topics': [TOPIC('Temp'), TOPIC('Fan')]}]
        ppm.queue(serialized({TOPIC('Setpoint'): {'status': 'stored', 'value': 70.0}}))
        assert iface.set_point(TOPIC('Setpoint'), '70') == 70.0
        assert ppm.payloads('WRITE_POINTS') == [{'values': {TOPIC('Setpoint'): 70.0}}]
        driver_agent.publish_push.assert_called_with({TOPIC('Setpoint'): 70.0})
        ppm.queue(serialized({}, {TOPIC('Mode'): 'bad value'}))
        with pytest.raises(IOError, match='bad value'):
            iface.set_point(TOPIC('Mode'), 2)
        ppm.queue(serialized({TOPIC('Setpoint'): {'status': 'stored'}}))
        iface.revert_point(TOPIC('Setpoint'))
        assert ppm.payloads('WRITE_POINTS')[-1] == {'values': {TOPIC('Setpoint'): 72.5}}         # the starting value
        ppm.sent.clear()
        iface.revert_point(TOPIC('Mode'))                                                           # no starting value: nothing
        assert ppm.sent == []
        ppm.queue(serialized({TOPIC('Temp'): 69.0}))
        assert iface.get_point(TOPIC('Temp')) == 69.0

    def test_unique_remote_id_includes_the_role(self):
        assert BACnet.unique_remote_id('x', RemoteConfig(**SERVED)) == ('bacnet', 'server', '127.0.0.1/32', 47850, 4242)
