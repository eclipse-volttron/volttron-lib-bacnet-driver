# volttron-lib-bacnet-driver

[![Passing?](https://github.com/VOLTTRON/volttron-lib-bacnet-driver/actions/workflows/run-tests.yml/badge.svg)](https://github.com/VOLTTRON/volttron-lib-bacnet-driver/actions/workflows/run-tests.yml)
[![pypi version](https://img.shields.io/pypi/v/volttron-lib-bacnet-driver.svg)](https://pypi.org/project/volttron-lib-bacnet-driver/)

# Prerequisites

* Python 3.8

## Python

<details>
<summary>To install Python 3.8, we recommend using <a href="https://github.com/pyenv/pyenv"><code>pyenv</code></a>.</summary>

```bash
# install pyenv
git clone https://github.com/pyenv/pyenv ~/.pyenv

# setup pyenv (you should also put these three lines in .bashrc or similar)
export PATH="${HOME}/.pyenv/bin:${PATH}"
export PYENV_ROOT="${HOME}/.pyenv"
eval "$(pyenv init -)"

# install Python 3.8
pyenv install 3.8.10

# make it available globally
pyenv global system 3.8.10
```
</details>


## Poetry

This project uses `poetry` to install and manage dependencies. To install poetry,
follow these [instructions](https://python-poetry.org/docs/master/#installation).

# Installation

1. Create and activate a virtual environment.

```shell
python -m venv env
source env/bin/activate
```

2. Install volttron and start the platform.

```shell
pip install volttron

# Start platform with output going to volttron.log
volttron -vv -l volttron.log &
```

3. Install the volttron platform driver:

```shell
vctl install volttron-platform-driver --vip-identity platform.driver --start
```

4. Install the BACnetProxy agent:

```shell
vctl install volttron-bacnet-proxy --agent-config <path to bacnet proxy agent configuration file>
```

5.  Install the volttron bacnet driver library:

```shell
pip install volttron-lib-bacnet-driver
```

6.  Install a BACnet Driver onto the Platform Driver.

Installing a BACnet driver in the Platform Driver Agent requires adding copies of the device configuration and registry configuration files to the Platform Driver’s configuration store.

Create a config directory and navigate to it:

```shell
mkdir config
cd config
```
Create a file called `bacnet.config`; it should contain a JSON object that specifies the configuration of your BACnet driver. The following JSON is an example:

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

Create another file called `bacnet.csv`; it should contain all the points on the device that you want published to Volttron. The following CSV file is an example:

```csv
Point Name,Volttron Point Name,Units,Unit Details,BACnet Object Type,Property,Writable,Index,Notes
3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T,3820a/Field Bus.3820A CHILLER.AHU-COIL-CHWR-T,degreesFahrenheit,-50.00 to 250.00,analogInput,presentValue,FALSE,3000741,,Primary CHW Return Temp
```

Add the bacnet driver config and bacnet csv file to the Platform Driver configuration store:

```
vctl config store platform.driver bacnet.csv bacnet.csv --csv
vctl config store platform.driver devices/bacnet bacnet1.config
```

7. Observe Data

To see data being published to the bus, install a [Listener Agent](https://pypi.org/project/volttron-listener/):

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

Please see the following helpful guide about [developing modular VOLTTRON agents](https://github.com/eclipse-volttron/volttron-core/blob/develop/DEVELOPING_ON_MODULAR.md)

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
