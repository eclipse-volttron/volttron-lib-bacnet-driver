"""Integration tests for volttron-lib-bacnet-driver"""
import csv
import gevent
import json
import logging
import os
import pytest

from unittest.mock import MagicMock

from volttron.client.logs import setup_logging
from volttron.types.auth.auth_credentials import Credentials

from volttrontesting import TestServer
from volttrontesting.mock_core_builder import MockCoreBuilder

from platform_driver.agent import PlatformDriverAgent

setup_logging()
_log = logging.getLogger(__name__)

BACNET_DEVICE_TOPIC = "devices/bacnet"


def test_scrape_all_should_succeed(bacnet_platform_driver_agent):
    register_values = [
        "3820aField Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820aField Bus.3820A CHILLER.CHW-FLOW",
    ]
    actual_values = bacnet_platform_driver_agent.get(BACNET_DEVICE_TOPIC)
    _log.info(f"Result of scrape_all: {actual_values}")

    for register in register_values:
        assert f'{BACNET_DEVICE_TOPIC}/{register}' in actual_values[0]


def test_get_point_should_succeed(bacnet_platform_driver_agent):
    register_values = [
        "3820aField Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820aField Bus.3820A CHILLER.CHW-FLOW",
    ]
    for register in register_values:
        value = bacnet_platform_driver_agent.get_point(f'{BACNET_DEVICE_TOPIC}/{register}')
        _log.info(f"Value for point {register}: {value}")
        assert isinstance(value, float)


@pytest.fixture(scope="module")
def bacnet_configuration(request):
    # this fixture will set up a BACnet driver that will communicate with a live BACnet device
    # using arguments passed to pytest.

    # Skip test if required parameters were not passed.
    missing = []
    if not (device_config_file := request.config.getoption("--device-config-file")
                                  or (device_config_file := os.environ.get("BACNET_DEVICE_CONFIG"))):
        missing.append('device-config-file')
    if missing:
        pytest.skip(f"Skipping tests because one or more options are not set. Missing options: {', '.join(missing)}")

    def _load_registry_file(registry_file_name):
        registry = []
        try:
            if ".csv" in registry_file_name:
                with open(registry_file_name) as c:
                    reader = csv.DictReader(c)
                    for row in reader:
                        registry.append(row)
            else:  # Assume json
                with open(registry_file_name) as j:
                    registry = json.load(j)
        except (json.JSONDecodeError, csv.Error) as e:
            raise ValueError(f'Unable to load registry from filename passed to --registry-config-file: {e}')
        return registry

    with open(device_config_file) as f:
        device_config = json.load(f)
    _log.debug(device_config)
    device_config['driver_config']['target_address'] = device_config['driver_config'].get(
        'target_address', device_config['driver_config'].get('device_address'))
    if not device_config['driver_config']['target_address']:
        raise ValueError('Device configuration requires either a "target_address" or "device_address" field.')

    # Use passed file for registry configuration if --registry-config-file was passed.
    #   Use filename in config_file if it is a config://... string.
    #   Otherwise device_config['registry_config'] must be a list of dictionaries.
    registry_config_file = request.config.getoption("--registry-config-file" or os.environ.get("REGISTRY_CONFIG_FILE"))
    if not registry_config_file and isinstance(device_config['registry_config'], str):
        registry_config_file = device_config['registry_config']
    if registry_config_file:
        device_config['registry_config'] = _load_registry_file(registry_config_file)
    elif (not isinstance(device_config_file, list)
          or not all(isinstance(d, dict) for d in device_config['registry_config'])):
        raise ValueError('Unable to find valid registry configuration. Registry configuration must be provided'
                         ' as list of dicts in device configuration or the name of a valid csv or json file must'
                         ' be passed to --registry-config-file.')
    return device_config


@pytest.fixture(scope="module")
def bacnet_platform_driver_agent(bacnet_configuration):
    # Skip test if target device cannot be reached.
    if os.system("ping -c 1 " + bacnet_configuration['driver_config']['target_address']) != 0:
        pytest.skip(f"BACnet device cannot be reached at {bacnet_configuration['driver_config']['target_address']}.")

    server = TestServer()
    pda = PlatformDriverAgent(credentials=Credentials(identity="platform.driver"), name="mock")
    server.connect_agent(pda)
    assert pda.core.identity == "platform.driver"

    pda.core.spawn = gevent.spawn  #  MagicMock()
    pda.core.schedule = MagicMock()

    # noinspection PyProtectedMember
    pda._configure_new_equipment(BACNET_DEVICE_TOPIC, 'NEW', bacnet_configuration, False)

    yield pda

    print("In teardown method of PlatformDriverAgent.")
    pda.core.stop()
