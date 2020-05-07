:orphan:

Ubuntu
======

We're going to be using Ubuntu on a cloud server for this installation.

Prerequisites
-------------

- Port 9000 must be open and accessible from the Internet so that
  GitHub can communicate with the Zuul web service.

Login to your environment
-------------------------

Since we'll be using a cloud image for Ubuntu, our login user will
be ``ubuntu`` which will also be the staging user for installation of
Zuul and Nodepool.

To get started, ssh to your machine as the ``ubuntu`` user.

.. code-block:: shell

   ssh ubuntu@<ip_address>

Environment Setup
-----------------

First, make sure the system packages are up to date, and then install
some packages which will be required later.  Most of Zuul's binary
dependencies are handled by the bindep program, but a few additional
dependencies are needed to install bindep, and for other commands
which we will use in these instructions.

.. code-block:: shell

   sudo apt-get update
   sudo apt-get install python3-pip git

   # install bindep, the --user setting will install bindep only in
   # the user profile not global.
   pip3 install --user bindep

Install Zookeeper
-----------------

Nodepool uses Zookeeper to keep track of information about the
resources it manages, and it's also how Zuul makes requests to
Nodepool for nodes.

.. code-block:: console

   sudo apt-get install -y zookeeper zookeeperd
