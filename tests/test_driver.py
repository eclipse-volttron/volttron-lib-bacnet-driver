"""Unit tests for volttron-lib-bacnet-driver"""

from volttron.driver.interfaces.bacnet.bacnet import BaseInterface, BACnet


def test_driver():
    driver = BACnet()
    assert isinstance(driver, BaseInterface)
