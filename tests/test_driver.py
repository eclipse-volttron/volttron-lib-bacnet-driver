"""Unit tests for volttron-lib-bacnet-driver"""

from volttron.driver.interfaces.bacnet.bacnet import BACnet, BaseInterface


def test_driver():
    driver = BACnet()
    assert isinstance(driver, BaseInterface)
