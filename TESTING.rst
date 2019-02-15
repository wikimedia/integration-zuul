===========================
Testing Your OpenStack Code
===========================
------------
A Quickstart
------------

This is designed to be enough information for you to run your first tests.
Detailed information on testing can be found here: https://wiki.openstack.org/wiki/Testing

*Install pip*::

  [apt-get | yum] install python-pip

More information on pip here: http://www.pip-installer.org/en/latest/

*Use pip to install tox*::

  pip install tox

As of zuul v3, a running zookeeper is required to execute tests.

*Install zookeeper*::

  [apt-get | yum] install zookeeperd

*Start zookeeper*::

  service zookeeper start

.. note:: Installing and bulding javascript is not required, but tests that
          depend on the javascript assets having been built will be skipped
          if you don't.

*Install javascript tools*::

  tools/install-js-tools.sh

*Install javascript dependencies*::

  pushd web
  yarn install
  popd

*Build javascript assets*::

  pushd web
  yarn build
  popd

Run The Tests
-------------

*Navigate to the project's root directory and execute*::

  tox

Note: completing this command may take a long time (depends on system resources)
also, you might not see any output until tox is complete.

Information about tox can be found here: http://testrun.org/tox/latest/


Run The Tests in One Environment
--------------------------------

Tox will run your entire test suite in the environments specified in the project tox.ini::

  [tox]

  envlist = <list of available environments>

To run the test suite in just one of the environments in envlist execute::

  tox -e <env>
so for example, *run the test suite in py35*::

  tox -e py35

Run One Test
------------

To run individual tests with tox::

  tox -e <env> -- path.to.module.Class.test

For example, to *run a single Zuul test*::

  tox -e py35 -- tests.unit.test_scheduler.TestScheduler.test_jobs_executed

To *run one test in the foreground* (after previously having run tox
to set up the virtualenv)::

  .tox/py35/bin/stestr run -t tests.unit.test_scheduler.TestScheduler.test_jobs_executed

List Failing Tests
------------------

  .tox/py35/bin/activate
  stestr failing --list

Hanging Tests
-------------

The following will run each test in turn and print the name of the
test as it is run::

  . .tox/py35/bin/activate
  stestr run

You can compare the output of that to::

  python -m testtools.run discover --list

Need More Info?
---------------

More information about stestr: http://stestr.readthedocs.io/en/latest/
