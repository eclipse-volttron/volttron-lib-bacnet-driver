"""Configuration for the pytest test suite."""

#from volttrontesting.fixtures.volttron_platform_fixtures import *

# Sets command-line arguments which can be passed to pytest. Defaulting to None allows some tests to run without them.
#  Tests which require these arguments should check for that they are not None and then skip or err.
def pytest_addoption(parser):
    parser.addoption("--device-config-file", action = "store", default=None,
                     help = "Filename of the device configuration for the BACnet device being tested.")
    parser.addoption("--registry-config-file", action = "store", default=None,
                     help = "Filename of the registry configuration for the BACnet device being tested."
                            "This is not required if registry is included as json in --device-config-file.")
