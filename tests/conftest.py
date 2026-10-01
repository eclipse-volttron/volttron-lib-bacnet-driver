"""Shared fixtures for the BACnet interface tests. These run without a platform, a proxy, or a device."""
import json

from pathlib import Path
from unittest import mock

import pytest

from gevent.event import AsyncResult

from volttron.driver.base.config import RemoteConfig
from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetPointConfig

TESTS_DIR = Path(__file__).parent
TMP_DIR = TESTS_DIR / "tmp"
FIXTURES_DIR = TESTS_DIR / "fixtures"


class FakePPM:
    """Stands in for GeventProtocolProxyManager: records payloads, replays queued replies, runs registration hooks."""

    def __init__(self):
        self.sent: list[tuple[str, dict, bool]] = []
        self.replies: list = []
        self.callbacks: dict[str, object] = {}
        self.peer = object()
        self.started = 0
        self.launch: tuple | None = None
        self.registration_waits: list[float] = []

    def queue(self, *replies):
        self.replies.extend(replies)

    def register_callback(self, fn, name, provides_response=False, timeout=30.0):
        self.callbacks[name] = fn

    def start(self):
        self.started += 1

    def select_loop(self):
        pass

    def get_proxy(self, key, **kwargs):
        self.launch = (key, kwargs)
        return self.peer

    def wait_peer_registered(self, peer, timeout, func=None, *args, **kwargs):
        self.registration_waits.append(timeout)
        if func:
            func(*args, **kwargs)

    def send(self, peer, message):
        self.sent.append((message.method_name, json.loads(message.payload.decode('utf8')), message.response_expected))
        reply = self.replies.pop(0) if self.replies else serialized(None)
        if isinstance(reply, (bytes, bytearray)):
            result = AsyncResult()
            result.set(reply)
            return result
        return reply

    def payloads(self, method_name=None):
        return [p for m, p, _ in self.sent if method_name is None or m == method_name]


def serialized(result, error=None):
    """A reply as the proxy's serializer would produce it."""
    return json.dumps({'result': result, 'error': error if error is not None else {}}).encode('utf8')


def point(name, object_type, instance, writable=False, **extra):
    return BacnetPointConfig(volttron_point_name=name, object_type=object_type, instance=instance, writable=writable,
                             units=extra.pop('units', 'degF'), **extra)


@pytest.fixture
def ppm():
    return FakePPM()


@pytest.fixture
def driver_agent():
    agent = mock.Mock()
    agent.tz = 'UTC'
    agent.core.schedule.return_value = 'scheduled'
    return agent


@pytest.fixture
def make_interface(ppm, driver_agent):
    """Build a real BACnet interface whose proxy manager is the FakePPM."""
    def build(points=(), base_topic='campus/building/ahu', **remote):
        remote = {'driver_type': 'bacnet', 'device_address': '10.0.0.5', 'device_id': 7,
                  'local_interface': '10.0.0.1/24', **remote}
        BACnet.default_config = {}
        with mock.patch('volttron.driver.interfaces.bacnet.bacnet.GeventProtocolProxyManager') as manager_class:
            manager_class.get_manager.return_value = ppm
            interface = BACnet(RemoteConfig(**remote), driver_agent=driver_agent)
        for p in points:
            interface.insert_register(interface.create_register(p), base_topic)
        return interface
    return build


@pytest.fixture
def interface(make_interface):
    """Temp (input, COV), Setpoint (analog value, writable at priority 8), Fan (binary value, writable)."""
    iface = make_interface([
        point('Temp', 'analogInput', 1, cov_flag=True),
        point('Setpoint', 'analogValue', 1, writable=True, write_priority=8),
        point('Fan', 'binaryValue', 1, writable=True, units=''),
        point('Modes', 'multiStateValue', 3, array_index=2, property='state-text'),
    ])
    iface.proxy_peer = iface.ppm.peer
    return iface


TOPIC = 'campus/building/ahu/{}'.format
