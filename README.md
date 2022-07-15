# volttron-lib-bacnet

[![ci](https://github.com/VOLTTRON/volttron-lib-bacnet/workflows/ci/badge.svg)](https://github.com/VOLTTRON/volttron-lib-bacnet/actions?query=workflow%3Aci)
[![documentation](https://img.shields.io/badge/docs-mkdocs%20material-blue.svg?style=flat)](https://VOLTTRON.github.io/volttron-lib-bacnet/)
[![pypi version](https://img.shields.io/pypi/v/volttron-lib-bacnet.svg)](https://pypi.org/project/volttron-lib-bacnet/)
[![gitter](https://badges.gitter.im/join%20chat.svg)](https://gitter.im/volttron-lib-bacnet/community)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

This project contains Drivers supported and maintained by the Volttron team.

## Requirements

volttron-lib-bacnet requires Python 3.8 or above.

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

## Installation

With `pip`:
```bash
python3.8 -m pip install volttron-lib-bacnet
```

With [`pipx`](https://github.com/pipxproject/pipx):
```bash
python3.8 -m pip install --user pipx

pipx install --python python3.8 volttron-lib-bacnet
```
