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

import os
import subprocess

# Configuration file for the Sphinx documentation builder.

# -- Project information

project = 'VOLTTRON BACnet Driver'
copyright = '2022, Pacific Northwest National Lab'
author = 'Pacific Northwest National Lab'

release = '0.2'
version = '0.2.0-rc'

# -- General configuration

extensions = [
    'sphinx.ext.duration',
    'sphinx.ext.doctest',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.intersphinx',
]

intersphinx_mapping = {
    'python': ('https://docs.python.org/3/', None),
    'sphinx': ('https://www.sphinx-doc.org/en/master/', None),
}
intersphinx_disabled_domains = ['std']

templates_path = ['_templates']

# -- Options for HTML output

html_theme = 'sphinx_rtd_theme'

# -- Options for EPUB output
# epub_show_urls = 'footnote'


script_dir = os.path.dirname(os.path.realpath(__file__))
external_docs_root = os.path.join(script_dir, "external-docs")

# Custom event handlers for Volttron #
def setup(app):
    """
    Registers callback method on sphinx events. callback method used to
    dynamically generate api-docs rst files which are then converted to html
    by readthedocs
    :param app:
    """
    # app.connect('builder-inited', generate_apidoc)
    app.connect('builder-inited', generate_agent_docs)

    # app.connect('build-finished', clean_api_rst)
    app.connect('build-finished', clean_agent_docs_rst)


def generate_agent_docs(app):
    os.makedirs(external_docs_root)
    project_name = agent_name= 'volttron-bacnet-proxy'
    agent_repo = f'https://github.com/eclipse-volttron/{agent_name}'
    docs_source_dir = 'docs/source'
    agent_version = 'develop'
    agent_clone_dir = os.path.join(external_docs_root, agent_name)
    subprocess.check_call(["git", "clone", "--no-checkout", agent_repo, project_name + "_docs_root"],
                          cwd=external_docs_root)
    # for 1st version not doing api-docs. If doing api-docs do full checkout, install requirements, run api-docs
    clone_dir = os.path.join(external_docs_root, project_name + "_docs_root")
    subprocess.check_call(["git", "sparse-checkout", "set", docs_source_dir], cwd=clone_dir)
    subprocess.check_call(["git", "checkout", agent_version], cwd=clone_dir)
    doc_index_dir = os.path.join(clone_dir, docs_source_dir)
    os.symlink(doc_index_dir, os.path.join(external_docs_root, project_name))

def clean_agent_docs_rst(app, exception):
    """
    Deletes folder containing all auto generated .rst files at the end of
    sphinx build immaterial of the exit state of sphinx build.
    :param app:
    :param exception:
    """
    global external_docs_root
    import shutil
    if os.path.exists(external_docs_root):
        print("Cleanup: Removing agent docs clone directory {}".format(external_docs_root))
        shutil.rmtree(external_docs_root)