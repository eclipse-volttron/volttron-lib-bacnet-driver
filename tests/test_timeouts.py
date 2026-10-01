"""Device, reply and registration waits are separate settings, and the proxy is launched with the device settings."""
import pytest

from gevent.event import AsyncResult

from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetRemoteConfig, BACnetRegister


def config(**kwargs):
    return BacnetRemoteConfig(driver_type='bacnet', device_address='10.0.0.5', device_id=7, **kwargs)


def test_defaults_are_ordered_device_then_proxy_then_driver():
    cfg = config()
    assert cfg.apdu_cycle == 12.0                       # 3 s * (3 + 1)
    assert cfg.batch_read_timeout == 60.0               # 24 points -> 3 chunks, + 2 setup cycles
    assert cfg.reply_timeout == 65.0                    # proxy limit + margin
    assert cfg.registration_timeout == 30.0


@pytest.mark.parametrize('kwargs, batch, reply', [
    ({'max_per_request': 100}, 144.0, 149.0),           # 10 chunks + 2 setup, 12 s each
    ({'max_per_request': 0}, 144.0, 149.0),             # unlimited: assume 10 chunks
    ({'apdu_timeout': 1.0, 'apdu_retries': 0}, 30.0, 35.0),   # floor
    ({'timeout': 20.0}, 60.0, 20.0),                    # explicit reply wait is respected as configured
])
def test_derivation(kwargs, batch, reply):
    cfg = config(**kwargs)
    assert cfg.batch_read_timeout == batch and cfg.reply_timeout == reply


def test_proxy_launch_options():
    cfg = config(apdu_timeout=2.0, apdu_retries=1, local_interface='10.0.0.1/24', bacnet_port=47809)
    assert cfg.proxy_launch_options() == {'local_interface': '10.0.0.1/24', 'bacnet_port': 1, 'apdu_timeout': 2.0,
                                          'apdu_retries': 1, 'batch_read_timeout': 30.0}


def test_interface_uses_reply_and_registration_timeouts():
    interface = BACnet.__new__(BACnet)
    interface.config = config(apdu_timeout=2.0, apdu_retries=1, registration_timeout=9.0)
    interface.point_map = {'p': BACnetRegister(1, 'analogInput', 'present-value', True, 'p', 'degF')}
    interface.proxy_peer = object()
    interface.ping_target = lambda: None
    interface.setup_time_synchronization = lambda: None
    waits = []

    class RecordingResult(AsyncResult):
        def get(self, block=True, timeout=None):
            waits.append(('reply', timeout))
            return super().get(block, timeout)

    class FakePPM:
        def get_proxy(self, key, **kwargs):
            waits.append(('launch', kwargs))
            return interface.proxy_peer

        def wait_peer_registered(self, peer, timeout, func=None, *a, **kw):
            waits.append(('register', timeout))

        def send(self, peer, message):
            result = RecordingResult()
            result.set(b'{"result": 1.5, "error": {}}')
            return result
    interface.ppm = FakePPM()
    interface.finalize_setup(initial_setup=True)
    assert interface.get_point('p') == 1.5
    assert ('register', 9.0) in waits
    assert ('reply', interface.config.reply_timeout) in waits and interface.config.reply_timeout == 35.0
    launch = next(v for k, v in waits if k == 'launch')
    assert launch['apdu_timeout'] == 2.0 and launch['apdu_retries'] == 1 and launch['batch_read_timeout'] == 30.0
