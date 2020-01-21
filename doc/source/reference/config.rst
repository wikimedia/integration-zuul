:title: Configuration Syntax

.. _project-config:

Configuration Syntax
====================

The following sections describe the main part of Zuul's configuration.
All of what follows is found within files inside of the repositories
that Zuul manages.

Security Contexts
-----------------

When a system administrator configures Zuul to operate on a project,
they specify one of two security contexts for that project.  A
*config-project* is one which is primarily tasked with holding
configuration information and job content for Zuul.  Jobs which are
defined in a config-project are run with elevated privileges, and all
Zuul configuration items are available for use.  Base jobs (that is,
jobs without a parent) may only be defined in config-projects.  It is
expected that changes to config-projects will undergo careful scrutiny
before being merged.

An *untrusted-project* is a project whose primary focus is not to
operate Zuul, but rather it is one of the projects being tested or
deployed.  The Zuul configuration language available to these projects
is somewhat restricted (as detailed in individual sections below), and
jobs defined in these projects run in a restricted execution
environment since they may be operating on changes which have not yet
undergone review.

Configuration Loading
---------------------

When Zuul starts, it examines all of the git repositories which are
specified by the system administrator in :ref:`tenant-config` and
searches for files in the root of each repository. Zuul looks first
for a file named ``zuul.yaml`` or a directory named ``zuul.d``, and if
they are not found, ``.zuul.yaml`` or ``.zuul.d`` (with a leading
dot). In the case of an :term:`untrusted-project`, the configuration
from every branch is included, however, in the case of a
:term:`config-project`, only the ``master`` branch is examined.

When a change is proposed to one of these files in an
untrusted-project, the configuration proposed in the change is merged
into the running configuration so that any changes to Zuul's
configuration are self-testing as part of that change.  If there is a
configuration error, no jobs will be run and the error will be
reported by any applicable pipelines.  In the case of a change to a
config-project, the new configuration is parsed and examined for
errors, but the new configuration is not used in testing the change.
This is because configuration in config-projects is able to access
elevated privileges and should always be reviewed before being merged.

As soon as a change containing a Zuul configuration change merges to
any Zuul-managed repository, the new configuration takes effect
immediately.

.. _configuration-items:

Configuration Items
-------------------

The ``zuul.yaml`` and ``.zuul.yaml`` configuration files are
YAML-formatted and are structured as a series of items, each of which
is referenced below.

In the case of a ``zuul.d`` (or ``.zuul.d``) directory, Zuul recurses
the directory and extends the configuration using all the .yaml files
in the sorted path order.  For example, to keep job's variants in a
separate file, it needs to be loaded after the main entries, for
example using number prefixes in file's names::

* zuul.d/pipelines.yaml
* zuul.d/projects.yaml
* zuul.d/01_jobs.yaml
* zuul.d/02_jobs-variants.yaml

Below are references to the different configuration items you may use within
the YAML files:

.. toctree::
   :maxdepth: 1

   pipeline_def
   job_def
   project_def
   secret_def
   nodeset_def
   semaphore_def
   pragma_def
