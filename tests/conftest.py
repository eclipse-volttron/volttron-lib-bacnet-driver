"""Shared fixtures for the BACnet interface tests. These run without a platform, a proxy, or a device."""
from pathlib import Path
from unittest import mock

import pytest

from volttron.driver.base.testing import FakePPM, build_interface, serialized      # noqa: F401  (re-exported for tests)
from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetPointConfig

TESTS_DIR = Path(__file__).parent
TMP_DIR = TESTS_DIR / "tmp"
FIXTURES_DIR = TESTS_DIR / "fixtures"


def point(name, object_type, instance, writable=False, **extra):
    return BacnetPointConfig(volttron_point_name=name, object_type=object_type, instance=instance, writable=writable,
                             units=extra.pop('units', 'degF'), **extra)


@pytest.fixture
def ppm():
    return FakePPM(default_reply=serialized(None))          # a BACnet write acknowledgement carries no value


@pytest.fixture
def driver_agent():
    agent = mock.Mock(equipment_model=None)
    agent.tz = 'UTC'
    agent.core.schedule.return_value = 'scheduled'
    return agent


@pytest.fixture
def make_interface(ppm, driver_agent):
    """Build a real BACnet interface whose proxy manager is the FakePPM."""
    def build(points=(), base_topic='campus/building/ahu', **remote):
        remote = {'driver_type': 'bacnet', 'device_address': '10.0.0.5', 'device_id': 7,
                  'local_interface': '10.0.0.1/24', **remote}
        return build_interface(BACnet, remote, driver_agent=driver_agent, ppm=ppm, points=points, base_topic=base_topic)
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
