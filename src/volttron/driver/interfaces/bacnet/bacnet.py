# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2022 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}

import json
import logging

from bacpypes3.primitivedata import ObjectIdentifier
from collections.abc import KeysView
from datetime import datetime, timedelta
from gevent import Timeout
from pydantic import computed_field, Field, field_validator, IPvAnyAddress
from pydantic import computed_field, ConfigDict, Field, field_validator, IPvAnyAddress

# TODO: Make sure these imports work and get rid of noinspection.
# noinspection PyUnresolvedReferences
from protocol_proxy.ipc import ProtocolProxyMessage, ProtocolProxyPeer, callback
# noinspection PyUnresolvedReferences
from protocol_proxy.manager.gevent import GeventProtocolProxyManager

from volttron.client.vip.agent import errors
from volttron.driver.base.config import PointConfig, RemoteConfig
from volttron.driver.base.driver_exceptions import DriverConfigError
from volttron.driver.base.interfaces import BaseInterface, BaseRegister
from volttron.utils.jsonrpc import RemoteError

_log = logging.getLogger(__name__)

COV_UPDATE_BUFFER = 3
BACNET_TYPE_MAPPING = {  # TODO: Update with additional types.
    "multiStateValue": int,
    "multiStateInput": int,
    "multiStateOutput": int,
    "analogValue": float,
    "analogInput": float,
    "analogOutput": float,
    "binaryValue": bool,
    "binaryInput": bool,
    "binaryOutput": bool
}


class BacnetPointConfig(PointConfig):
    array_index: int | None = None
    bacnet_object_type: str = Field(alias='BACnet Object Type')
    property: str = Field(alias='Property', default='present-value')  # TODO: Should be an Enum of BACnet property types.
    index: int = Field(alias='Index')
    cov_flag: bool = Field(default=False, alias='COV Flag')
    write_priority: int | None = Field(default=16, ge=1, le=16, alias='Write Priority')

    @field_validator('write_priority', mode='before')
    @classmethod
    def _normalize_write_priority(cls, v):
        return 16 if v == '' else float(v)


class BacnetRemoteConfig(RemoteConfig):
    model_config = ConfigDict(populate_by_name=True)
    bacnet_network: int = Field(default=0)
    cov_lifetime_configured: float = Field(default=180.0, alias='cov_lifetime')
    device_id: int = Field(ge=0)
    local_device_address: IPvAnyAddress = Field(default='0.0.0.0')  # TODO: Should this be IpvAnyInterface? (i.e., should it allow CIDR specification?)
    max_per_request: int = Field(ge=0, default=24)
    min_priority: int = Field(default=8, ge=1, le=16)
    ping_retry_interval_configured: float = Field(alias='ping_retry_interval', default=5.0)
    proxy_vip_identity: str = Field(alias="proxy_address", default="platform.bacnet_proxy")
    target_address: str = Field(alias="device_address")
    timeout: float = Field(ge=0, default=30.0)
    use_read_multiple: bool = True

    @computed_field
    @property
    def ping_retry_interval(self) -> timedelta:
        return timedelta(seconds=self.ping_retry_interval_configured)

    @ping_retry_interval.setter
    def ping_retry_interval(self, v):
        if isinstance(v, timedelta):
            self.ping_retry_interval_configured = v.total_seconds()

    @computed_field
    @property
    def cov_lifetime(self) -> timedelta:
        return timedelta(seconds=self.cov_lifetime_configured)

    @cov_lifetime.setter
    def cov_lifetime(self, v):
        if isinstance(v, timedelta):
            self.cov_lifetime_configured = v.total_seconds()


class BACnetRegister(BaseRegister):

    def __init__(self,
                 instance_number,
                 object_type,
                 property_name,
                 read_only,
                 point_name,
                 units,
                 description='',
                 priority=None,
                 list_index=None,  # TODO: Should this be renamed "array_index"?
                 is_cov=False):
        super(BACnetRegister, self).__init__("byte",
                                             read_only,
                                             point_name,
                                             units,
                                             description=description)
        self.instance_number = int(instance_number)
        self.object_type = object_type
        self.property = property_name
        self.priority = priority
        self.array_index = list_index
        self.python_type = BACNET_TYPE_MAPPING[object_type]
        self.is_cov = is_cov


class BACnet(BaseInterface):

    REGISTER_CONFIG_CLASS = BacnetPointConfig
    INTERFACE_CONFIG_CLASS = BacnetRemoteConfig

    def __init__(self, config, *args, **kwargs):
        super(BACnet, self).__init__(config, *args, **kwargs)
        self.register_count_divisor = 1

        self.ppm: GeventProtocolProxyManager = GeventProtocolProxyManager.get_manager('bacnet')  # '(BACnetProxy)
        self.proxy_peer: ProtocolProxyPeer = None
        self.scheduled_ping = None

        self.ppm.register_callback(self.receive_cov, 'RECEIVE_COV', provides_response=False)
        self.ppm.start()  # TODO: Does this and/or select_loop spawn need to be in finalize_setup? (If not, keep here.)
        self.driver_agent.core.spawn(self.ppm.select_loop)
        _log.debug('AFTER BACNET INTERFACE INIT')

    @property
    def register_count(self):
        return sum([len(reg_group) for reg_group in self.registers.values()])

    def finalize_setup(self, initial_setup: bool = False):
        # TODO: This will be called after every device is added.  If this is an issue, we would need a different hook.
        #  It could be called on every remote after the end of a setup loop, possibly?
        _log.debug('BACnet finalize_setup called.')
        self.proxy_peer = self.ppm.get_proxy((self.config.local_device_address, self.config.bacnet_network),
                                             local_device_address=self.config.local_device_address)
        _log.debug('BACnet finalize_setup: proxy_peer is: %s', self.proxy_peer)
        if initial_setup is True:
            self.ppm.wait_peer_registered(self.proxy_peer, self.config.timeout, self.ping_target)
        # TODO: Consider adding a self.config.remote_refresh_interval to be scheduled as
        #  a periodic here to ping the target with a WhoIs.
        for topic, register in self.point_map.items():
            if register.is_cov:
                self.establish_cov_subscription(register, topic, self.config.cov_lifetime)

    def create_register(self, register_definition: BacnetPointConfig) -> BACnetRegister:
        if register_definition.write_priority < self.config.min_priority:
            raise DriverConfigError(
                f"{register_definition.volttron_point_name} configured with a priority"
                f" {register_definition.write_priority} which is lower than than minimum {self.config.min_priority}.")

        return BACnetRegister(register_definition.index,
                              register_definition.bacnet_object_type,
                              register_definition.property,
                              register_definition.writable is False,
                              register_definition.volttron_point_name,
                              register_definition.units,
                              description=register_definition.notes,
                              priority=register_definition.write_priority,
                              list_index=register_definition.array_index,
                              is_cov=register_definition.cov_flag)

    def schedule_ping(self):
        if self.scheduled_ping is None:
            now = datetime.now()
            next_try = now + self.config.ping_retry_interval
            self.scheduled_ping = self.driver_agent.core.schedule(next_try, self.ping_target)

    # TODO: Can the ping handling be improved to better keep remotes alive?
    def ping_target(self):
        # Some devices (mostly RemoteStation addresses behind routers) will not be reachable without
        # first establishing the route to the device. Sending a directed WhoIsRequest is will
        # settle that for us when the response comes back.

        pinged = False
        try:
            self.ppm.send(self.proxy_peer,
                         ProtocolProxyMessage(
                             method_name='WHO_IS',
                             payload=json.dumps({
                                 'low_limit': self.config.device_id,
                                 'high_limit': self.config.device_id,
                                 'address': self.config.target_address
                                                }).encode('utf8'),
                            response_expected=False
                         ))
            pinged = True
        # TODO: What exceptions might we really encounter, now, through PPM?
        except errors.Unreachable:
            _log.warning("Unable to reach BACnet proxy.")
        except errors.VIPError:
            _log.warning("Error trying to ping device.")

        self.scheduled_ping = None
        # Schedule retry.
        if not pinged:
            self.schedule_ping()

    def get_point(self, topic: str, on_property: str = None):
        register: BACnetRegister = self.get_register_by_name(topic)
        return self.ppm.send(self.proxy_peer,
                             ProtocolProxyMessage(
                                 method_name='READ_PROPERTY',
                                 payload=json.dumps({
                                     'device_address': self.config.target_address,
                                     'object_identifier': str((register.object_type, register.instance_number)),
                                     'property_identifier': on_property if on_property else register.property,
                                     'property_array_index': register.array_index
                                 }).encode('utf8'),
                                 response_expected=True
                             ))

    def set_point(self, topic, value, priority=None, on_property=None):
        # TODO: support writing from an array.
        register: BACnetRegister = self.get_register_by_name(topic)
        if register.read_only:
            raise IOError("Trying to write to a point configured read only: " + topic)

        if priority is not None and priority < self.config.min_priority:
            raise IOError("Trying to write with a priority lower than the minimum of " +
                          str(self.config.min_priority))

        return self.ppm.send(self.proxy_peer,
                             ProtocolProxyMessage(
                                 method_name='WRITE_PROPERTY',
                                 payload=json.dumps({
                                     'device_address': self.config.target_address,
                                     'object_identifier': str((register.object_type, register.instance_number)),
                                     'property_identifier': on_property if on_property else register.property,
                                     'value': value,
                                     'priority': priority if priority is not None else register.priority,
                                     'property_array_index': register.array_index
                                 }).encode('utf8'),
                                 response_expected=True
                             ))

    @staticmethod
    def _query_fields(reg: BacnetPointConfig):
        return {'object_id': f'{reg.object_type}, {reg.instance_number}',
                'property': reg.property, 'array_index': reg.array_index}

    def get_multiple_points(self, topics: KeysView[str], **kwargs) -> (dict, dict):
        # TODO: support reading from an array.
        # TODO: Manner of packing and unpacking this request needs to be rethought.
        point_map = {t: self._query_fields(self.point_map[t]) for t in topics if t in self.point_map}
        while True:
            try:
                # TODO:
                #  Need to honor self.config.max_per_request, and probably detect it.
                #  Need to loop if not self.config.use_read_multiple --- Probably want to use batchread!
                response = self.ppm.send(self.proxy_peer,
                                     ProtocolProxyMessage(
                                         method_name='BATCH_READ',
                                         payload=json.dumps({
                                             'device_address': self.config.target_address,
                                             'read_specifications': point_map
                                         }).encode('utf8'),
                                         response_expected=True
                                     )).get(timeout=self.config.timeout).decode('utf8')
                _log.debug(f"RESPONSE IS: {response}")
                response = json.loads(response)
                result_dict = response.get('result', {})
                error_dict = response.get('error', {})
            # TODO: The error handling still reflects the BACnetProxyAgent. How do we do this correctly?
            except Timeout as e:
                _log.warning(f'Request timed out polling: {self.config.target_address}')
            except RemoteError as e:
                if "segmentationNotSupported" in e.message:
                    if self.config.max_per_request <= 1:
                        _log.error(
                            "Receiving a segmentationNotSupported error with 'max_per_request' setting of 1."
                        )
                        raise
                    self.register_count_divisor += 1
                    self.config.max_per_request = max(
                        int(self.register_count / self.register_count_divisor)+1, 1)
                    _log.info("Device requires a lower max_per_request setting. Trying: " +
                              str(self.config.max_per_request))
                    continue
                elif e.message.endswith("rejected the request: 9") and self.config.use_read_multiple:
                    _log.info(
                        "Device rejected request with 'unrecognized-service' error, attempting to access with use_read_multiple false"
                    )
                    self.config.use_read_multiple = False
                    continue
                else:
                    raise
            except errors.Unreachable:
                # If the Proxy is not running bail.
                _log.warning("Unable to reach BACnet proxy.")
                self.schedule_ping()
                raise
            _log.debug(f'RECEIVED ERROR: {error_dict}')
            _log.debug(f'RECEIVED RESULT: {result_dict}')
            return result_dict, error_dict
        # return ret_dict, {}  # TODO: Need error dict, if possible.

    def set_multiple_points(self, topics_values, **kwargs):
        # TODO: Implement SET_PROPERTY_MULTIPLE in BACnetProtocolProxy
        super(BACnet, self).set_multiple_points(topics_values, **kwargs)

    def revert_all(self, priority=None):
        """
        Revert entrire device to it's default state
        """
        # TODO: Add multipoint write support
        write_registers = self.get_registers_by_type("byte", False)
        for register in write_registers:
            self.revert_point(register.point_name, priority=priority)

    def revert_point(self, topic, priority=None):
        """
        Revert point to it's default state
        """
        # TODO: Should this have a way to set the revert value to something other than None (e.g., for UCSD's lights)?
        self.set_point(topic, None, priority=priority)

    def establish_cov_subscription(self, register, topic, lifetime):
        """
        Asks the BACnet Proxy to establish a COV subscription for the point via RPC.
        If lifetime is specified, the subscription will be renewed by the proxy on
         that interval, else the subscription will last indefinitely.
        """
        self.ppm.send(
            self.proxy_peer,
            ProtocolProxyMessage(
                method_name='SETUP_COV',
                payload=json.dumps({
                    'subscription_key': topic,
                    'device_address': self.config.target_address,
                    'monitored_object_identifier': ':'.join([str(register.object_type), str(register.instance_number)]),
                    'property_identifier': register.property,
                    'issue_confirmed_notifications': True,  # TODO: Should this be exposed somewhere in case only unconfirmed notifications are supported?
                    'lifetime': lifetime.total_seconds()
                }).encode('utf8'),
                response_expected=False
                ))

    @callback
    def receive_cov(self, _, raw_message: bytes):
        # TODO: Validation and error handling.
        _log.debug('@@@@@@@@@ IN RECEIVE_COV')
        message = json.loads(raw_message.decode('utf8'))
        _log.debug(f'@@@@@@@@@ Received COV message: {message}')
        self.driver_agent.publish_push(message)

    @classmethod
    def unique_remote_id(cls, config_name: str, config: BacnetRemoteConfig) -> tuple:
        # TODO: This should probably incorporate information which currently belongs to the BACnet Proxy Agent.
        return config.target_address, config.device_id