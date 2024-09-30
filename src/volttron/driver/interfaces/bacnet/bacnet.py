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

import logging

from collections.abc import KeysView
from datetime import datetime, timedelta
from pydantic import computed_field, Field, field_validator

from volttron.client.vip.agent import errors
from volttron.driver.base.config import PointConfig, RemoteConfig
from volttron.driver.base.driver_exceptions import DriverConfigError
from volttron.driver.base.interfaces import BaseInterface, BaseRegister
from volttron.utils.jsonrpc import RemoteError

_log = logging.getLogger(__name__)

COV_UPDATE_BUFFER = 3
BACNET_TYPE_MAPPING = {
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
    array_index: int | None = None # TODO: Is this the correct default for this? What is it?
    bacnet_object_type: str = Field(alias='BACnet Object Type')
    property: str = Field(alias='Property')  # TODO: Should be an Enum of BACnet property types.
    index: int = Field(alias='Index')
    cov_flag: bool = Field(default=False, alias='COV Flag')
    write_priority: int | None = Field(default=16, ge=1, le=16, alias='Write Priority')

    @field_validator('write_priority', mode='before')
    @classmethod
    def _normalize_write_priority(cls, v):
        return 16 if v == '' else float(v)


class BacnetRemoteConfig(RemoteConfig):
    cov_lifetime_configured: float = Field(default=180.0, alias='cov_lifetime')
    device_id: int = Field(ge=0)
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
                 list_index=None,
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
        self.index = list_index
        self.python_type = BACNET_TYPE_MAPPING[object_type]
        self.is_cov = is_cov


class BACnet(BaseInterface):

    REGISTER_CONFIG_CLASS = BacnetPointConfig
    INTERFACE_CONFIG_CLASS = BacnetRemoteConfig

    def __init__(self, config, **kwargs):
        super(BACnet, self).__init__(config, **kwargs)
        self.register_count_divisor = 1

        self.scheduled_ping = None

    @property
    def register_count(self):
        return len(self.registers)

    def finalize_setup(self, initial_setup: bool = False):
        if initial_setup is True:
            self.ping_target()

    def create_register(self, register_definition: BacnetPointConfig) -> BACnetRegister:
        if register_definition.write_priority < self.config.min_priority:
            raise DriverConfigError(
                f"{register_definition.volttron_point_name} configured with a priority"
                f" {register_definition.write_priority} which is lower than than minimum {self.config.min_priority}.")

        return BACnetRegister(register_definition.index,
                              register_definition.bacnet_object_type,
                              register_definition.property,
                              register_definition.writable is True,
                              register_definition.volttron_point_name,
                              register_definition.units,
                              description=register_definition.notes,
                              priority=register_definition.write_priority,
                              list_index=register_definition.array_index,
                              is_cov=register_definition.cov_flag)

    def insert_register(self, register: BACnetRegister, base_topic: str):
        super(BACnet, self).insert_register(register, base_topic)
        if register.is_cov:
            self.establish_cov_subscription(register.point_name, self.config.cov_lifetime, True)

    def schedule_ping(self):
        if self.scheduled_ping is None:
            now = datetime.now()
            next_try = now + self.config.ping_retry_interval
            self.scheduled_ping = self.core.schedule(next_try, self.ping_target)

    def ping_target(self):
        # Some devices (mostly RemoteStation addresses behind routers) will not be reachable without
        # first establishing the route to the device. Sending a directed WhoIsRequest is will
        # settle that for us when the response comes back.

        pinged = False
        try:
            self.vip.rpc.call(self.config.proxy_vip_identity, 'ping_device', self.config.target_address,
                              self.config.device_id).get(timeout=self.config.timeout)
            pinged = True
        except errors.Unreachable:
            _log.warning("Unable to reach BACnet proxy.")

        except errors.VIPError:
            _log.warning("Error trying to ping device.")

        self.scheduled_ping = None
        # Schedule retry.
        if not pinged:
            self.schedule_ping()

    def get_point(self, topic, get_priority_array=False):
        register: BACnetRegister = self.get_register_by_name(topic)
        property_name = "priorityArray" if get_priority_array else register.property
        register_index = None if get_priority_array else register.index
        result = self.vip.rpc.call(self.config.proxy_vip_identity, 'read_property', self.config.target_address,
                                   register.object_type, register.instance_number, property_name,
                                   register_index).get(timeout=self.config.timeout)
        return result

    def set_point(self, topic, value, priority=None):
        # TODO: support writing from an array.
        register: BACnetRegister = self.get_register_by_name(topic)
        if register.read_only:
            raise IOError("Trying to write to a point configured read only: " + topic)

        if priority is not None and priority < self.config.min_priority:
            raise IOError("Trying to write with a priority lower than the minimum of " +
                          str(self.config.min_priority))

        # We've already validated the register priority against the min priority.
        args = [
            self.config.target_address, value, register.object_type, register.instance_number,
            register.property, priority if priority is not None else register.priority,
            register.index
        ]
        result = self.vip.rpc.call(self.config.proxy_vip_identity, 'write_property',
                                   *args).get(timeout=self.config.timeout)
        return result

    def get_multiple_points(self, topics: list[str], **kwargs) -> (dict, dict):
        # TODO: support reading from an array.
        point_map = {t: [r.object_type, r.instance_number, r.property, r.index]
                     for t, r in self.point_map.items() if t in topics}
        while True:
            try:
                result = self.vip.rpc.call(self.config.proxy_vip_identity, 'read_properties',
                                           self.config.target_address, point_map, self.config.max_per_request,
                                           self.config.use_read_multiple).get(timeout=self.config.timeout)
            except RemoteError as e:
                if "segmentationNotSupported" in e.message:
                    if self.config.max_per_request <= 1:
                        _log.error(
                            "Receiving a segmentationNotSupported error with 'max_per_request' setting of 1."
                        )
                        raise
                    self.register_count_divisor += 1
                    self.config.max_per_request = max(
                        int(self.register_count / self.register_count_divisor), 1)
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
            else:
                break

        return result, {}  # TODO: Need error dict, if possible.

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
        self.set_point(topic, None, priority=priority)

    def establish_cov_subscription(self, topic, lifetime, renew=False):
        """
        Asks the BACnet proxy to establish a COV subscription for the point via RPC.
        If lifetime is specified, the subscription will live for that period, else the
        subscription will last indefinitely. Default period of 3 minutes. If renew is
        True, the the core scheduler will call this method again near the expiration
        of the subscription.
        """
        register: BACnetRegister = self.get_register_by_name(topic)
        try:
            self.vip.rpc.call(self.config.proxy_vip_identity,
                              'create_cov_subscription',
                              self.config.target_address,
                              self.unique_remote_id('', self.config),
                              topic,
                              register.object_type,
                              register.instance_number,
                              lifetime=lifetime)
        except errors.Unreachable:
            _log.warning(
                "Unable to establish a subscription via the bacnet proxy as it was unreachable.")
        # Schedule COV resubscribe
        if renew and (lifetime > COV_UPDATE_BUFFER):
            now = datetime.now()
            next_sub_update = now + timedelta(seconds=(lifetime - COV_UPDATE_BUFFER))
            self.core.schedule(next_sub_update, self.establish_cov_subscription, topic,
                               lifetime, renew)

    @classmethod
    def unique_remote_id(cls, config_name: str, config: BacnetRemoteConfig) -> tuple:
        # TODO: This should probably incorporate information which currently belongs to the BACnet Proxy Agent.
        return config.target_address, config.device_id