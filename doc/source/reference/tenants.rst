:title: Tenant Configuration

.. _tenant-config:

Tenant Configuration
====================

After ``zuul.conf`` is configured, Zuul component servers will be able
to start, but a tenant configuration is required in order for Zuul to
perform any actions.  The tenant configuration file specifies upon
which projects Zuul should operate.  These repositories are grouped
into tenants.  The configuration of each tenant is separate from the
rest (no pipelines, jobs, etc are shared between them).

A project may appear in more than one tenant; this may be useful if
you wish to use common job definitions across multiple tenants.

Actions normally available to the Zuul operator only can be performed by specific
users on Zuul's REST API, if admin rules are listed for the tenant. Admin rules
are also defined in the tenant configuration file.

The tenant configuration file is specified by the
:attr:`scheduler.tenant_config` setting in ``zuul.conf``.  It is a
YAML file which, like other Zuul configuration files, is a list of
configuration objects, though only two types of objects are supported:
``tenant`` and ``admin-rule``.

Alternatively the :attr:`scheduler.tenant_config_script`
can be the path to an executable that will be executed and its stdout
used as the tenant configuration. The executable must return a valid
tenant YAML formatted output.

Tenant
------

A tenant is a collection of projects which share a Zuul
configuration. Some examples of tenant definitions are:

.. code-block:: yaml

   - tenant:
       name: my-tenant
       max-nodes-per-job: 5
       exclude-unprotected-branches: false
       report-build-page: true
       source:
         gerrit:
           config-projects:
             - common-config
             - shared-jobs:
                 include: job
           untrusted-projects:
             - zuul/zuul-jobs:
                 shadow: common-config
             - project1
             - project2:
                 exclude-unprotected-branches: true

.. code-block:: yaml

   - tenant:
       name: my-tenant
       admin-rules:
         - acl1
         - acl2
       source:
         gerrit:
           config-projects:
             - common-config
           untrusted-projects:
             - exclude:
                 - job
                 - semaphore
                 - project
                 - project-template
                 - nodeset
                 - secret
               projects:
                 - project1
                 - project2:
                     exclude-unprotected-branches: true

.. attr:: tenant

   The following attributes are supported:

   .. attr:: name
      :required:

      The name of the tenant.  This may appear in URLs, paths, and
      monitoring fields, and so should be restricted to URL friendly
      characters (ASCII letters, numbers, hyphen and underscore) and
      you should avoid changing it unless necessary.

   .. attr:: source
      :required:

      A dictionary of sources to consult for projects.  A tenant may
      contain projects from multiple sources; each of those sources
      must be listed here, along with the projects it supports.  The
      name of a :ref:`connection<connections>` is used as the
      dictionary key (e.g. ``gerrit`` in the example above), and the
      value is a further dictionary containing the keys below.

   The next two attributes, **config-projects** and
   **untrusted-projects** provide the bulk of the information for
   tenant configuration.  They list all of the projects upon which
   Zuul will act.

   The order of the projects listed in a tenant is important.  A job
   which is defined in one project may not be redefined in another
   project; therefore, once a job appears in one project, a project
   listed later will be unable to define a job with that name.
   Further, some aspects of project configuration (such as the merge
   mode) may only be set on the first appearance of a project
   definition.

   Zuul loads the configuration from all **config-projects** in the
   order listed, followed by all **untrusted-projects** in order.

   .. attr:: config-projects

      A list of projects to be treated as :term:`config projects
      <config-project>` in this tenant.  The jobs in a config project
      are trusted, which means they run with extra privileges, do not
      have their configuration dynamically loaded for proposed
      changes, and Zuul config files are only searched for in the
      ``master`` branch.

      The items in the list follow the same format described in
      **untrusted-projects**.

   .. attr:: untrusted-projects

      A list of projects to be treated as untrusted in this tenant.
      An :term:`untrusted-project` is the typical project operated on
      by Zuul.  Their jobs run in a more restrictive environment, they
      may not define pipelines, their configuration dynamically
      changes in response to proposed changes, and Zuul will read
      configuration files in all of their branches.

      .. attr:: <project>

         The items in the list may either be simple string values of
         the project names, or a dictionary with the project name as
         key and the following values:

         .. attr:: include

            Normally Zuul will load all of the :ref:`configuration-items`
            appropriate for the type of project (config or untrusted)
            in question.  However, if you only want to load some
            items, the **include** attribute can be used to specify
            that *only* the specified items should be loaded.
            Supplied as a string, or a list of strings.

            The following **configuration items** are recognized:

            * pipeline
            * job
            * semaphore
            * project
            * project-template
            * nodeset
            * secret

         .. attr:: exclude

            A list of **configuration items** that should not be loaded.

         .. attr:: shadow

            A list of projects which this project is permitted to
            shadow.  Normally, only one project in Zuul may contain
            definitions for a given job.  If a project earlier in the
            configuration defines a job which a later project
            redefines, the later definition is considered an error and
            is not permitted.  The **shadow** attribute of a project
            indicates that job definitions in this project which
            conflict with the named projects should be ignored, and
            those in the named project should be used instead.  The
            named projects must still appear earlier in the
            configuration.  In the example above, if a job definition
            appears in both the ``common-config`` and ``zuul-jobs``
            projects, the definition in ``common-config`` will be
            used.

         .. attr:: exclude-unprotected-branches

            Define if unprotected github branches should be
            processed. Defaults to the tenant wide setting of
            exclude-unprotected-branches.

         .. attr:: extra-config-paths

            Normally Zuul loads in-repo configuration from the first
            of these paths:

            * zuul.yaml
            * zuul.d/*
            * .zuul.yaml
            * .zuul.d/*

            If this option is supplied then, after the normal process
            completes, Zuul will also load any configuration found in
            the files or paths supplied here.  This can be a string or
            a list.  If a list of multiple items, Zuul will load
            configuration from *all* of the items in the list (it will
            not stop at the first extra configuration found).
            Directories should be listed with a trailing ``/``.  Example:

            .. code-block:: yaml

               extra-config-paths:
                 - zuul-extra.yaml
                 - zuul-extra.d/

            This feature may be useful to allow a project that
            primarily holds shared jobs or roles to include additional
            in-repo configuration for its own testing (which may not
            be relevant to other users of the project).

      .. attr:: <project-group>

         The items in the list are dictionaries with the following
         attributes. A **configuration items** definition is applied
         to the list of projects.

         .. attr:: include

            A list of **configuration items** that should be loaded.

         .. attr:: exclude

            A list of **configuration items** that should not be loaded.

         .. attr:: projects

            A list of **project** items.

   .. attr:: max-nodes-per-job
      :default: 5

      The maximum number of nodes a job can request.  A value of
      '-1' value removes the limit.

   .. attr:: max-job-timeout
      :default: 10800

      The maximum timeout for jobs. A value of '-1' value removes the limit.

   .. attr:: exclude-unprotected-branches
      :default: false

      When using a branch and pull model on a shared GitHub repository
      there are usually one or more protected branches which are gated
      and a dynamic number of personal/feature branches which are the
      source for the pull requests. These branches can potentially
      include broken Zuul config and therefore break the global tenant
      wide configuration. In order to deal with this Zuul's operations
      can be limited to the protected branches which are gated. This
      is a tenant wide setting and can be overridden per project.
      This currently only affects GitHub projects.

   .. attr:: default-parent
      :default: base

      If a job is defined without an explicit :attr:`job.parent`
      attribute, this job will be configured as the job's parent.
      This allows an administrator to configure a default base job to
      implement local policies such as node setup and artifact
      publishing.

   .. attr:: default-ansible-version

      Default ansible version to use for jobs that doesn't specify a version.
      See :attr:`job.ansible-version` for details.

   .. attr:: allowed-triggers
      :default: all connections

      The list of connections a tenant can trigger from. When set, this setting
      can be used to restrict what connections a tenant can use as trigger.
      Without this setting, the tenant can use any connection as a trigger.

   .. attr:: allowed-reporters
      :default: all connections

      The list of connections a tenant can report to. When set, this setting
      can be used to restrict what connections a tenant can use as reporter.
      Without this setting, the tenant can report to any connection.

   .. attr:: allowed-labels
      :default: []

      The list of labels (as strings or regular expressions) a tenant
      can use in a job's nodeset. When set, this setting can be used
      to restrict what labels a tenant can use.  Without this setting,
      the tenant can use any labels.

   .. attr:: disallowed-labels
      :default: []

      The list of labels (as strings or regular expressions) a tenant
      is forbidden to use in a job's nodeset. When set, this setting
      can be used to restrict what labels a tenant can use.  Without
      this setting, the tenant can use any labels permitted by
      :attr:`tenant.allowed-labels`.  This check is applied after the
      check for `allowed-labels` and may therefore be used to further
      restrict the set of permitted labels.

   .. attr:: report-build-page
      :default: false

      If this is set to ``true``, then Zuul will use the URL of the
      build page in Zuul's web interface when reporting to the code
      review system.  In this case, :attr:`job.success-url` and
      :attr:`job.failure-url` are ignored for the report (though they
      are still used on the status page before the buildset is
      complete and reported).

      This requires that all the pipelines in the tenant have a
      :ref:`SQL reporter<sql_reporter>` configured, and at least one of
      :attr:`tenant.web-root` or :attr:`web.root` must be defined.

   .. attr:: web-root

      If this tenant has a whitelabeled installation of zuul-web, set
      its externally visible URL here (e.g.,
      ``https://tenant.example.com/``).  This will override the
      :attr:`web.root` setting when constructing URLs for this tenant.

   .. attr:: admin-rules

      A list of access rules for the tenant. These rules are checked to grant
      privileged actions to users at the tenant level, through Zuul's REST API.

      At least one rule in the list must match for the user to be allowed the
      privileged action.

      More information on tenant-scoped actions can be found in
      :ref:`tenant-scoped-rest-api`.


.. _admin_rule_definition:

Access Rule
-----------

An access rule is a set of conditions the claims of a user's JWT must match
in order to be allowed to perform protected actions at a tenant's level.

The protected actions available at tenant level are **autohold**, **enqueue**
or **dequeue**.

.. note::

   Rules can be overridden by the ``zuul.admin`` claim in a token if if matches
   an authenticator configuration where `allow_authz_override` is set to true.
   See :ref:`Zuul web server's configuration <web-server-tenant-scoped-api>` for
   more details.

Below are some examples of how access rules can be defined:

.. code-block:: yaml

   - admin-rule:
       name: affiliate_or_admin
       conditions:
         - resources_access:
             account:
               roles: "affiliate"
           iss: external_institution
         - resources_access.account.roles: "admin"
   - admin-rule:
       name: alice_or_bob
       conditions:
         - zuul_uid: alice
         - zuul_uid: bob


.. attr:: admin-rule

   The following attributes are supported:

   .. attr:: name
      :required:

      The name of the rule, so that it can be referenced in the ``admin-rules``
      attribute of a tenant's definition. It must be unique.

   .. attr:: conditions
      :required:

      This is the list of conditions that define a rule. A JWT must match **at
      least one** of the conditions for the rule to apply. A condition is a
      dictionary where keys are claims. **All** the associated values must
      match the claims in the user's token; in other words the condition dictionary
      must be a "sub-dictionary" of the user's JWT.

      Zuul's authorization engine will adapt matching tests depending on the
      nature of the claim in the token, eg:

      * if the claim is a JSON list, check that the condition value is in the
        claim
      * if the claim is a string, check that the condition value is equal to
        the claim's value

      The claim names can also be written in the XPath format for clarity: the
      condition

      .. code-block:: yaml

        resources_access:
          account:
            roles: "affiliate"

      is equivalent to the condition

      .. code-block:: yaml

        resources_access.account.roles: "affiliate"

      The special ``zuul_uid`` claim refers to the ``uid_claim`` setting in an
      authenticator's configuration. By default it refers to the ``sub`` claim
      of a token. For more details see the :ref:`configuration section
      <web-server-tenant-scoped-api>` for Zuul web server.

      Under the above example, the following token would match rules
      ``affiliate_or_admin`` and ``alice_or_bob``:

      .. code-block:: javascript

        {
         'iss': 'external_institution',
         'aud': 'my_zuul_deployment',
         'exp': 1234567890,
         'iat': 1234556780,
         'sub': 'alice',
         'resources_access': {
             'account': {
                 'roles': ['affiliate', 'other_role']
             }
         },
        }

      And this token would only match rule ``affiliate_or_admin``:

      .. code-block:: javascript

        {
         'iss': 'some_other_institution',
         'aud': 'my_zuul_deployment',
         'exp': 1234567890,
         'sub': 'carol',
         'iat': 1234556780,
         'resources_access': {
             'account': {
                 'roles': ['admin', 'other_role']
             }
         },
        }
