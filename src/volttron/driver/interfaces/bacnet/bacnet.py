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

from math import ceil

from collections.abc import Iterable, KeysView
from datetime import datetime, timedelta
from gevent import Timeout
from gevent.event import AsyncResult
from pydantic import AliasChoices, computed_field, Field, IPvAnyInterface
from typing import ClassVar, Annotated, Any, cast

from protocol_proxy.ipc import ProtocolProxyMessage, ProtocolProxyPeer, callback
from protocol_proxy.manager.gevent import GeventProtocolProxyManager

from volttron.driver.base.config import empty_str_is, PointConfig, RemoteConfig
from volttron.driver.base.driver_exceptions import DriverConfigError
from volttron.driver.base.interfaces import BaseInterface, BaseRegister, DriverInterfaceError

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
    array_index: Annotated[int | None, empty_str_is(None)] = None
    object_type: str = Field(validation_alias=AliasChoices('object_type', 'Object Type','BACnet Object Type', 'bacnet_object_type'))
    property: Annotated[str, empty_str_is('present-value')] = Field(alias='Property', default='present-value')  # TODO: Should be an Enum of BACnet property types.
    instance: int = Field(validation_alias=AliasChoices('instance','Instance', 'Index', 'index'))
    cov_flag: Annotated[bool, empty_str_is(False)] = Field(default=False, alias='COV Flag')
    write_priority: Annotated[int, empty_str_is(16)] = Field(default=16, ge=1, le=16, alias='Write Priority')


class BacnetRemoteConfig(RemoteConfig):
    # TODO: Confirm this is not needed now that it is added to superclass: model_config = ConfigDict(populate_by_name=True)
    bacnet_port_configured: int = Field(default=0, alias='bacnet_port')
    cov_lifetime_configured: float = Field(default=180.0, alias='cov_lifetime')  # TODO: Can this by by point instead?
    device_id: int = Field(ge=0)
    local_interface: IPvAnyInterface = Field(default='0.0.0.0/32')  # TODO: We should attempt to discover the interface.
    max_per_request: int = Field(ge=0, default=24)
    min_priority: int = Field(default=8, ge=1, le=16)
    ping_retry_interval_configured: float = Field(alias='ping_retry_interval', default=5.0)
    time_synchronization_seconds: float | None = Field(default=None, ge=0, alias='time_synchronization_interval')
    target_address: str = Field(alias="device_address")
    # Device-level: how long the proxy waits for this device to answer one request, and how often it retries.
    # These configure the shared proxy process for this local interface; the first device to launch it wins.
    apdu_timeout: float = Field(default=3.0, gt=0)
    apdu_retries: int = Field(default=3, ge=0)
    # Reply wait: how long to wait for the proxy's answer to a request. None derives it from the APDU settings and
    # max_per_request so that it always outlasts the proxy's own BATCH_READ limit.
    timeout: float | None = Field(default=None, ge=0)
    # How long to wait for the proxy process to start and register.
    registration_timeout: float = Field(default=30.0, gt=0)
    use_read_multiple: bool = True

    RPM_CHUNK_SIZE: ClassVar[int] = 10           # BatchRead's ReadPropertyMultiple chunk size
    SETUP_CYCLES: ClassVar[int] = 2              # Who-Is + protocol-services-supported on first contact
    REPLY_MARGIN: ClassVar[float] = 5.0

    @property
    def apdu_cycle(self) -> float:
        """Worst-case seconds one request spends on a silent device."""
        return self.apdu_timeout * (self.apdu_retries + 1)

    @property
    def batch_read_timeout(self) -> float:
        """Seconds the proxy should allow one BATCH_READ of up to max_per_request points to a silent device."""
        chunks = ceil(self.max_per_request / self.RPM_CHUNK_SIZE) if self.max_per_request > 0 else 10
        return max(30.0, self.apdu_cycle * (chunks + self.SETUP_CYCLES))

    @property
    def reply_timeout(self) -> float:
        """Driver-side wait for the proxy's reply; strictly longer than the proxy's batch limit."""
        return self.timeout if self.timeout is not None else self.batch_read_timeout + self.REPLY_MARGIN

    def proxy_launch_options(self) -> dict:
        return {'local_interface': str(self.local_interface), 'bacnet_port': self.bacnet_port,
                'apdu_timeout': self.apdu_timeout, 'apdu_retries': self.apdu_retries,
                'batch_read_timeout': self.batch_read_timeout}

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

    @computed_field
    @property
    def time_synchronization_interval(self) -> timedelta:
        return timedelta(seconds=self.time_synchronization_seconds) if self.time_synchronization_seconds else None

    @time_synchronization_interval.setter
    def time_synchronization_interval(self, v):
        if isinstance(v, timedelta):
            self.time_synchronization_seconds = v.total_seconds()

    @computed_field
    @property
    def bacnet_port(self) -> int:
        if self.bacnet_port_configured >= 47808:
            return self.bacnet_port_configured - 47808
        else:
            return self.bacnet_port_configured

    @bacnet_port.setter
    def bacnet_port(self, v):
        self.bacnet_port_configured = int(v)

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
        self.proxy_peer: ProtocolProxyPeer | None = None
        self.scheduled_ping = None
        self.time_synchronization_active = False

        self.ppm.register_callback(self.receive_cov, 'RECEIVE_COV', provides_response=False)
        self.ppm.start()
        self.driver_agent.core.spawn(self.ppm.select_loop)

    @property
    def register_count(self):
        return sum([len(reg_group) for reg_group in self.registers.values()])

    def finalize_setup(self, initial_setup: bool = False):
        # TODO: This will be called after every device is added.  If this is an issue, we would need a different hook.
        #  It could be called on every remote after the end of a setup loop, possibly?
        #_log.debug('BACnet finalize_setup called.')
        self.proxy_peer = self.ppm.get_proxy((str(self.config.local_interface), self.config.bacnet_port),
                                             **self.config.proxy_launch_options())
        _log.debug('BACnet finalize_setup: proxy_peer is: %s', self.proxy_peer)
        if initial_setup:
            self.ppm.wait_peer_registered(self.proxy_peer, self.config.registration_timeout, self.ping_target)
        # TODO: Consider adding a self.config.remote_refresh_interval to be scheduled as
        #  a periodic here to ping the target with a WhoIs.
        self.setup_time_synchronization()
        for topic, register in self.point_map.items():
            if register.is_cov:
                self.ppm.wait_peer_registered(self.proxy_peer, self.config.registration_timeout,
                                              self.establish_cov_subscription,
                                              register, topic, self.config.cov_lifetime)

    def create_register(self, register_definition: BacnetPointConfig) -> BACnetRegister:
        if register_definition.write_priority < self.config.min_priority:
            raise DriverConfigError(
                f"{register_definition.volttron_point_name} configured with a priority"
                f" {register_definition.write_priority} which is lower than than minimum {self.config.min_priority}.")

        return BACnetRegister(register_definition.instance,
                              register_definition.object_type,
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
                                 'device_instance_low': self.config.device_id,
                                 'device_instance_high': self.config.device_id,
                                 'dest': self.config.target_address
                             }).encode('utf8'),
                            response_expected=False
                         ))
            pinged = True
        # TODO: What exceptions might we really encounter, now, through PPM?
        except Exception as e:
            _log.warning(f"Error trying to ping device: {e}")

        self.scheduled_ping = None
        # Schedule retry.
        if not pinged:
            self.schedule_ping()

    def parse_proxy_response(self, response: Any, error_keys: Iterable[str]) -> tuple[Any, dict]:
        """Normalize a reply from the BACnet Proxy into ``(result, errors)``.

        ``ProtocolProxyManager.send`` returns an AsyncResult when a response is expected, or False when the
        request could not be sent (e.g., the proxy process has not registered). The proxy itself replies with
        ``{'result': ..., 'error': {...}}`` from its serializer, with ``{'status': 'error', 'error': ..., 'method': ...}``
        when the endpoint raised or timed out, or with an empty body when the endpoint returned nothing.
        Whole-request failures are reported against every key in ``error_keys`` (normally the affected topics).
        A gevent Timeout waiting on the AsyncResult is left to propagate to the caller.
        """
        error_keys = list(error_keys)

        def failed(message: str) -> tuple[dict, dict]:
            return {}, {key: message for key in error_keys}

        if not isinstance(response, AsyncResult):
            return failed(f'Unable to send request to BACnet Proxy (send returned {response!r}).')
        raw = response.get(timeout=self.config.reply_timeout)
        if not raw:
            return failed('Empty response from BACnet Proxy.')
        try:
            payload = json.loads(raw.decode('utf8'))
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError) as e:
            return failed(f'Undecodable response from BACnet Proxy: {e}')
        if not isinstance(payload, dict):
            return failed(f'Unexpected response from BACnet Proxy: {payload!r}')
        if payload.get('status') == 'error':
            return failed(f"BACnet Proxy {payload.get('method', 'request')} failed: {payload.get('error')}")
        return payload.get('result', {}), payload.get('error') or {}

    def _parse_scalar_response(self, response: Any, topic: str, operation: str) -> Any:
        result, errors = self.parse_proxy_response(response, [topic])
        if result != {}:
            return result
        elif errors:
            msg = f'Error {operation} point: {topic} --- {errors.get(topic, errors)}'
        else:
            msg = f'Unknown error {operation} point: {topic}. Response from proxy was empty.'
        _log.warning(msg)
        raise RuntimeError(msg)

    def get_point(self, topic: str, on_property: str = None):
        if self.proxy_peer is None:
            raise DriverInterfaceError("BACnet interface not initialized.  No proxy peer available.")
        register: BACnetRegister = cast(BACnetRegister, self.get_register_by_name(topic))
        response = self.ppm.send(self.proxy_peer,
                             ProtocolProxyMessage(
                                 method_name='READ_PROPERTY',
                                 payload=json.dumps({
                                     'device_address': self.config.target_address,
                                     'object_identifier': f'{register.object_type}, {register.instance_number}',
                                     'property_identifier': on_property if on_property else register.property,
                                     'property_array_index': register.array_index
                                 }).encode('utf8'),
                                 response_expected=True
                             ))
        return self._parse_scalar_response(response, topic, 'reading')

    def set_point(self, topic, value, priority=None, on_property=None):
        # TODO: support writing from an array.
        register: BACnetRegister = cast(BACnetRegister, self.get_register_by_name(topic))
        if register.read_only:
            raise IOError("Trying to write to a point configured read only: " + topic)

        if priority is not None and priority < self.config.min_priority:
            raise IOError("Trying to write with a priority lower than the minimum of " +
                          str(self.config.min_priority))

        response = self.ppm.send(self.proxy_peer,
                             ProtocolProxyMessage(
                                 method_name='WRITE_PROPERTY',
                                 payload=json.dumps({
                                     'device_address': self.config.target_address,
                                     'object_identifier': f'{register.object_type}, {register.instance_number}',
                                     'property_identifier': on_property if on_property else register.property,
                                     'value': value,
                                     'priority': priority if priority is not None else register.priority,
                                     'property_array_index': register.array_index
                                 }).encode('utf8'),
                                 response_expected=True
                             ))
        return self._parse_scalar_response(response, topic, 'writing')

    @staticmethod
    def _query_fields(reg: BacnetPointConfig):
        return {'object_id': f'{reg.object_type}, {reg.instance_number}',
                'property': reg.property, 'array_index': reg.array_index}

    def get_multiple_points(self, topics: KeysView[str], **kwargs) -> tuple[dict, dict]:
        # TODO: support reading from an array.
        # TODO: Manner of packing and unpacking this request needs to be rethought.
        point_map = {t: self._query_fields(self.point_map[t]) for t in topics if t in self.point_map}
        result_dict, error_dict = {}, {}
        if not point_map:
            return result_dict, error_dict
        # TODO: max_per_request could probably be detected from the device rather than only configured.
        batch_size = self.config.max_per_request if self.config.max_per_request > 0 else len(point_map)
        items = list(point_map.items())
        try:
            for i in range(0, len(items), batch_size):
                batch = dict(items[i:i + batch_size])
                response = self.ppm.send(self.proxy_peer,
                                         ProtocolProxyMessage(
                                             method_name='BATCH_READ',
                                             payload=json.dumps({
                                                 'device_address': self.config.target_address,
                                                 'read_specifications': batch
                                             }).encode('utf8'),
                                             response_expected=True
                                         ))
                result, errors = self.parse_proxy_response(response, batch.keys())
                result_dict.update(result if isinstance(result, dict) else {})
                error_dict.update(errors)
        except Timeout as e:
            _log.warning(f'Request timed out polling: {self.config.target_address}: {e}')
        except Exception as e:
            _log.warning(f'Unexpected error in get_multiple_points: {e}')
        return result_dict, error_dict

    def set_multiple_points(self, topics_values, **kwargs):
        # TODO: Implement SET_PROPERTY_MULTIPLE in BACnetProtocolProxy
        return super(BACnet, self).set_multiple_points(topics_values, **kwargs)

    def revert_all(self, priority=None):
        """
        Revert entire device to its default state
        """
        # TODO: Add multipoint write support
        # point_map is keyed by full topic; registers only know their bare point name.
        for topic, register in self.point_map.items():
            if not register.read_only:
                try:
                    self.revert_point(topic, priority=priority)
                except Exception as e:      # One failing point must not stop the rest of the device reverting.
                    _log.warning(f'Error while reverting point {topic}: {e}')

    def revert_point(self, topic, priority=None):
        """
        Revert point to its default state
        """
        # TODO: Should this have a way to set the revert value to something other than None (e.g., for UCSD's lights)?
        self.set_point(topic, None, priority=priority)

    def setup_time_synchronization(self):
        interval = self.config.time_synchronization_interval
        if interval is not None or self.time_synchronization_active:
            interval_seconds =  interval.total_seconds() if interval else None
            self.ppm.send(self.proxy_peer,
                          ProtocolProxyMessage(
                              method_name='SETUP_TIME_SYNCHRONIZATION',
                              payload=json.dumps({
                                  'device_address': self.config.target_address,
                                  'interval': interval_seconds,
                                  'time_zone': self.driver_agent.tz
                              }).encode('utf8'),
                              response_expected=False
                          ))
            self.time_synchronization_active = False if interval is None else True

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
        #_log.debug('@@@@@@@@@ IN RECEIVE_COV')
        message = json.loads(raw_message.decode('utf8'))
        #_log.debug(f'@@@@@@@@@ Received COV message: {message}')
        if error := message.get('error', []):
            _log.warning(f'Error received in COV push: {error}')
        if result := message.get('result', {}):
            self.driver_agent.publish_push(result)

    @classmethod
    def unique_remote_id(cls, config_name: str, config: RemoteConfig) -> tuple:
        bacnet_config = cls.INTERFACE_CONFIG_CLASS(**config.model_dump())
        return bacnet_config.target_address, bacnet_config.device_id
