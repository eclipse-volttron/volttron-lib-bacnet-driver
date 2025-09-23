"""Integration tests for volttron-lib-bacnet-driver"""
import logging
import os
import socket

import gevent
import pytest
from mock import MagicMock    # type: ignore
from volttron.client.known_identities import CONFIGURATION_STORE, PLATFORM_DRIVER
from volttron.client.logs import setup_logging
from volttrontesting.platformwrapper import PlatformWrapper

setup_logging()
logger = logging.getLogger(__name__)

BACNET_DEVICE_TOPIC = "devices/bacnet"
BACNET_TEST_IP = "BACNET_TEST_IP"
skip_msg = f"Env var {BACNET_TEST_IP} not set. Please set the env var to the proper IP to run this integration test."

# apply skipif to all tests
pytestmark = pytest.mark.skipif(os.environ.get(BACNET_TEST_IP) is None, reason=skip_msg)


def test_scrape_all_should_succeed(bacnet_test_agent):
    register_values = [
        "3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820a/Field Bus.3820A CHILLER.CHW-FLOW",
    ]
    actual_values = bacnet_test_agent.vip.rpc.call(PLATFORM_DRIVER, "scrape_all",
                                                   "bacnet").get(timeout=10)
    logger.info(f"Result of scrape_all: {actual_values}")

    for register in register_values:
        assert register in actual_values


def test_get_point_should_succeed(bacnet_test_agent):
    register_values = [
        "3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T",
        "3820a/Field Bus.3820A CHILLER.CHW-FLOW",
    ]
    for register in register_values:
        async_res = bacnet_test_agent.vip.rpc.call(PLATFORM_DRIVER, "get_point", "bacnet",
                                                   register)
        value = async_res.get()
        logger.info(f"Value for point {register}: {value}")
        assert isinstance(value, float)


@pytest.fixture(scope="module")
def bacnet_proxy_agent(volttron_instance: PlatformWrapper):
    device_address = socket.gethostbyname(socket.gethostname() + ".local")
    print(f"Device address for proxy agent for testing: {device_address}")
    bacnet_proxy_agent_config = {
        "device_address": device_address,
    # below are optional; values are set to show configuration options; values use the default values
        "max_apdu_length": 1024,
        "object_id": 599,
        "object_name": "Volttron BACnet driver",
        "vendor_id": 5,
        "segmentation_supported": "segmentedBoth",
    }

    # Installs volttron-bacnet-proxy from PyPi
    bacnet_proxy_agent_uuid = volttron_instance.install_agent(
        agent_dir="volttron-bacnet-proxy",
        config_file=bacnet_proxy_agent_config,
    )
    gevent.sleep(1)
    volttron_instance.start_agent(bacnet_proxy_agent_uuid)
    assert volttron_instance.is_agent_running(bacnet_proxy_agent_uuid)

    yield bacnet_proxy_agent_uuid

    print("Teardown of bacnet_proxy_agent")
    volttron_instance.stop_agent(bacnet_proxy_agent_uuid)


@pytest.fixture(scope="module")
def config_store_connection(volttron_instance: PlatformWrapper):
    capabilities = [{"edit_config_store": {"identity": PLATFORM_DRIVER}}]
    connection = volttron_instance.build_connection(peer=CONFIGURATION_STORE,
                                                    capabilities=capabilities)
    gevent.sleep(1)

    # Installs volttron-platform-driver from PyPi
    # Start the platform driver agent which would in turn start the bacnet driver
    platform_uuid = volttron_instance.install_agent(
        agent_dir="volttron-platform-driver",
        config_file={
            "publish_breadth_first_all": False,
            "publish_depth_first": False,
            "publish_breadth_first": False,
        },
        start=True,
    )
    gevent.sleep(2)    # wait for the agent to start and start the devices

    yield connection

    volttron_instance.stop_agent(platform_uuid)
    volttron_instance.remove_agent(platform_uuid)
    connection.kill()


@pytest.fixture(scope="module")
def config_store(config_store_connection):
    # this fixture will set up a BACnet driver that will communicate with a live BACnet device located at PNNL campus in Richland at the given device_address
    device_address = os.environ.get(BACNET_TEST_IP)
    if os.system("ping -c 1 " + device_address) != 0:
        pytest.skip(f"BACnet device cannot be reached at {device_address} ")

    registry_config = "bacnet_test.csv"
    registry_string = f"""Reference Point Name,Volttron Point Name,Units,Unit Details,BACnet Object Type,Property,Writable,Index,Write Priority,Notes
        3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T,3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T,degreesFahrenheit,-50.00 to 250.00,analogInput,presentValue,FALSE,3000741,,Primary CHW Return Temp
        3820a/Field Bus.3820A CHILLER.CHW-FLOW,3820a/Field Bus.3820A CHILLER.CHW-FLOW,usGallonsPerMinute,-50.00 to 250.00,analogInput,presentValue,FALSE,3000744,,Chiller 1 CHW Flow"""

    # registry config
    config_store_connection.call(
        "manage_store",
        PLATFORM_DRIVER,
        registry_config,
        registry_string,
        config_type="csv",
    )

    # driver config
    driver_config = {
        "driver_config": {
            "device_address": device_address,
            "device_id": 506892
        },
        "driver_type": "bacnet",
        "registry_config": f"config://{registry_config}",
        "timezone": "US/Pacific",
        "interval": 15,
    }

    config_store_connection.call(
        "manage_store",
        PLATFORM_DRIVER,
        BACNET_DEVICE_TOPIC,
        driver_config,
    )

    yield config_store_connection

    print("Wiping out store.")
    config_store_connection.call("manage_delete_store", PLATFORM_DRIVER)
    gevent.sleep(0.1)


@pytest.fixture(scope="module")
def bacnet_test_agent(bacnet_proxy_agent, config_store, volttron_instance):
    test_agent = volttron_instance.build_agent(identity="test-agent")
    assert test_agent.core.identity

    # create a mock callback to use with a subscription to the driver's publish publishes
    test_agent.poll_callback = MagicMock(name="poll_callback")

    # subscribe to device topic results
    test_agent.vip.pubsub.subscribe(
        peer="pubsub",
        prefix=BACNET_DEVICE_TOPIC,
        callback=test_agent.poll_callback,
    ).get()

    # give the test agent the capability to modify the platform_driver's config store
    capabilities = {"edit_config_store": {"identity": PLATFORM_DRIVER}}
    volttron_instance.add_capabilities(test_agent.core.publickey, capabilities)

    # A sleep was required here to get the platform to consistently add the edit config store capability
    gevent.sleep(1)

    yield test_agent

    print("In teardown method of query_agent")
    test_agent.core.stop()
