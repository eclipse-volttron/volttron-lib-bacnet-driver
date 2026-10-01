"""Simulated BACnet/IP device with known values. Usage: bacnet_simulator.py PORT DEVICE_ID

Objects: analog-input,1 'Temp' (68.25, ramps by 0.5 every 2 s to drive COV), analog-value,1 'Setpoint'
(commandable, relinquish default 72.5), binary-value,1 'Fan' (commandable, relinquish default inactive).
"""
import asyncio
import sys

from bacpypes3.app import Application
from bacpypes3.local.analog import AnalogInputObject, AnalogValueObject
from bacpypes3.local.binary import BinaryValueObject
from bacpypes3.local.cmd import Commandable
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject


class CommandableAnalogValue(Commandable, AnalogValueObject):
    pass


class CommandableBinaryValue(Commandable, BinaryValueObject):
    pass


async def main(port: int, device_id: int):
    device = DeviceObject(objectIdentifier=('device', device_id), objectName='simulated-device')
    network_port = NetworkPortObject(f'127.0.0.1/32:{port}', objectIdentifier=('network-port', 1), objectName='np',
                                     networkNumber=0, networkNumberQuality='configured')
    temp = AnalogInputObject(objectIdentifier=('analog-input', 1), objectName='Temp', presentValue=68.25,
                             units='degreesFahrenheit', covIncrement=0.1)
    setpoint = CommandableAnalogValue(objectIdentifier=('analog-value', 1), objectName='Setpoint', presentValue=72.5,
                                      units='degreesFahrenheit', relinquishDefault=72.5)
    fan = CommandableBinaryValue(objectIdentifier=('binary-value', 1), objectName='Fan', presentValue='inactive',
                                 relinquishDefault='inactive')
    app = Application.from_object_list([device, network_port, temp, setpoint, fan])
    print(f'simulator ready on 127.0.0.1:{port} as device {device_id}', flush=True)
    try:
        while True:
            await asyncio.sleep(2)
            temp.presentValue = round(float(temp.presentValue) + 0.5, 2)
    finally:
        app.close()


if __name__ == '__main__':
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
