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

import builtins
import json
import logging

from math import ceil

from collections.abc import Iterable, KeysView
from datetime import datetime, timedelta
from enum import Enum
from pydantic import AliasChoices, computed_field, Field, IPvAnyInterface, field_validator, model_validator
from typing import ClassVar, Annotated, Any, cast

from volttron.driver.base.config import DataSource, empty_str_is, PointConfig, RemoteConfig
from volttron.driver.base.driver_exceptions import DriverConfigError
from volttron.driver.base.interfaces import BaseInterface, BaseRegister, DriverInterfaceError
from volttron.driver.base.proxy_interface import ProxyBackedInterface

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


#: Object types a peer may command on a served device unless the row says otherwise; inputs are read-only by convention.
COMMANDABLE_OBJECT_TYPES = frozenset({'analogOutput', 'analogValue', 'binaryOutput', 'binaryValue', 'multiStateOutput',
                                      'multiStateValue'})


class DriverRole(str, Enum):
    """Which side of BACnet this remote is. ``master``/``outstation``/``slave`` are accepted spellings as well."""
    client = 'client'
    server = 'server'


ROLE_ALIASES = {'master': 'client', 'outstation': 'server', 'slave': 'server'}


class BacnetPointConfig(PointConfig):
    array_index: Annotated[int | None, empty_str_is(None)] = None
    object_type: str = Field(validation_alias=AliasChoices('object_type', 'Object Type','BACnet Object Type', 'bacnet_object_type'))
    property: Annotated[str, empty_str_is('present-value')] = Field(alias='Property', default='present-value')  # TODO: Should be an Enum of BACnet property types.
    instance: int = Field(validation_alias=AliasChoices('instance','Instance', 'Index', 'index'))
    cov_flag: Annotated[bool, empty_str_is(False)] = Field(default=False, alias='COV Flag')
    write_priority: Annotated[int, empty_str_is(16)] = Field(default=16, ge=1, le=16, alias='Write Priority')
    # Served devices: the value the object holds until something writes it, the state count of a multi-state object,
    # the COV increment of an analog object and the object's name on the network (the point name when blank).
    default_value: Any = Field(default=None, validation_alias=AliasChoices('default_value', 'Default Value', 'Starting Value'))
    number_of_states: Annotated[int, empty_str_is(2)] = Field(default=2, ge=1, validation_alias=AliasChoices('number_of_states', 'Number Of States'))
    cov_increment: Annotated[float | None, empty_str_is(None)] = Field(default=None, validation_alias=AliasChoices('cov_increment', 'COV Increment'))
    object_name: str = Field(default='', validation_alias=AliasChoices('object_name', 'Object Name'))

    # ``property`` is a field of this model, so the decorator comes from builtins.
    @builtins.property
    def is_served(self) -> bool:
        """A served point (``data_source: server``) of a device this driver serves."""
        return self.data_source is DataSource.SERVER

    @builtins.property
    def resolved_remote_writable(self) -> bool:
        """Whether a peer may command this served object: as configured, else when it is an output or value object."""
        return self.remote_writable if self.remote_writable is not None else self.object_type in COMMANDABLE_OBJECT_TYPES


class BacnetRemoteConfig(RemoteConfig):
    driver_role: DriverRole = Field(default=DriverRole.client,
                                    description='client reaches a BACnet device; server serves the registry as a '
                                                'device of its own.',
                                    json_schema_extra={'group': 'Device', 'order': 20})
    # TODO: Confirm this is not needed now that it is added to superclass: model_config = ConfigDict(populate_by_name=True)
    bacnet_port_configured: int = Field(default=0, alias='bacnet_port',
                                        description="The proxy's UDP port; 0 means 47808.",
                                        json_schema_extra={'group': 'Connection', 'advanced': True, 'order': 40})
    cov_lifetime_configured: float = Field(default=180.0, alias='cov_lifetime',
                                           description='Lifetime requested for change-of-value subscriptions.',
                                           json_schema_extra={'group': 'Protocol and timing', 'advanced': True,
                                                              'unit': 's', 'order': 130})  # TODO: Can this by by point instead?
    device_id: int = Field(ge=0, description='The BACnet device instance number.',
                           json_schema_extra={'group': 'Connection', 'order': 20})
    # TODO: local_interface: we should attempt to discover the interface.
    local_interface: IPvAnyInterface = Field(default='0.0.0.0/32',
                                             description='The local interface (CIDR) the proxy binds.',
                                             json_schema_extra={'group': 'Connection', 'order': 30})
    max_per_request: int = Field(ge=0, default=24, description='Points per ReadPropertyMultiple request.',
                                 json_schema_extra={'group': 'Protocol and timing', 'order': 70})
    min_priority: int = Field(default=8, ge=1, le=16, description='Lowest BACnet write priority this device accepts.',
                              json_schema_extra={'group': 'Protocol and timing', 'advanced': True, 'order': 90})
    ping_retry_interval_configured: float = Field(alias='ping_retry_interval', default=5.0,
                                                  json_schema_extra={'group': 'Protocol and timing', 'advanced': True,
                                                                     'unit': 's', 'order': 140})
    time_synchronization_seconds: float | None = Field(default=None, ge=0, alias='time_synchronization_interval',
                                                       description='How often to send the device a time '
                                                                   'synchronization; blank never.',
                                                       json_schema_extra={'group': 'Protocol and timing',
                                                                          'advanced': True, 'unit': 's', 'order': 150})
    # Client role: the device to reach. Required in that role.
    target_address: str | None = Field(default=None, alias='device_address', description='Client: the device to reach.',
                                       json_schema_extra={'group': 'Connection',
                                                          'show_when': {'driver_role': ['client', 'master']},
                                                          'order': 10})
    # Server role: the UDP port the served device answers on (an offset from 47808, or an absolute port; it must differ
    # from this proxy's bacnet_port), and the served device object's name.
    served_port: int | None = Field(default=None, ge=0,
                                    description='Server: the UDP port the served device answers on (an offset from '
                                                '47808 or an absolute port; must differ from bacnet_port).',
                                    json_schema_extra={'group': 'Connection',
                                                       'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                       'order': 50})
    object_name: str = Field(default='VOLTTRON served device', description="Server: the served device object's name.",
                             json_schema_extra={'group': 'Connection',
                                                'show_when': {'driver_role': ['server', 'slave', 'outstation']},
                                                'order': 60})
    # Device-level: how long the proxy waits for this device to answer one request, and how often it retries.
    # These configure the shared proxy process for this local interface; the first device to launch it wins.
    apdu_timeout: float = Field(default=3.0, gt=0,
                                description='How long the proxy waits for this device to answer one request; the first '
                                            'device to launch the shared proxy wins.',
                                json_schema_extra={'group': 'Protocol and timing', 'unit': 's', 'order': 100})
    apdu_retries: int = Field(default=3, ge=0, json_schema_extra={'group': 'Protocol and timing', 'order': 110})
    # Reply wait: how long to wait for the proxy's answer to a request. None derives it from the APDU settings and
    # max_per_request so that it always outlasts the proxy's own BATCH_READ limit.
    timeout: float | None = Field(default=None, ge=0,
                                  description="How long to wait for the proxy's answer; blank derives it from the APDU "
                                              'settings and max_per_request.',
                                  json_schema_extra={'group': 'Protocol and timing', 'advanced': True, 'unit': 's',
                                                     'order': 120})
    # How long to wait for the proxy process to start and register.
    registration_timeout: float = Field(default=30.0, gt=0,
                                        json_schema_extra={'group': 'Proxy', 'advanced': True, 'unit': 's',
                                                           'order': 160})
    persistent_connection: bool = Field(default=True, description='Keep one IPC connection to the proxy open for every'
                                        ' request and push instead of one connection per message.',
                                        json_schema_extra={'group': 'Proxy', 'advanced': True, 'order': 170})
    use_read_multiple: bool = Field(default=True,
                                    json_schema_extra={'group': 'Protocol and timing', 'advanced': True, 'order': 80})

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

    @field_validator('driver_role', mode='before')
    @classmethod
    def _role(cls, v):
        if isinstance(v, str):
            v = v.lower().strip() or 'client'
            return ROLE_ALIASES.get(v, v)
        return DriverRole.client if v is None else v

    @model_validator(mode='after')
    def _check_role_fields(self):
        if self.driver_role is DriverRole.client and not self.target_address:
            raise ValueError('A BACnet client needs the device_address of the device to reach.')
        if self.driver_role is DriverRole.server:
            if self.served_port is None:
                raise ValueError('A served BACnet device needs served_port, a UDP port of its own.')
            if self.resolved_served_port == self.resolved_proxy_port:
                raise ValueError('served_port must differ from bacnet_port: the proxy already answers on that port.')
        return self

    @property
    def is_server(self) -> bool:
        return self.driver_role is DriverRole.server

    @staticmethod
    def _udp_port(port: int) -> int:
        return port if port >= 1024 else 47808 + port

    @property
    def resolved_proxy_port(self) -> int:
        return self._udp_port(self.bacnet_port_configured)

    @property
    def resolved_served_port(self) -> int:
        return self._udp_port(self.served_port or 0)

    def served_fields(self) -> dict:
        """What REGISTER_REMOTE needs to serve the device, besides the point table."""
        return {'role': 'server', 'local_interface': str(self.local_interface), 'port': self.resolved_served_port,
                'device_id': self.device_id, 'object_name': self.object_name}

    def proxy_key(self) -> tuple:
        """Selects the proxy process: one per local interface and port."""
        return str(self.local_interface), self.bacnet_port

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
        # Served devices only.
        self.remote_writable = False
        self.initial_value: Any = None
        self.number_of_states = 2
        self.cov_increment: float | None = None
        self.object_name = ''

    def served_fields(self, topic: str) -> dict:
        """This register's entry in a served device's point table."""
        return {'topic': topic, 'object_type': self.object_type, 'instance': self.instance_number, 'property': self.property,
                'units': self.units, 'remote_writable': self.remote_writable, 'initial_value': self.initial_value,
                'number_of_states': self.number_of_states, 'cov_increment': self.cov_increment, 'object_name': self.object_name}


class BACnet(ProxyBackedInterface, BaseInterface):
    """Platform Driver interface for a BACnet device, served by the BACnet Protocol Proxy, in either role.

    Client role (the default): BACnet has no registration message; the proxy is launched with the local interface
    settings, the device is reached with a directed WhoIs, and each change-of-value point is subscribed to. Reads
    batch by ``max_per_request``; writes are one WriteProperty per point with a priority.

    Server role (``driver_role: server``): the registry rows are objects of a BACnet device the proxy serves on
    ``served_port`` (REGISTER_REMOTE). ``set_point`` sets an object's present value and reflects it into the
    equipment tree at once, ``get_multiple_points`` reports what the proxy holds (READ_POINTS, WRITE_POINTS by topic),
    and a peer's accepted writes to ``Remote Writable`` objects (outputs and values by default) arrive as pushes.
    Served rows default to ``data_source: server``, so they are never polled.
    """

    REGISTER_CONFIG_CLASS = BacnetPointConfig
    INTERFACE_CONFIG_CLASS = BacnetRemoteConfig
    PROXY_NAME, PROXY_LABEL = 'bacnet', 'BACnet Proxy'
    REGISTER_METHOD = None
    READ_METHOD, WRITE_METHOD, PUSH_METHOD = 'BATCH_READ', 'WRITE_PROPERTY', 'RECEIVE_COV'

    def __init__(self, config, *args, **kwargs):
        super(BACnet, self).__init__(config, *args, **kwargs)
        self.register_count_divisor = 1
        self.scheduled_ping = None
        self.time_synchronization_active = False
        if self.config.is_server:
            self.REGISTER_METHOD, self.READ_METHOD, self.WRITE_METHOD = 'REGISTER_REMOTE', 'READ_POINTS', 'WRITE_POINTS'
        self.init_proxy()

    @property
    def is_server(self) -> bool:
        return self.config.is_server

    @property
    def register_count(self):
        return sum([len(reg_group) for reg_group in self.registers.values()])

    # ---- the proxy exchange, in BACnet terms ------------------------------------------------------------------
    def identity_fields(self) -> dict:
        if self.is_server:
            return self.config.served_fields()
        return {'device_address': self.config.target_address}

    def proxy_launch_options(self) -> dict:
        return self.config.proxy_launch_options()

    def prepare_registry_config(self, registry_config: list[dict], remote_config: RemoteConfig | None = None) -> list[dict]:
        """In the server role, rows default to the ``server`` data source and to being writable by the platform."""
        role = getattr(remote_config, 'driver_role', None) if remote_config is not None else self.config.driver_role
        role = getattr(role, 'value', role)
        if ROLE_ALIASES.get(role, role) != DriverRole.server.value:
            return registry_config
        prepared = []
        for row in registry_config:
            row = dict(row)
            if all(row.get(key) in (None, '') for key in ('data_source', 'Data Source')):
                row['data_source'] = 'server'
            if all(row.get(key) in (None, '') for key in ('writable', 'Writable')):
                row['writable'] = True
            prepared.append(row)
        return prepared

    def registration_payload(self) -> dict:
        """The served device and its objects (server role only; a client sends no registration)."""
        return {**self.config.served_fields(),
                'points': [cast(BACnetRegister, register).served_fields(topic) for topic, register in self.point_map.items()],
                'values': self.tree_values()}

    def after_registration(self, result: dict, initial_setup: bool):
        if self.is_server:
            _log.info(f"BACnet device {self.config.device_id} is served on {self.config.local_interface}:{result.get('port')} by"
                      f" the proxy as {result.get('server')} with {result.get('points')} objects.")
            return
        # TODO: finalize_setup is called after every device is added. If this is an issue, we would need a different
        #  hook; it could be called on every remote after the end of a setup loop, possibly?
        if initial_setup:
            self.ping_target()
        # TODO: Consider adding a self.config.remote_refresh_interval to be scheduled as
        #  a periodic here to ping the target with a WhoIs.
        self.setup_time_synchronization()
        for topic, register in self.point_map.items():
            if register.is_cov:
                self.ppm.wait_peer_registered(self.proxy_peer, self.config.registration_timeout,
                                              self.establish_cov_subscription,
                                              register, topic, self.config.cov_lifetime)

    def create_register(self, register_definition: BacnetPointConfig) -> BACnetRegister:
        if not self.is_server and register_definition.write_priority < self.config.min_priority:
            raise DriverConfigError(
                f"{register_definition.volttron_point_name} configured with a priority"
                f" {register_definition.write_priority} which is lower than than minimum {self.config.min_priority}.")

        register = BACnetRegister(register_definition.instance,
                                  register_definition.object_type,
                                  register_definition.property,
                                  register_definition.writable is False,
                                  register_definition.volttron_point_name,
                                  register_definition.units,
                                  description=register_definition.notes,
                                  priority=register_definition.write_priority,
                                  list_index=register_definition.array_index,
                                  is_cov=register_definition.cov_flag)
        if register_definition.is_served:
            register.remote_writable = register_definition.resolved_remote_writable
            register.number_of_states, register.cov_increment = register_definition.number_of_states, register_definition.cov_increment
            register.object_name = register_definition.object_name
            if register_definition.default_value not in (None, ''):
                try:
                    register.initial_value = self._coerce(register, register_definition.default_value)
                except (TypeError, ValueError):
                    _log.warning(f"Starting value {register_definition.default_value!r} for {register.point_name} is not usable;"
                                 " the object starts empty.")
        return register

    @staticmethod
    def _coerce(register: BACnetRegister, value: Any):
        python_type = register.python_type
        if python_type is bool:
            if isinstance(value, str):
                text = value.strip().lower()
                if text in ('true', 't', 'on', 'yes', '1', 'active'):
                    return True
                if text in ('false', 'f', 'off', 'no', '0', '', 'inactive'):
                    return False
                raise ValueError(f'{value!r} is not a boolean')
            return bool(value)
        if python_type is int:
            return int(value, 0) if isinstance(value, str) else int(value)
        if isinstance(value, bool):
            raise TypeError('a boolean is not a number')
        return float(value)

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
            self._send('WHO_IS', {'device_instance_low': self.config.device_id,
                                  'device_instance_high': self.config.device_id,
                                  'dest': self.config.target_address}, response_expected=False)
            pinged = True
        # TODO: What exceptions might we really encounter, now, through PPM?
        except Exception as e:
            _log.warning(f"Error trying to ping device: {e}")

        self.scheduled_ping = None
        # Schedule retry.
        if not pinged:
            self.schedule_ping()

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

    def _property_payload(self, register: BACnetRegister, on_property: str | None) -> dict:
        return {'device_address': self.config.target_address,
                'object_identifier': f'{register.object_type}, {register.instance_number}',
                'property_identifier': on_property if on_property else register.property,
                'property_array_index': register.array_index}

    def get_point(self, topic: str, on_property: str = None, **kwargs):
        """One ReadProperty (BATCH_READ is for polls): any property of the point's object, not only the configured one.
        On a served device, the object's present value as the proxy holds it. Other keywords (e.g. a write's
        ``priority`` passed along to a confirming read) do not apply to a read and are ignored."""
        if self.is_server:
            return ProxyBackedInterface.get_point(self, topic)
        self._require_peer()
        register: BACnetRegister = cast(BACnetRegister, self.get_register_by_name(topic))
        response = self._send('READ_PROPERTY', self._property_payload(register, on_property))
        return self._parse_scalar_response(response, topic, 'reading')

    def set_point(self, topic, value, priority=None, on_property=None):
        # TODO: support writing from an array.
        if self.is_server:
            register = cast(BACnetRegister, self.point_map[topic])
            results, errors = self.proxy_write([(topic, self._coerce(register, value))])
            if topic not in results:
                raise IOError(f'Error writing point {topic}: {errors.get(topic, errors)}')
            return results[topic]
        register: BACnetRegister = cast(BACnetRegister, self.get_register_by_name(topic))
        if register.read_only:
            raise IOError("Trying to write to a point configured read only: " + topic)
        if priority is not None and priority < self.config.min_priority:
            raise IOError("Trying to write with a priority lower than the minimum of " +
                          str(self.config.min_priority))
        response = self._send('WRITE_PROPERTY', self._write_payload(register, value, priority, on_property))
        return self._parse_scalar_response(response, topic, 'writing')

    def _write_payload(self, register: BACnetRegister, value, priority=None, on_property=None) -> dict:
        return {**self._property_payload(register, on_property), 'value': value,
                'priority': priority if priority is not None else register.priority}

    @staticmethod
    def _query_fields(reg: BacnetPointConfig, on_property: str | None = None):
        return {'object_id': f'{reg.object_type}, {reg.instance_number}',
                'property': on_property if on_property else reg.property, 'array_index': reg.array_index}

    def split_reads(self, topics: list[str], on_property: str = None, **kwargs) -> list[tuple[dict, list[str]]]:
        """BATCH_READ requests of at most ``max_per_request`` points (0: everything in one); a served device is read
        by topic in one request. ``on_property`` reads that property instead of each register's configured one, as
        get_point does for a single point; other keywords (e.g. a write's ``priority``) do not apply to a read and
        are ignored."""
        if self.is_server:
            return ProxyBackedInterface.split_reads(self, topics, **kwargs)
        # TODO: support reading from an array.
        # TODO: max_per_request could probably be detected from the device rather than only configured.
        batch_size = self.config.max_per_request if self.config.max_per_request > 0 else len(topics)
        batches = []
        for i in range(0, len(topics), batch_size):
            batch = topics[i:i + batch_size]
            batches.append(({'device_address': self.config.target_address,
                             'read_specifications': {t: self._query_fields(self.point_map[t], on_property)
                                                     for t in batch}}, batch))
        return batches

    def split_writes(self, items: list[tuple[str, Any]], priority=None, on_property=None, **kwargs):
        """One WRITE_PROPERTY per point until the proxy supports WritePropertyMultiple; a served device takes every
        value by topic in one request."""
        if self.is_server:
            return ProxyBackedInterface.split_writes(self, items, **kwargs)
        return [(self._write_payload(cast(BACnetRegister, self.point_map[topic]), value, priority, on_property), [(topic, value)])
                for topic, value in items]

    def interpret_write(self, items: list[tuple[str, Any]], result: Any, request_errors: Any) -> tuple[dict, dict]:
        """A WriteProperty acknowledgement carries no value: the reply is the (possibly None) result or an error. A
        served device answers by topic; its stored values are reflected into the equipment tree at once."""
        if self.is_server:
            results, errors = ProxyBackedInterface.interpret_write(self, items, result, request_errors)
            if results:
                self.driver_agent.publish_push(dict(results))
            return results, errors
        [(topic, _)] = items
        if isinstance(request_errors, dict) and request_errors:
            return {}, {topic: str(request_errors.get(topic, request_errors))}
        return {topic: result if result != {} else None}, {}

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
        Revert point to its default state: relinquish the command on a remote device; on a served device, restore the
        row's starting value (a point without one is left as it is).
        """
        if self.is_server:
            register = cast(BACnetRegister, self.point_map[topic])
            if register.initial_value is not None:
                self.set_point(topic, register.initial_value)
            return
        # TODO: Should this have a way to set the revert value to something other than None (e.g., for UCSD's lights)?
        self.set_point(topic, None, priority=priority)

    def setup_time_synchronization(self):
        interval = self.config.time_synchronization_interval
        if interval is not None or self.time_synchronization_active:
            interval_seconds = interval.total_seconds() if interval else None
            self._send('SETUP_TIME_SYNCHRONIZATION', {'device_address': self.config.target_address,
                                                      'interval': interval_seconds, 'time_zone': self.driver_agent.tz},
                       response_expected=False)
            self.time_synchronization_active = False if interval is None else True

    def establish_cov_subscription(self, register, topic, lifetime):
        """
        Asks the BACnet Proxy to establish a COV subscription for the point via RPC.
        If lifetime is specified, the subscription will be renewed by the proxy on
         that interval, else the subscription will last indefinitely.
        """
        self._send('SETUP_COV', {
            'subscription_key': topic,
            'device_address': self.config.target_address,
            'monitored_object_identifier': ':'.join([str(register.object_type), str(register.instance_number)]),
            'property_identifier': register.property,
            'issue_confirmed_notifications': True,  # TODO: Should this be exposed somewhere in case only unconfirmed notifications are supported?
            'lifetime': lifetime.total_seconds(),
            'remote_id': self.remote_id.hex                 # the proxy tags this subscription's RECEIVE_COV pushes with it
        }, response_expected=False)

    @classmethod
    def unique_remote_id(cls, config_name: str, config: RemoteConfig) -> tuple:
        bacnet_config = cls.INTERFACE_CONFIG_CLASS(**config.model_dump())
        if bacnet_config.is_server:
            return 'bacnet', 'server', str(bacnet_config.local_interface), bacnet_config.resolved_served_port, bacnet_config.device_id
        return bacnet_config.target_address, bacnet_config.device_id
