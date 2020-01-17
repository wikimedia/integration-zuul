Troubleshooting
---------------

Some advanced troubleshooting options are provided below.  These are
generally very low-level and are not normally required.

.. _debug_gearman:

Gearman Jobs
============

Connecting to Gearman can allow you see if any Zuul components appear
to not be accepting requests correctly.

For unencrypted Gearman connections, you can use telnet to connect to
and check which Zuul components are online::

    telnet <gearman_ip> 4730

For encrypted connections, you will need to provide suitable keys,
e.g::

    openssl s_client -connect localhost:4730 -cert /etc/zuul/ssl/client.pem  -key /etc/zuul/ssl/client.key

Commands available are discussed in the Gearman `administrative
protocol <http://gearman.org/protocol>`__.  Useful commands are
``workers`` and ``status`` which you can run by just typing those
commands once connected to gearman.

For ``status`` you will see output for internal Zuul functions in the
form ``FUNCTION\tTOTAL\tRUNNING\tAVAILABLE_WORKERS``::

  ...
  executor:resume:ze06.openstack.org	0	0	1
  zuul:config_errors_list	0	0	1
  zuul:status_get	0	0	1
  executor:stop:ze11.openstack.org	0	0	1
  zuul:job_list	0	0	1
  zuul:tenant_sql_connection	0	0	1
  executor:resume:ze09.openstack.org	0	0	1
  ...

Thread Dumps and Profiling
==========================

If you send a SIGUSR2 to one of the daemon processes, it will dump a
stack trace for each running thread into its debug log. It is written
under the log bucket ``zuul.stack_dump``.  This is useful for tracking
down deadlock or otherwise slow threads::

  sudo kill -USR2 `cat /var/run/zuul/executor.pid`
  view /var/log/zuul/executor-debug.log +/zuul.stack_dump

When `yappi <https://code.google.com/p/yappi/>`_ (Yet Another Python
Profiler) is available, additional functions' and threads' stats are
emitted as well. The first SIGUSR2 will enable yappi, on the second
SIGUSR2 it dumps the information collected, resets all yappi state and
stops profiling. This is to minimize the impact of yappi on a running
system.
