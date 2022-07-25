# volttron-lib-bacnet-driver

![Passing?](https://github.com/VOLTTRON/volttron-lib-bacnet-driver/actions/workflows/run_tests.yml/badge.svg)
[![documentation](https://img.shields.io/badge/docs-mkdocs%20material-blue.svg?style=flat)](https://VOLTTRON.github.io/volttron-lib-bacnet-driver/)
[![pypi version](https://img.shields.io/pypi/v/volttron-lib-bacnet-driver.svg)](https://pypi.org/project/volttron-lib-bacnet-driver/)
[![gitter](https://badges.gitter.im/join%20chat.svg)](https://gitter.im/volttron-lib-bacnet-driver/community)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)



# Prerequisites

* Python 3.8
* Poetry 1.2

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

With `pip`:

```shell
python3.8 -m pip install volttron-lib-bacnet-driver

# Develop mode
python3.8 -m pip install --editable volttron-lib-bacnet-driver
```

# Development

## Environment

Set the environment to be in your project directory:

```poetry config virtualenvs.in-project true```

If you want to install all your dependencies, including dependencies to help with developing your agent, run this command:

```poetry install```

If you want to install only the dependencies needed to run your agent, run this command:

```poetry install --no-dev```

Activate the virtual environment:

```shell
# using Poetry
poetry shell

# using 'source' command
source "$(poetry env info --path)/bin/activate"
```

## Source Control

1. To use git to manage version control, create a new git repository in your local agent project.

```git init```

2. Then create a new repo in your Github or Gitlab account. Copy the URL that points to that new repo in
your Github or Gitlab account. This will be known as our 'remote'.

3. Add the remote (i.e. the new repo URL from your Github or Gitlab account) to your local repository. Run the following command:

```git remote add origin <my github/gitlab URL>```

When you push to your repo, note that the default branch is called 'main'.


## Optional Configurations

### Precommit

Note: Ensure that you have created the virtual environment using Poetry

Install pre-commit hooks:

```poetry run pre-commit install```

To run pre-commit on all your files, run this command:

```poetry run pre-commit run --all-files```

If you have precommit installed and you want to ignore running the commit hooks
every time you run a commit, include the `--no-verify` flag in your commit. The following
is an example:

```git commit -m "Some message" --no-verify```


# Publishing to PyPi

Publishing your Driver module to PyPi is automated through the continuous integration workflow provided in `~/.github/workflows/publish_to_pypi.yml`.
You can update that Github Workflow with your credentials to ensure that publishing to PyPi will succeed. The default behavior of
that workflow is to publish to PyPi when a release has been published. If you want to change this behavior, you can modify the
workflow to publish to PyPi based on whatever desired event; see [Github Workflows docs](https://docs.github.com/en/actions/using-workflows/triggering-a-workflow)
on how to change the events that trigger a workflow.


# Documentation

To build the docs, navigate to the 'docs' directory and build the documentation:

```shell
cd docs
make html
```

After the documentation is built, view the documentation in html form in your browser.
The html files will be located in `~<path to project directory>/docs/build/html`.

**PROTIP: To open the landing page of your documentation directly from the command line, run the following command:**

```shell
open <path to project directory>/docs/build/html/index.html
```

This will open the documentation landing page in your default browsert (e.g. Chrome, Firefox).
