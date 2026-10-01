"""End-to-end harness: real interface -> real GeventProtocolProxyManager -> real BACnet proxy subprocess -> simulated
BACnet device (bacpypes3). Run by test_end_to_end.py in its own process (gevent's monkey patching must happen before
other imports). Exit status is non-zero if any check fails.

Usage: e2e_harness.py [DEVICE_PORT] [PROXY_PORT_OFFSET]
"""
from gevent import monkey; monkey.patch_all()
import gevent, logging, os, subprocess, sys, time
from pathlib import Path
from unittest import mock
logging.basicConfig(filename=os.environ.get('BACNET_E2E_LOG', '/tmp/bacnet_e2e.log'), level=logging.DEBUG,
                    format='%(asctime)s %(name)s %(levelname)s %(message)s')
from volttron.driver.base.config import RemoteConfig
from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetPointConfig

DEVICE_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 47821
PROXY_OFFSET = int(sys.argv[2]) if len(sys.argv) > 2 else 3          # proxy binds 47808 + offset
DEVICE_ID = 1001
DEVICE = f'127.0.0.1:{DEVICE_PORT}'
T = 'campus/b/ahu/{}'.format

sim = subprocess.Popen([sys.executable, str(Path(__file__).with_name('bacnet_simulator.py')), str(DEVICE_PORT), str(DEVICE_ID)],
                       stdout=subprocess.PIPE, text=True)
for _ in range(100):
    line = sim.stdout.readline()
    if 'simulator ready' in line:
        break
else:
    sys.exit('simulator did not start')

checks = []
def check(name, cond, detail=''):
    checks.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  {detail}' if detail else ''), flush=True)

class Core:
    spawn = staticmethod(gevent.spawn)
    def schedule(self, when, func, *args):
        return gevent.spawn_later(max(0.0, (when - __import__('datetime').datetime.now()).total_seconds()), func, *args)
class Agent:
    core = Core()
    tz = 'UTC'
    def __init__(self): self.pushed = []
    def publish_push(self, result): self.pushed.append(result)

BACnet.default_config = {}
agent = Agent()
try:
    iface = BACnet(RemoteConfig(driver_type='bacnet', device_address=DEVICE, device_id=DEVICE_ID, local_interface='127.0.0.1/32',
                                bacnet_port=PROXY_OFFSET, apdu_timeout=2.0, apdu_retries=1, registration_timeout=20,
                                min_priority=8), driver_agent=agent)
    rows = [dict(volttron_point_name='Temp', object_type='analogInput', instance=1, units='degF', writable=False, cov_flag=True),
            dict(volttron_point_name='Setpoint', object_type='analogValue', instance=1, units='degF', writable=True, write_priority=8),
            dict(volttron_point_name='Fan', object_type='binaryValue', instance=1, units='', writable=True, write_priority=8),
            dict(volttron_point_name='Missing', object_type='analogValue', instance=99, units='', writable=True, write_priority=8)]
    for row in rows:
        iface.insert_register(iface.create_register(BacnetPointConfig(**row)), 'campus/b/ahu')
    t0 = time.time(); iface.finalize_setup(initial_setup=True); t_setup = time.time() - t0
    check('proxy launched and registered', iface.proxy_peer is not None and iface.proxy_peer.socket_params is not None, f'{t_setup:.1f}s')

    t0 = time.time(); results, errors = iface.get_multiple_points(list(iface.point_map)); t_poll = time.time() - t0
    short = lambda d: {k.split('/')[-1]: v for k, v in d.items()}
    print('  results:', short(results)); print('  errors:', short(errors))
    check('poll: analog input and commandable values read', results.get(T('Temp')) is not None and results.get(T('Setpoint')) == 72.5)
    check('poll: binary value read', results.get(T('Fan')) in (0, False, 'inactive'), str(results.get(T('Fan'))))
    check('poll: unknown object isolated to its point', T('Missing') in errors and T('Missing') not in results and len(errors) == 1 and 'unknown-object' in str(errors.get(T('Missing'))), str(errors.get(T('Missing')))[:100])
    check('poll timing', t_poll < 10, f'{t_poll:.2f}s')

    check('get_point', iface.get_point(T('Setpoint')) == 72.5)
    check('set_point acknowledged', iface.set_point(T('Setpoint'), 65.0) is None)
    check('set_point visible on device', iface.get_point(T('Setpoint')) == 65.0)
    check('set binary', iface.set_point(T('Fan'), 'active') is None and iface.get_point(T('Fan')) in (1, True, 'active'), str(iface.get_point(T('Fan'))))
    try:
        iface.set_point(T('Temp'), 1.0); check('read-only write rejected', False)
    except Exception as ex:
        check('read-only write rejected', 'read only' in str(ex))
    try:
        iface.set_point(T('Missing'), 1.0); check('write to unknown object reports error', False)
    except Exception as ex:
        check('write to unknown object reports error', 'unknown-object' in str(ex), str(ex)[:100])

    iface.revert_point(T('Setpoint'))
    check('revert_point relinquishes to default', iface.get_point(T('Setpoint')) == 72.5)
    iface.set_point(T('Setpoint'), 60.0)
    iface.revert_all()
    check('revert_all relinquishes all writable points', iface.get_point(T('Setpoint')) == 72.5 and iface.get_point(T('Fan')) in (0, False, 'inactive'))

    deadline = time.time() + 12
    while time.time() < deadline and not agent.pushed:
        gevent.sleep(0.5)
    check('COV notifications pushed for subscribed point', any(T('Temp') in p for p in agent.pushed), f'{len(agent.pushed)} pushes')
finally:
    ppm = getattr(iface, 'ppm', None) if 'iface' in dir() else None
    for peer in list(getattr(ppm, 'peers', {}).values()) if ppm else []:
        proc = getattr(peer, 'process', None)
        if proc:
            proc.terminate()
    sim.terminate()
failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed' + (f'; FAILED: {failed}' if failed else ''), flush=True)
sys.exit(1 if failed else 0)
