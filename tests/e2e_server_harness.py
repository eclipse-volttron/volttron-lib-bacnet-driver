"""End-to-end harness for the server role: one real proxy process serves a BACnet/IP device for an interface in the
server role, and a second interface in the client role reaches it through the same proxy. Run by test_end_to_end.py in
its own process (gevent's monkey patching must happen before other imports). Exit status is non-zero if any check fails.

Usage: e2e_server_harness.py [SERVED_UDP_PORT] [PROXY_PORT_OFFSET]
"""
from gevent import monkey; monkey.patch_all()
import gevent, logging, os, sys, time
logging.basicConfig(filename=os.environ.get('BACNET_E2E_LOG', '/tmp/bacnet_e2e_server.log'), level=logging.DEBUG,
                    format='%(asctime)s %(name)s %(levelname)s %(message)s')
from volttron.driver.base.config import RemoteConfig
from volttron.driver.interfaces.bacnet.bacnet import BACnet, BacnetPointConfig

SERVED_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 47841
PROXY_OFFSET = int(sys.argv[2]) if len(sys.argv) > 2 else 14
DEVICE_ID = 4242
S = 'campus/b/served/{}'.format
C = 'campus/b/client/{}'.format

checks = []
def check(name, cond, detail=''):
    checks.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name + (f'  {detail}' if detail else ''), flush=True)
def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(): return True
        gevent.sleep(0.1)
    return predicate()


class Core:
    spawn = staticmethod(gevent.spawn)
    def schedule(self, when, func, *args):
        return gevent.spawn_later(max(0.0, (when - __import__('datetime').datetime.now()).total_seconds()), func, *args)
class Agent:
    core = Core()
    tz = 'UTC'
    equipment_model = None
    def __init__(self): self.pushed = []
    def publish_push(self, result): self.pushed.append(dict(result))
    def flat(self):
        out = {}
        for values in self.pushed: out.update(values)
        return out
    def latest(self, topic):
        for values in reversed(self.pushed):
            if topic in values: return values[topic]
        return None


BACnet.default_config = {}
served = client = None
served_agent, client_agent = Agent(), Agent()
try:
    served = BACnet(RemoteConfig(driver_type='bacnet', driver_role='server', local_interface='127.0.0.1/32', bacnet_port=PROXY_OFFSET,
                                 served_port=SERVED_PORT, device_id=DEVICE_ID, object_name='served-rtu', apdu_timeout=2.0, apdu_retries=1,
                                 registration_timeout=20), driver_agent=served_agent)
    rows = [dict(volttron_point_name='Temp', object_type='analogInput', instance=1, units='degreesFahrenheit', default_value='68.25', cov_increment=0.1),
            dict(volttron_point_name='Setpoint', object_type='analogValue', instance=1, units='degreesFahrenheit', default_value='72.5'),
            dict(volttron_point_name='Fan', object_type='binaryValue', instance=1, units='', default_value='inactive'),
            dict(volttron_point_name='Mode', object_type='multiStateValue', instance=1, units='', number_of_states=3, default_value='2', remote_writable=False)]
    rows = served.prepare_registry_config(rows, served.config)
    check('served rows default to data_source server and writable', all(r['data_source'] == 'server' and r['writable'] is True for r in rows))
    for row in rows:
        served.insert_register(served.create_register(BacnetPointConfig(**row)), 'campus/b/served')
    t0 = time.time(); served.finalize_setup(initial_setup=True); t_setup = time.time() - t0
    check('proxy launched and device served', served.proxy_peer is not None and served.proxy_peer.socket_params is not None, f'{t_setup:.1f}s')
    check('served table pushed on registration', wait_for(lambda: S('Mode') in served_agent.flat())
          and served_agent.flat() == {S('Temp'): 68.25, S('Setpoint'): 72.5, S('Fan'): False, S('Mode'): 2}, str(served_agent.flat()))

    client = BACnet(RemoteConfig(driver_type='bacnet', device_address=f'127.0.0.1:{SERVED_PORT}', device_id=DEVICE_ID, local_interface='127.0.0.1/32',
                                 bacnet_port=PROXY_OFFSET, apdu_timeout=2.0, apdu_retries=1, registration_timeout=20, min_priority=8), driver_agent=client_agent)
    for row in [dict(volttron_point_name='Temp', object_type='analogInput', instance=1, units='degF', writable=False, cov_flag=True),
                dict(volttron_point_name='Setpoint', object_type='analogValue', instance=1, units='degF', writable=True, write_priority=8),
                dict(volttron_point_name='Fan', object_type='binaryValue', instance=1, units='', writable=True, write_priority=8),
                dict(volttron_point_name='Mode', object_type='multiStateValue', instance=1, units='', writable=True, write_priority=8)]:
        client.insert_register(client.create_register(BacnetPointConfig(**row)), 'campus/b/client')
    client.finalize_setup(initial_setup=True)
    check('client uses the same proxy', client.proxy_peer is served.proxy_peer and client.proxy_peer is not None)

    results, errors = client.get_multiple_points(list(client.point_map))
    print('  client poll:', {k.split('/')[-1]: v for k, v in results.items()}, errors, flush=True)
    check('client reads the served starting values', results.get(C('Temp')) == 68.25 and results.get(C('Setpoint')) == 72.5
          and results.get(C('Fan')) in (0, False, 'inactive') and results.get(C('Mode')) == 2 and errors == {}, str(errors))

    served_agent.pushed.clear()
    r, e = served.set_multiple_points([(S('Temp'), 70.5), (S('Setpoint'), 71.0), (S('Fan'), True)])
    check('platform writes served objects', r == {S('Temp'): 70.5, S('Setpoint'): 71.0, S('Fan'): True} and e == {}, str(e))
    check('served writes are reflected at once', served_agent.flat() == {S('Temp'): 70.5, S('Setpoint'): 71.0, S('Fan'): True}, str(served_agent.flat()))
    check('client sees the platform write', client.get_point(C('Temp')) == 70.5 and client.get_point(C('Setpoint')) == 71.0
          and client.get_point(C('Fan')) in (1, True, 'active'))
    check('COV subscription delivers the platform write', wait_for(lambda: client_agent.latest(C('Temp')) == 70.5, 12), str(client_agent.pushed[-2:]))

    served_agent.pushed.clear()
    check('client writes a commandable object', client.set_point(C('Setpoint'), 65.0) is None and client.set_point(C('Fan'), 'inactive') is None)
    check('peer writes are pushed to the served interface', wait_for(lambda: served_agent.flat() == {S('Setpoint'): 65.0, S('Fan'): False}), str(served_agent.flat()))
    check('served interface reads the peer write', served.get_point(S('Setpoint')) == 65.0 and served.get_point(S('Fan')) is False)
    try:
        client.set_point(C('Mode'), 3); check('write to a point that is not remote-writable is refused', False)
    except Exception as ex:
        check('write to a point that is not remote-writable is refused', 'write-access-denied' in str(ex) or 'writeAccessDenied' in str(ex), str(ex)[:120])
    check('relinquishing returns the platform value', client.revert_point(C('Setpoint')) is None and wait_for(lambda: served_agent.latest(S('Setpoint')) == 71.0)
          and client.get_point(C('Setpoint')) == 71.0, str(served_agent.pushed[-1:]))

    served.revert_point(S('Temp'))
    check('revert_point on the served device restores the starting value', served.get_point(S('Temp')) == 68.25 and client.get_point(C('Temp')) == 68.25)

    # A proxy crash: the manager relaunches it, both interfaces register again, and the served values come back from
    # the platform's memory (the tree values sent with the re-registration) rather than from the starting values.
    class Tree:
        def __init__(self, agent): self.agent = agent
        def get_node(self, topic):
            flat = self.agent.flat()
            return type('Node', (), {'last_value': flat[topic], 'last_updated': True})() if topic in flat else None
    served_agent.pushed.clear()
    served_agent.pushed.append({S('Temp'): 68.25, S('Setpoint'): 71.0, S('Fan'): False, S('Mode'): 2})
    served.set_point(S('Temp'), 66.0)                                                 # now in the tree as 66.0
    served.driver_agent.equipment_model = Tree(served_agent)
    old_peer = served.proxy_peer
    old_peer.process.terminate()
    relaunched = wait_for(lambda: served.proxy_peer is not None and served.proxy_peer is not old_peer
                          and served.proxy_peer.socket_params is not None and client.proxy_peer is served.proxy_peer, 30)
    check('proxy relaunched after a crash and both remotes set up again', relaunched)
    def client_reads():
        try:
            return client.get_point(C('Temp')) is not None
        except Exception:
            return False
    wait_for(client_reads, 20)
    check('served values survive the proxy restart from the platform memory', client.get_point(C('Temp')) == 66.0 and client.get_point(C('Setpoint')) == 71.0)
    served.driver_agent.equipment_model = None
finally:
    for iface in (served, client):
        if iface is not None and getattr(iface, 'ppm', None) is not None:
            for peer in list(getattr(iface.ppm, 'peers', {}).values()):
                proc = getattr(peer, 'process', None)
                if proc: proc.terminate()
failed = [n for n, ok in checks if not ok]
print(f'\n{len(checks) - len(failed)}/{len(checks)} passed' + (f'; FAILED: {failed}' if failed else ''), flush=True)
sys.exit(1 if failed else 0)
