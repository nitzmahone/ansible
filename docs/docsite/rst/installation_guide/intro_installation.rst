.. _installation_guide:
.. _intro_installation_guide:

******************
Installing Ansible
******************

Ansible is an agentless automation tool that you install on a single host (referred to as the control node). From the control node, Ansible can manage an entire fleet of machines and other devices (referred to as managed nodes) remotely via SSH, Powershell remoting, and numerous other transports, all from a simple command-line interface with no databases or daemons required!

.. contents::
  :local:

.. _control_node_requirements:

Control node requirements
=========================

For your control node (the machine that runs Ansible), you can use nearly any UNIX-like machine with Python 3.8 or newer installed.
This includes Red Hat, Debian, Ubuntu, macOS, BSDs, and Windows under a `Windows Subsystem for Linux (WSL) distribution <https://docs.microsoft.com/en-us/windows/wsl/about>`_.
Windows (sans WSL) is not natively supported as a control node; see `Matt Davis's blog post <http://blog.rolpdog.com/2020/03/why-no-ansible-controller-for-windows.html>`_ for more information.

.. _what_version:

Selecting an Ansible package and version to install
===================================================

Ansible's community packages are distributed in two ways: a minimalist language and runtime package called ``ansible-core``, and a much larger "batteries included" package called ``ansible``, which adds a community-curated selection of :ref:`Ansible Collections <_collections>` for automating a wide variety of devices. Choose the package that fits your needs; the following instructions will refer to ``ansible``, but ``ansible-core`` can be substituted if you prefer to start with a more minimal package and install only the Ansible Collections you require.

.. _from_pip:

Installing and upgrading Ansible with ``pip``
---------------------------------------------

For the fastest access to Ansible updates, we recommend installing Ansible control nodes via ``pip``, the Python package manager.

Locating Python
===============

Locate and remember the path to the Python interpreter you wish to use to run Ansible. For OS-specific tips, see :ref:`FIXME`. We'll refer to this Python path in the following instructions as ``(yourpython)``. For example, if you've determined that you want the Python at ``/usr/bin/python3.9`` to be the one that you'll install Ansible under, when you see an instruction like:

.. code-block:: console

    $ (yourpython) -V

You'd type the following at your shell prompt:

.. code-block:: console

    /usr/bin/python3.9 -V


Ensuring ``pip`` is available
=============================

Once you've located a suitable Python interpreter, ensure that the ``pip`` package manager module is available to it. The following instructions will always call the ``pip`` module directly with the Python interpreter you wish, rather than relying on a script wrapper on your ``PATH`` like ``pip3``. This ensures that no matter how many different Python environments might be on your control node, that we're always installing to the same place.

.. code-block:: console

    $ (yourpython) -m pip -V


If all is well, you should see something like the following:

.. code-block:: console

    $ /usr/bin/python3.9 -m pip -V
    pip 21.0.1 from /usr/lib/python3.9/site-packages/pip (python 3.9)

If so, ``pip`` is available, and you can move on to the next step.

If you see an error like ``No module named pip``, you'll need to install ``pip`` under your chosen Python interpreter before proceeding. This may mean installing an additional OS package (for example, ``python3-pip`` on newer Debian and Ubuntu hosts), or installing the latest ``pip`` directly from the Python Packaging Authority by running the following:

.. code-block:: console
    $ curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    $ (yourpython) get-pip.py --user


Installing or upgrading Ansible
===============================

Use ``pip`` in your selected Python environment to install the Ansible package of your choice for the current user:

.. code-block:: console

    $ (yourpython) -m pip install --user ansible

To upgrade an existing Ansible installation in this Python environment to the latest released version, simply add ``--upgrade``:

.. code-block:: console

    $ (yourpython) -m pip install --upgrade --user ansible

To install a specific version of ``ansible-core`` in this Python environment:

.. code-block:: console

    $ (yourpython) -m pip install ansible-core==2.11.4 --user

Installing and running the ``devel`` branch from source
=======================================================

In Ansible 2.10 and later, the `ansible/ansible repository <https://github.com/ansible/ansible>`_ contains the code for basic features and functions, such as copying module code to managed nodes. This code is also known as ``ansible-core``.

New features are added to ``ansible-core`` on a branch called ``devel``. If you are testing new features, fixing bugs, or otherwise working with the development team on changes to the core code, you can install and run ``devel``.

.. note::

    You should only install and run the ``devel`` branch if you are modifying ``ansible-core`` or trying out features under development. This is a rapidly changing source of code and can become unstable at any point.

.. note::

   If you want to use Ansible AWX as the control node, do not install or run the ``devel`` branch of Ansible. Use an OS package manager (like ``apt`` or ``yum``) or ``pip`` to install a stable version.

If you are running Ansible from source, you may also wish to follow the `Ansible GitHub project <https://github.com/ansible/ansible>`_. We track issues, document bugs, and share feature ideas in this and other related repositories.

For more information on getting involved in the Ansible project, see the :ref:`ansible_community_guide`. For more information on creating Ansible modules and Collections, see the :ref:`developer_guide`.

Installing ``devel`` from GitHub with ``pip``
---------------------------------------------

You can install the ``devel`` branch of ``ansible-core`` directly from GitHub with ``pip``:

.. code-block:: bash

    $ python -m pip install --user https://github.com/ansible/ansible/archive/devel.tar.gz

.. note::

  If you have Ansible 2.9 or older installed or Ansible 3, see :ref:`pip_upgrade`.



You can replace ``devel`` in the URL mentioned above, with any other branch or tag on GitHub to install older versions of Ansible (prior to ``ansible-base`` 2.10.), tagged alpha or beta versions, and release candidates. This installs all of Ansible.

.. code-block:: bash

    $ python -m pip install --user https://github.com/ansible/ansible/archive/stable-2.9.tar.gz

See :ref:`from_source` for instructions on how to run ``ansible-core`` directly from source.


Installing ``devel`` from GitHub by cloning
-------------------------------------------

You can install the ``devel`` branch of ``ansible-core`` by cloning the GitHub repository:

.. code-block:: bash

    $ git clone https://github.com/ansible/ansible.git
    $ cd ./ansible

The default branch is ``devel``.

.. _from_source:

Running the ``devel`` branch from a clone
-----------------------------------------

``ansible-core`` is easy to run from source. You do not need ``root`` permissions to use it and there is no software to actually install. No daemons or database setup are required.

Once you have installed the ``ansible-core`` repository by cloning, setup the Ansible environment:

Using Bash:

.. code-block:: bash

    $ source ./hacking/env-setup

Using Fish::

    $ source ./hacking/env-setup.fish

If you want to suppress spurious warnings/errors, use::

    $ source ./hacking/env-setup -q

If you do not have ``pip`` installed in your version of Python, install it::

    $ curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
    $ python get-pip.py --user

Ansible also uses the following Python modules that need to be installed [1]_:

.. code-block:: bash

    $ python -m pip install --user -r ./requirements.txt

To update the ``devel`` branch of ``ansible-core`` on your local machine, use pull-with-rebase so any local changes are replayed.

.. code-block:: bash

    $ git pull --rebase

.. code-block:: bash

    $ git pull --rebase #same as above
    $ git submodule update --init --recursive

After you run the the env-setup script, you will be running from the source code. The default inventory file will be ``/etc/ansible/hosts``. You can optionally specify an inventory file (see :ref:`inventory`) other than ``/etc/ansible/hosts``:

.. code-block:: bash

    $ echo "127.0.0.1" > ~/ansible_hosts
    $ export ANSIBLE_INVENTORY=~/ansible_hosts

You can read more about the inventory file at :ref:`inventory`.

Confirming your installation
============================

Whatever method of installing Ansible you chose, you can test that it is installed correctly with a ping command:

.. code-block:: bash

    $ ansible all -m ping --ask-pass

You can also use "sudo make install".

.. _tagged_releases:

Finding tarballs of tagged releases
===================================

If you are packaging Ansible or wanting to build a local package yourself, and you want to avoid a git checkout, you can use a tarball of a tagged release. You can download the latest stable release from PyPI's `ansible package page <https://pypi.org/project/ansible/>`_. If you need a specific older version, beta version, or release candidate, you can use the pattern ``pypi.python.org/packages/source/a/ansible/ansible-{{VERSION}}.tar.gz``. VERSION must be the full version number, for example 3.1.0 or 4.0.0b2. You can make VERSION a variable in your package managing system that you update in one place whenever you package a new version.

.. note::

	If you are creating your own Ansible package, you must also download or package ``ansible-core`` (or ``ansible-base`` for packages based on 2.10.x) from PyPI as part of your Ansible package. You must specify a particular version. Visit the PyPI project pages to download files for `ansible-core <https://pypi.org/project/ansible-core/>`_ or `ansible-base <https://pypi.org/project/ansible-base/>`_.

These releases are also tagged in the `git repository <https://github.com/ansible/ansible/releases>`_ with the release version.


.. _shell_completion:

Adding Ansible command shell completion
=======================================

As of Ansible 2.9, you can add shell completion of the Ansible command line utilities by installing an optional dependency called ``argcomplete``. ``argcomplete`` supports bash, and has limited support for zsh and tcsh.

You can install ``python-argcomplete`` from EPEL on Red Hat Enterprise based distributions, and or from the standard OS repositories for many other distributions.

For more information about installation and configuration, see the `argcomplete documentation <https://kislyuk.github.io/argcomplete/>`_.

Installing ``argcomplete`` on RHEL, CentOS, or Fedora
-----------------------------------------------------

On Fedora:

.. code-block:: bash

    $ sudo dnf install python-argcomplete

On RHEL and CentOS:

.. code-block:: bash

    $ sudo yum install epel-release
    $ sudo yum install python-argcomplete


Installing ``argcomplete`` with ``apt``
---------------------------------------

.. code-block:: bash

    $ sudo apt install python3-argcomplete


Installing ``argcomplete`` with ``pip``
---------------------------------------

.. code-block:: bash

    $ python -m pip install argcomplete

Configuring ``argcomplete``
---------------------------

There are 2 ways to configure ``argcomplete`` to allow shell completion of the Ansible command line utilities: globally or per command.

Global configuration
^^^^^^^^^^^^^^^^^^^^

Global completion requires bash 4.2.

.. code-block:: bash

    $ sudo activate-global-python-argcomplete

This will write a bash completion file to a global location. Use ``--dest`` to change the location.

Per command configuration
^^^^^^^^^^^^^^^^^^^^^^^^^

If you do not have bash 4.2, you must register each script independently.

.. code-block:: bash

    $ eval $(register-python-argcomplete ansible)
    $ eval $(register-python-argcomplete ansible-config)
    $ eval $(register-python-argcomplete ansible-console)
    $ eval $(register-python-argcomplete ansible-doc)
    $ eval $(register-python-argcomplete ansible-galaxy)
    $ eval $(register-python-argcomplete ansible-inventory)
    $ eval $(register-python-argcomplete ansible-playbook)
    $ eval $(register-python-argcomplete ansible-pull)
    $ eval $(register-python-argcomplete ansible-vault)

You should place the above commands into your shells profile file such as ``~/.profile`` or ``~/.bash_profile``.

Using ``argcomplete`` with zsh or tcsh
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

See the `argcomplete documentation <https://kislyuk.github.io/argcomplete/>`_.


.. seealso::

   :ref:`intro_adhoc`
       Examples of basic commands
   :ref:`working_with_playbooks`
       Learning ansible's configuration management language
   :ref:`installation_faqs`
       Ansible Installation related to FAQs
   `Mailing List <https://groups.google.com/group/ansible-project>`_
       Questions? Help? Ideas?  Stop by the list on Google Groups
   :ref:`communication_irc`
       How to join Ansible chat channels

.. [1] ``paramiko`` was included in Ansible's ``requirements.txt`` prior to 2.8.