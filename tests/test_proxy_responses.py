"""Unit tests for how the BACnet interface interprets replies from the BACnet Protocol Proxy.
These run without a platform or a BACnet device."""
import json

import pytest

from gevent.event import AsyncResult

from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetRemoteConfig, BACnetRegister


def _interface(max_per_request=24):
    interface = BACnet.__new__(BACnet)
    interface.config = BacnetRemoteConfig(driver_type='bacnet', device_address='10.0.0.5', device_id=7, max_per_request=max_per_request,
                                          timeout=1.0)
    interface.point_map = {f'p{i}': BACnetRegister(i, 'analogInput', 'present-value', True, f'p{i}', 'degF')
                           for i in range(50)}
    interface.proxy_peer = object()
    interface.sent = []

    class FakePPM:
        def __init__(self, replies):
            self.replies = list(replies)

        def send(self, peer, message):
            interface.sent.append(json.loads(message.payload.decode('utf8')))
            reply = self.replies.pop(0)
            if isinstance(reply, (bytes, bytearray)):
                result = AsyncResult()
                result.set(reply)
                return result
            return reply
    interface.make_ppm = lambda *replies: setattr(interface, 'ppm', FakePPM(replies))
    return interface


def _serialized(result, error=None):
    return json.dumps({'result': result, 'error': error or {}}).encode('utf8')


def test_scalar_success_and_serializer_error():
    interface = _interface()
    interface.make_ppm(_serialized(72.5), _serialized(0), _serialized({}, {'p1': {'error': 'ErrorPDU'}}))
    assert interface.get_point('p0') == 72.5
    assert interface.get_point('p0') == 0    # Falsy values are still results.
    with pytest.raises(RuntimeError, match='ErrorPDU'):
        interface.get_point('p1')


def test_scalar_proxy_error_status_is_not_a_value():
    interface = _interface()
    error = json.dumps({'status': 'error', 'error': "ValueError('bad')", 'method': 'READ_PROPERTY'}).encode('utf8')
    interface.make_ppm(error, b'', False)
    with pytest.raises(RuntimeError, match='READ_PROPERTY failed'):
        interface.get_point('p0')
    with pytest.raises(RuntimeError, match='Empty response'):
        interface.get_point('p0')
    with pytest.raises(RuntimeError, match='Unable to send'):    # send() refused (peer not registered).
        interface.get_point('p0')


def test_get_multiple_points_batches_by_max_per_request():
    interface = _interface(max_per_request=24)
    interface.make_ppm(*[_serialized({}) for _ in range(3)])
    interface.get_multiple_points(list(interface.point_map)[:10])
    assert [len(m['read_specifications']) for m in interface.sent] == [10]    # Fewer points than the limit still poll.
    interface.sent.clear()
    interface.get_multiple_points(list(interface.point_map))
    assert [len(m['read_specifications']) for m in interface.sent] == [24, 24, 2]


def test_get_multiple_points_unlimited_and_empty():
    interface = _interface(max_per_request=0)
    interface.make_ppm(_serialized({}))
    interface.get_multiple_points(list(interface.point_map))
    assert [len(m['read_specifications']) for m in interface.sent] == [50]
    interface.sent.clear()
    assert interface.get_multiple_points([]) == ({}, {})
    assert interface.sent == []


def test_get_multiple_points_merges_results_and_attributes_batch_failures_to_topics():
    interface = _interface(max_per_request=2)
    proxy_error = json.dumps({'status': 'error', 'error': 'TimeoutError()', 'method': 'BATCH_READ'}).encode('utf8')
    interface.make_ppm(_serialized({'p0': 1.0, 'p1': 2.0}), proxy_error, _serialized({'p4': 5.0}, {'p5': 'nack'}))
    results, errors = interface.get_multiple_points(['p0', 'p1', 'p2', 'p3', 'p4', 'p5'])
    assert results == {'p0': 1.0, 'p1': 2.0, 'p4': 5.0}
    assert set(errors) == {'p2', 'p3', 'p5'}
    assert 'BATCH_READ failed' in errors['p2'] and errors['p5'] == 'nack'


def test_bacnet_port_alias_offset_and_setter():
    config = BacnetRemoteConfig(driver_type='bacnet', device_address='10.0.0.5', device_id=7, bacnet_port=47809)
    assert config.bacnet_port_configured == 47809 and config.bacnet_port == 1
    config.bacnet_port = 3
    assert config.bacnet_port_configured == 3 and config.cov_lifetime_configured == 180.0
