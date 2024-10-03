# VOLTTRON BACnet Driver Interface

![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
[![Passing?](https://github.com/VOLTTRON/volttron-lib-bacnet-driver/actions/workflows/run-tests.yml/badge.svg)](https://github.com/VOLTTRON/volttron-lib-bacnet-driver/actions/workflows/run-tests.yml)
[![pypi version](https://img.shields.io/pypi/v/volttron-lib-bacnet-driver.svg)](https://pypi.org/project/volttron-lib-bacnet-driver/)

# Requires
# Requires

* python >= 3.10
* bacpypes == 0.16.7
* volttron-core >= 2.0.0rc0
* volttron-lib-base-driver >= 2.0.0rc0
* volttron-bacnet-proxy >= 2.0.0rc0

# Documentation
More detailed documentation can be found on [ReadTheDocs](https://eclipse-volttron.readthedocs.io/en/latest/external-docs/volttron-platform-driver/docs/source/index.html). The RST source
of the documentation for this component is located in the "docs" directory of this repository.


# Installation

Before installing, VOLTTRON should be installed and running.  Its virtual environment should be active.
Information on how to install of the VOLTTRON platform can be found
[here](https://github.com/eclipse-volttron/volttron-core).

1. If it is not already, install the VOLTTRON Platform Driver Agent:

    ```shell
    vctl install volttron-platform-driver --vip-identity platform.driver
    ```

2. Install the BACnetProxy Agent. An example configuration can be found [here](https://github.com/eclipse-volttron/volttron-bacnet-proxy/blob/main/config)

    ```shell
    vctl install volttron-bacnet-proxy --agent-config <path to bacnet proxy agent configuration file> --vip-identity platform.bacnet_proxy
    ```

3. Install the VOLTTRON BACnet Driver Library:

    ```shell
    pip install volttron-lib-bacnet-driver
    ```

4. Store device and registry files for the BACnet device to the Platform Driver configuration store:

    * Create a config directory and navigate to it:

        ```shell
        mkdir config
        cd config
        ```

    * Create a file called `device_name.config`; it should contain a JSON object that specifies the configuration of your BACnet driver. An example of such a file is provided at the root of this project; the example file is named 'bacnet.config'. The following JSON is an example of a `bacnet.config`:
    
         ```json
         {
             "driver_config": {"device_address": "123.45.67.890",
                               "device_id": 123456},
             "driver_type": "bacnet",
             "registry_config":"config://bacnet.csv",
             "interval": 15,
             "timezone": "US/Pacific"
         }
         ```

    * Create another file called `device_name.csv`; it should contain all the points on the device that you want published to Volttron. An example of such a CSV file is provided at the root of this project; the example CSV file is named 'bacnet.csv'. The following CSV file is an example:

        ```csv
        Point Name,Volttron Point Name,Units,Unit Details,BACnet Object Type,Property,Writable,Index,Notes
        12345a/Field Bus.12345A CHILLER.AHU-COIL-CHWR-T,12345a/Field Bus.12345A CHILLER.AHU-COIL-CHWR-T,degreesFahrenheit,-50.00 to 250.00,analogInput,presentValue,FALSE,3000741,,Primary CHW Return Temp
        ```
    
    * Add the bacnet driver config and bacnet csv file to the Platform Driver configuration store:

         ```
         vctl config store platform.driver bacnet.csv bacnet.csv --csv
         vctl config store platform.driver devices/bacnet bacnet.config
         ```

5. Observe Data

    To see data being published to the bus, install a [Listener Agent](https://github.com/eclipse-volttron/volttron-listener):

    ```
    vctl install volttron-listener --start
    ```

    Once installed, you should see the data being published by viewing the Volttron logs file that was created in step 2.
    To watch the logs, open a separate terminal and run the following command:

    ```
    tail -f <path to folder containing volttron.log>/volttron.log
    ```

# Development

Please see the following for contributing guidelines [contributing](https://github.com/eclipse-volttron/volttron-core/blob/develop/CONTRIBUTING.md).

Please see the following helpful guide about [developing modular VOLTTRON agents](https://eclipse-volttron.readthedocs.io/en/latest/developing-volttron/developing-agents/agent-development.html)

# Disclaimer Notice

This material was prepared as an account of work sponsored by an agency of the
United States Government.  Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in the development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or any
information, apparatus, product, software, or process disclosed, or represents
that its use would not infringe privately owned rights.

Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the United
States Government or any agency thereof, or Battelle Memorial Institute. The
views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.
