.. _job:

Job
===

A job is a unit of work performed by Zuul on an item enqueued into a
pipeline.  Items may run any number of jobs (which may depend on each
other).  Each job is an invocation of an Ansible playbook with a
specific inventory of hosts.  The actual tasks that are run by the job
appear in the playbook for that job while the attributes that appear in the
Zuul configuration specify information about when, where, and how the
job should be run.

Jobs in Zuul support inheritance.  Any job may specify a single parent
job, and any attributes not set on the child job are collected from
the parent job.  In this way, a configuration structure may be built
starting with very basic jobs which describe characteristics that all
jobs on the system should have, progressing through stages of
specialization before arriving at a particular job.  A job may inherit
from any other job in any project (however, if the other job is marked
as :attr:`job.final`, jobs may not inherit from it).

A job with no parent is called a *base job* and may only be defined in
a :term:`config-project`.  Every other job must have a parent, and so
ultimately, all jobs must have an inheritance path which terminates at
a base job.  Each tenant has a default parent job which will be used
if no explicit parent is specified.

Multiple job definitions with the same name are called variants.
These may have different selection criteria which indicate to Zuul
that, for instance, the job should behave differently on a different
git branch.  Unlike inheritance, all job variants must be defined in
the same project.  Some attributes of jobs marked :attr:`job.final`
may not be overridden.

When Zuul decides to run a job, it performs a process known as
freezing the job.  Because any number of job variants may be
applicable, Zuul collects all of the matching variants and applies
them in the order they appeared in the configuration.  The resulting
frozen job is built from attributes gathered from all of the
matching variants.  In this way, exactly what is run is dependent on
the pipeline, project, branch, and content of the item.

In addition to the job's main playbook, each job may specify one or
more pre- and post-playbooks.  These are run, in order, before and
after (respectively) the main playbook.  They may be used to set up
and tear down resources needed by the main playbook.  When combined
with inheritance, they provide powerful tools for job construction.  A
job only has a single main playbook, and when inheriting from a
parent, the child's main playbook overrides (or replaces) the
parent's.  However, the pre- and post-playbooks are appended and
prepended in a nesting fashion.  So if a parent job and child job both
specified pre and post playbooks, the sequence of playbooks run would
be:

* parent pre-run playbook
* child pre-run playbook
* child playbook
* child post-run playbook
* parent post-run playbook

Further inheritance would nest even deeper.

Here is an example of two job definitions:

.. code-block:: yaml

   - job:
       name: base
       pre-run: copy-git-repos
       post-run: copy-logs

   - job:
       name: run-tests
       parent: base
       nodeset:
         nodes:
           - name: test-node
             label: fedora

.. attr:: job

   The following attributes are available on a job; all are optional
   unless otherwise specified:

   .. attr:: name
      :required:

      The name of the job.  By default, Zuul looks for a playbook with
      this name to use as the main playbook for the job.  This name is
      also referenced later in a project pipeline configuration.

   .. TODO: figure out how to link the parent default to tenant.default.parent

   .. attr:: parent
      :default: Tenant default-parent

      Specifies a job to inherit from.  The parent job can be defined
      in this or any other project.  Any attributes not specified on a
      job will be collected from its parent.  If no value is supplied
      here, the job specified by :attr:`tenant.default-parent` will be
      used.  If **parent** is set to ``null`` (which is only valid in
      a :term:`config-project`), this is a :term:`base job`.

   .. attr:: description

      A textual description of the job.  Not currently used directly
      by Zuul, but it is used by the zuul-sphinx extension to Sphinx
      to auto-document Zuul jobs (in which case it is interpreted as
      ReStructuredText.

   .. attr:: final
      :default: false

      To prevent other jobs from inheriting from this job, and also to
      prevent changing execution-related attributes when this job is
      specified in a project's pipeline, set this attribute to
      ``true``.

      .. warning::

         It is possible to circumvent the use of `final` in an
         :term:`untrusted-project` by creating a change which
         `Depends-On` a change which alters `final`.  This limitation
         does not apply to jobs in a :term:`config-project`.

   .. attr:: protected
      :default: false

      When set to ``true`` only jobs defined in the same project may inherit
      from this job. Once this is set to ``true`` it cannot be reset to
      ``false``.

      .. warning::

         It is possible to circumvent the use of `protected` in an
         :term:`untrusted-project` by creating a change which
         `Depends-On` a change which alters `protected`.  This
         limitation does not apply to jobs in a
         :term:`config-project`.

   .. attr:: abstract
      :default: false

      To indicate a job is not intended to be run directly, but
      instead must be inherited from, set this attribute to ``true``.

      .. warning::

         It is possible to circumvent the use of `abstract` in an
         :term:`untrusted-project` by creating a change which
         `Depends-On` a change which alters `abstract`.  This
         limitation does not apply to jobs in a
         :term:`config-project`.

   .. attr:: success-message
      :default: SUCCESS

      Normally when a job succeeds, the string ``SUCCESS`` is reported
      as the result for the job.  If set, this option may be used to
      supply a different string.

   .. attr:: failure-message
      :default: FAILURE

      Normally when a job fails, the string ``FAILURE`` is reported as
      the result for the job.  If set, this option may be used to
      supply a different string.

   .. attr:: success-url

      When a job succeeds, this URL is reported along with the result.
      If this value is not supplied, Zuul uses the content of the job
      :ref:`return value <return_values>` **zuul.log_url**.  This is
      recommended as it allows the code which stores the URL to the
      job artifacts to report exactly where they were stored.  To
      override this value, or if it is not set, supply an absolute URL
      in this field.  If a relative URL is supplied in this field, and
      **zuul.log_url** is set, then the two will be combined to
      produce the URL used for the report.  This can be used to
      specify that certain jobs should "deep link" into the stored job
      artifacts.

   .. attr:: failure-url

      When a job fails, this URL is reported along with the result.
      Otherwise behaves the same as **success-url**.

   .. attr:: hold-following-changes
      :default: false

      In a dependent pipeline, this option may be used to indicate
      that no jobs should start on any items which depend on the
      current item until this job has completed successfully.  This
      may be used to conserve build resources, at the expense of
      inhibiting the parallelization which speeds the processing of
      items in a dependent pipeline.

   .. attr:: voting
      :default: true

      Indicates whether the result of this job should be used in
      determining the overall result of the item.

   .. attr:: semaphore

      The name of a :ref:`semaphore` which should be acquired and
      released when the job begins and ends.  If the semaphore is at
      maximum capacity, then Zuul will wait until it can be acquired
      before starting the job. The format is either a string or a
      dictionary. If it's a string it references a semaphore using the
      default value for :attr:`job.semaphore.resources-first`.

      .. attr:: name
         :required:

         The name of the referenced semaphore

      .. attr:: resources-first
         :default: False

         By default a semaphore is acquired before the resources are
         requested. However in some cases the user wants to run cheap
         jobs as quickly as possible in a consecutive manner. In this
         case :attr:`job.semaphore.resources-first` can be enabled to
         request the resources before locking the semaphore. This can
         lead to some amount of blocked resources while waiting for the
         semaphore so this should be used with caution.

   .. attr:: tags

      Metadata about this job.  Tags are units of information attached
      to the job; they do not affect Zuul's behavior, but they can be
      used within the job to characterize the job.  For example, a job
      which tests a certain subsystem could be tagged with the name of
      that subsystem, and if the job's results are reported into a
      database, then the results of all jobs affecting that subsystem
      could be queried.  This attribute is specified as a list of
      strings, and when inheriting jobs or applying variants, tags
      accumulate in a set, so the result is always a set of all the
      tags from all the jobs and variants used in constructing the
      frozen job, with no duplication.

   .. attr:: provides

      A list of free-form strings which identifies resources provided
      by this job which may be used by other jobs for other changes
      using the :attr:`job.requires` attribute.

   .. attr:: requires

      A list of free-form strings which identify resources which may
      be provided by other jobs for other changes (via the
      :attr:`job.provides` attribute) that are used by this job.

      When Zuul encounters a job with a `requires` attribute, it
      searches for those values in the `provides` attributes of any
      jobs associated with any queue items ahead of the current
      change.  In this way, if a change uses either git dependencies
      or a `Depends-On` header to indicate a dependency on another
      change, Zuul will be able to determine that the parent change
      affects the run-time environment of the child change.  If such a
      relationship is found, the job with `requires` will not start
      until all of the jobs with matching `provides` have completed or
      paused.  Additionally, the :ref:`artifacts <return_artifacts>`
      returned by the `provides` jobs will be made available to the
      `requires` job.

      For example, a job which produces a builder container image in
      one project that is then consumed by a container image build job
      in another project might look like this:

      .. code-block:: yaml

         - job:
             name: build-builder-image
             provides: images

         - job:
             name: build-final-image
             requires: images

         - project:
             name: builder-project
             check:
               jobs:
                 - build-builder-image

         - project:
             name: final-project
             check:
               jobs:
                 - build-final-image

   .. attr:: secrets

      A list of secrets which may be used by the job.  A
      :ref:`secret` is a named collection of private information
      defined separately in the configuration.  The secrets that
      appear here must be defined in the same project as this job
      definition.

      Each item in the list may may be supplied either as a string,
      in which case it references the name of a :ref:`secret` definition,
      or as a dict. If an element in this list is given as a dict, it
      may have the following fields:

      .. attr:: name
         :required:

         The name to use for the Ansible variable into which the secret
         content will be placed.

      .. attr:: secret
         :required:

         The name to use to find the secret's definition in the
         configuration.

      .. attr:: pass-to-parent
         :default: false

         A boolean indicating that this secret should be made
         available to playbooks in parent jobs.  Use caution when
         setting this value -- parent jobs may be in different
         projects with different security standards.  Setting this to
         true makes the secret available to those playbooks and
         therefore subject to intentional or accidental exposure.

      For example:

      .. code-block:: yaml

         - secret:
             name: important-secret
             data:
               key: encrypted-secret-key-data

         - job:
             name: amazing-job
             secrets:
               - name: ssh_key
                 secret: important-secret

      will result in the following being passed as a variable to the playbooks
      in ``amazing-job``:

      .. code-block:: yaml

         ssh_key:
           key: descrypted-secret-key-data

   .. attr:: nodeset

      The nodes which should be supplied to the job.  This parameter
      may be supplied either as a string, in which case it references
      a :ref:`nodeset` definition which appears elsewhere in the
      configuration, or a dictionary, in which case it is interpreted
      in the same way as a Nodeset definition, though the ``name``
      attribute should be omitted (in essence, it is an anonymous
      Nodeset definition unique to this job).  See the :ref:`nodeset`
      reference for the syntax to use in that case.

      If a job has an empty (or no) :ref:`nodeset` definition, it will
      still run and is able to perform limited actions within the Zuul
      executor sandbox (e.g. copying files or triggering APIs).  Note
      so-called "executor-only" jobs run with an empty inventory, and
      hence Ansible's *implicit localhost*.  This means an
      executor-only playbook must be written to match ``localhost``
      directly; i.e.

      .. code-block:: yaml

          - hosts: localhost
            tasks:
             ...

      not with ``hosts: all`` (as this does not match the implicit
      localhost and the playbook will not run).  There are also
      caveats around things like enumerating the magic variable
      ``hostvars`` in this situation.  For more information see the
      Ansible `implicit localhost documentation
      <https://docs.ansible.com/ansible/latest/inventory/implicit_localhost.html>`__.

      A useful example of executor-only jobs is saving resources by
      directly utilising the prior results from testing a committed
      change.  For example, a review which updates documentation
      source files would generally test validity by building a
      documentation tree.  When this change is committed, the
      pre-built output can be copied in an executor-only job directly
      to the publishing location in a post-commit *promote* pipeline;
      avoiding having to use a node to rebuild the documentation for
      final publishing.

   .. attr:: override-checkout

      When Zuul runs jobs for a proposed change, it normally checks
      out the branch associated with that change on every project
      present in the job.  If jobs are running on a ref (such as a
      branch tip or tag), then that ref is normally checked out.  This
      attribute is used to override that behavior and indicate that
      this job should, regardless of the branch for the queue item,
      use the indicated ref (i.e., branch or tag) instead.  This can
      be used, for example, to run a previous version of the software
      (from a stable maintenance branch) under test even if the change
      being tested applies to a different branch (this is only likely
      to be useful if there is some cross-branch interaction with some
      component of the system being tested).  See also the
      project-specific :attr:`job.required-projects.override-checkout`
      attribute to apply this behavior to a subset of a job's
      projects.

      This value is also used to help select which variants of a job
      to run.  If ``override-checkout`` is set, then Zuul will use
      this value instead of the branch of the item being tested when
      collecting jobs to run.

   .. attr:: timeout

      The time in seconds that the job should be allowed to run before
      it is automatically aborted and failure is reported.  If no
      timeout is supplied, the job may run indefinitely.  Supplying a
      timeout is highly recommended.

      This timeout only applies to the pre-run and run playbooks in a
      job.

   .. attr:: post-timeout

      The time in seconds that each post playbook should be allowed to run
      before it is automatically aborted and failure is reported.  If no
      post-timeout is supplied, the job may run indefinitely.  Supplying a
      post-timeout is highly recommended.

      The post-timeout is handled separately from the above timeout because
      the post playbooks are typically where you will copy jobs logs.
      In the event of the pre-run or run playbooks timing out we want to
      do our best to copy the job logs in the post-run playbooks.

   .. attr:: attempts
      :default: 3

      When Zuul encounters an error running a job's pre-run playbook,
      Zuul will stop and restart the job.  Errors during the main or
      post-run -playbook phase of a job are not affected by this
      parameter (they are reported immediately).  This parameter
      controls the number of attempts to make before an error is
      reported.

   .. attr:: pre-run

      The name of a playbook or list of playbooks to run before the
      main body of a job.  The full path to the playbook in the repo
      where the job is defined is expected.

      When a job inherits from a parent, the child's pre-run playbooks
      are run after the parent's.  See :ref:`job` for more
      information.

   .. attr:: post-run

      The name of a playbook or list of playbooks to run after the
      main body of a job.  The full path to the playbook in the repo
      where the job is defined is expected.

      When a job inherits from a parent, the child's post-run
      playbooks are run before the parent's.  See :ref:`job` for more
      information.

   .. attr:: cleanup-run

      The name of a playbook or list of playbooks to run after a job
      execution. The full path to the playbook in the repo
      where the job is defined is expected.

      The cleanup phase is performed unconditionally of the job's result,
      even when the job is canceled. Cleanup results are not taken into
      account.

   .. attr:: run

      The name of a playbook or list of playbooks for this job.  If it
      is not supplied, the parent's playbook will be used (and likewise
      up the inheritance chain).  The full path within the repo is
      required.  Example:

      .. code-block:: yaml

         run: playbooks/job-playbook.yaml

   .. attr:: ansible-version

      The ansible version to use for all playbooks of the job. This can be
      defined at the following layers of configuration where the first match
      takes precedence:

      * :attr:`job.ansible-version`
      * :attr:`tenant.default-ansible-version`
      * :attr:`scheduler.default_ansible_version`
      * Zuul default version

      The supported ansible versions are:

      .. program-output:: zuul-manage-ansible -l

   .. attr:: roles

      .. code-block:: yaml
         :name: job-roles-example

         - job:
             name: myjob
             roles:
               - zuul: myorg/our-roles-project
               - zuul: myorg/ansible-role-foo
                 name: foo

      A list of Ansible roles to prepare for the job.  Because a job
      runs an Ansible playbook, any roles which are used by the job
      must be prepared and installed by Zuul before the job begins.
      This value is a list of dictionaries, each of which indicates
      one of two types of roles: a Galaxy role, which is simply a role
      that is installed from Ansible Galaxy, or a Zuul role, which is
      a role provided by a project managed by Zuul.  Zuul roles are
      able to benefit from speculative merging and cross-project
      dependencies when used by playbooks in untrusted projects.
      Roles are added to the Ansible role path in the order they
      appear on the job -- roles earlier in the list will take
      precedence over those which follow.

      In the case of job inheritance or variance, the roles used for
      each of the playbooks run by the job will be only those which
      were defined along with that playbook.  If a child job inherits
      from a parent which defines a pre and post playbook, then the
      pre and post playbooks it inherits from the parent job will run
      only with the roles that were defined on the parent.  If the
      child adds its own pre and post playbooks, then any roles added
      by the child will be available to the child's playbooks.  This
      is so that a job which inherits from a parent does not
      inadvertently alter the behavior of the parent's playbooks by
      the addition of conflicting roles.  Roles added by a child will
      appear before those it inherits from its parent.

      If a project used for a Zuul role has branches, the usual
      process of selecting which branch should be checked out applies.
      See :attr:`job.override-checkout` for a description of that
      process and how to override it.  As a special case, if the role
      project is the project in which this job definition appears,
      then the branch in which this definition appears will be used.
      In other words, a playbook may not use a role from a different
      branch of the same project.

      A project which supplies a role may be structured in one of two
      configurations: a bare role (in which the role exists at the
      root of the project), or a contained role (in which the role
      exists within the ``roles/`` directory of the project, perhaps
      along with other roles).  In the case of a contained role, the
      ``roles/`` directory of the project is added to the role search
      path.  In the case of a bare role, the project itself is added
      to the role search path.  In case the name of the project is not
      the name under which the role should be installed (and therefore
      referenced from Ansible), the ``name`` attribute may be used to
      specify an alternate.

      A job automatically has the project in which it is defined added
      to the roles path if that project appears to contain a role or
      ``roles/`` directory.  By default, the project is added to the
      path under its own name, however, that may be changed by
      explicitly listing the project in the roles list in the usual
      way.

      .. attr:: galaxy

         .. warning:: Galaxy roles are not yet implemented.

         The name of the role in Ansible Galaxy.  If this attribute is
         supplied, Zuul will search Ansible Galaxy for a role by this
         name and install it.  Mutually exclusive with ``zuul``;
         either ``galaxy`` or ``zuul`` must be supplied.

      .. attr:: zuul

         The name of a Zuul project which supplies the role.  Mutually
         exclusive with ``galaxy``; either ``galaxy`` or ``zuul`` must
         be supplied.

      .. attr:: name

         The installation name of the role.  In the case of a bare
         role, the role will be made available under this name.
         Ignored in the case of a contained role.

   .. attr:: required-projects

      A list of other projects which are used by this job.  Any Zuul
      projects specified here will also be checked out by Zuul into
      the working directory for the job.  Speculative merging and
      cross-repo dependencies will be honored.

      The format for this attribute is either a list of strings or
      dictionaries.  Strings are interpreted as project names,
      dictionaries, if used, may have the following attributes:

      .. attr:: name
         :required:

         The name of the required project.

      .. attr:: override-checkout

         When Zuul runs jobs for a proposed change, it normally checks
         out the branch associated with that change on every project
         present in the job.  If jobs are running on a ref (such as a
         branch tip or tag), then that ref is normally checked out.
         This attribute is used to override that behavior and indicate
         that this job should, regardless of the branch for the queue
         item, use the indicated ref (i.e., branch or tag) instead,
         for only this project.  See also the
         :attr:`job.override-checkout` attribute to apply the same
         behavior to all projects in a job.

         This value is also used to help select which variants of a
         job to run.  If ``override-checkout`` is set, then Zuul will
         use this value instead of the branch of the item being tested
         when collecting any jobs to run which are defined in this
         project.

   .. attr:: vars

      A dictionary of variables to supply to Ansible.  When inheriting
      from a job (or creating a variant of a job) vars are merged with
      previous definitions.  This means a variable definition with the
      same name will override a previously defined variable, but new
      variable names will be added to the set of defined variables.

   .. attr:: extra-vars

      A dictionary of variables to be passed to ansible command-line
      using the --extra-vars flag. Note by using extra-vars, these
      variables always win precedence.

   .. attr:: host-vars

      A dictionary of host variables to supply to Ansible.  The keys
      of this dictionary are node names as defined in a
      :ref:`nodeset`, and the values are dictionaries of variables,
      just as in :attr:`job.vars`.

   .. attr:: group-vars

      A dictionary of group variables to supply to Ansible.  The keys
      of this dictionary are node groups as defined in a
      :ref:`nodeset`, and the values are dictionaries of variables,
      just as in :attr:`job.vars`.

   An example of three kinds of variables:

   .. code-block:: yaml

      - job:
          name: variable-example
          nodeset:
            nodes:
              - name: controller
                label: fedora-27
              - name: api1
                label: centos-7
              - name: api2
                label: centos-7
            groups:
              - name: api
                nodes:
                  - api1
                  - api2
         vars:
           foo: "this variable is visible to all nodes"
         host-vars:
           controller:
             bar: "this variable is visible only on the controller node"
         group-vars:
           api:
             baz: "this variable is visible on api1 and api2"
   .. attr:: dependencies

      A list of other jobs upon which this job depends.  Zuul will not
      start executing this job until all of its dependencies have
      completed successfully, and if one or more of them fail, this
      job will not be run.

      The format for this attribute is either a list of strings or
      dictionaries.  Strings are interpreted as job names,
      dictionaries, if used, may have the following attributes:

      .. attr:: name
         :required:

         The name of the required job.

      .. attr:: soft
         :default: false

         A boolean value which indicates whether this job is a *hard*
         or *soft* dependency.  A *hard* dependency will cause an
         error if the specified job is not run.  That is, if job B
         depends on job A, but job A is not run for any reason (for
         example, it containes a file matcher which does not match),
         then Zuul will not run any jobs and report an error.  A
         *soft* dependency will simply be ignored if the dependent job
         is not run.

   .. attr:: allowed-projects

      A list of Zuul projects which may use this job.  By default, a
      job may be used by any other project known to Zuul, however,
      some jobs use resources or perform actions which are not
      appropriate for other projects.  In these cases, a list of
      projects which are allowed to use this job may be supplied.  If
      this list is not empty, then it must be an exhaustive list of
      all projects permitted to use the job.  The current project
      (where the job is defined) is not automatically included, so if
      it should be able to run this job, then it must be explicitly
      listed.  This setting is ignored by :term:`config projects
      <config-project>` -- they may add any job to any project's
      pipelines.  By default, all projects may use the job.

      If a :attr:`job.secrets` is used in a job definition in an
      :term:`untrusted-project`, `allowed-projects` is automatically
      set to the current project only, and can not be overridden.
      However, a :term:`config-project` may still add such a job to
      any project's pipeline.  Apply caution when doing so as other
      projects may be able to expose the source project's secrets.

      .. warning::

         It is possible to circumvent the use of `allowed-projects` in
         an :term:`untrusted-project` by creating a change which
         `Depends-On` a change which alters `allowed-projects`.  This
         limitation does not apply to jobs in a
         :term:`config-project`, or jobs in an `untrusted-project`
         which use a secret.

   .. attr:: post-review
      :default: false

      A boolean value which indicates whether this job may only be
      used in pipelines where :attr:`pipeline.post-review` is
      ``true``.  This is automatically set to ``true`` if this job
      uses a :ref:`secret` and is defined in a :term:`untrusted-project`.
      It may be explicitly set to obtain the same behavior for jobs
      defined in :term:`config projects <config-project>`.  Once this
      is set to ``true`` anywhere in the inheritance hierarchy for a job,
      it will remain set for all child jobs and variants (it can not be
      set to ``false``).

      .. warning::

         It is possible to circumvent the use of `post-review` in an
         :term:`untrusted-project` by creating a change which
         `Depends-On` a change which alters `post-review`.  This
         limitation does not apply to jobs in a
         :term:`config-project`, or jobs in an `untrusted-project`
         which use a secret.

   .. attr:: branches

      A regular expression (or list of regular expressions) which
      describe on what branches a job should run (or in the case of
      variants, to alter the behavior of a job for a certain branch).

      This attribute is not inherited in the usual manner.  Instead,
      it is used to determine whether each variant on which it appears
      will be used when running the job.

      If there is no job definition for a given job which matches the
      branch of an item, then that job is not run for the item.
      Otherwise, all of the job variants which match that branch are
      used when freezing the job.  However, if
      :attr:`job.override-checkout` or
      :attr:`job.required-projects.override-checkout` are set for a
      project, Zuul will attempt to use the job variants which match
      the values supplied in ``override-checkout`` for jobs defined in
      those projects.  This can be used to run a job defined in one
      project on another project without a matching branch.

      This example illustrates a job called *run-tests* which uses a
      nodeset based on the current release of an operating system to
      perform its tests, except when testing changes to the stable/2.0
      branch, in which case it uses an older release:

      .. code-block:: yaml

         - job:
             name: run-tests
             nodeset: current-release

         - job:
             name: run-tests
             branches: stable/2.0
             nodeset: old-release

      In some cases, Zuul uses an implied value for the branch
      specifier if none is supplied:

      * For a job definition in a :term:`config-project`, no implied
        branch specifier is used.  If no branch specifier appears, the
        job applies to all branches.

      * In the case of an :term:`untrusted-project`, if the project
        has only one branch, no implied branch specifier is applied to
        :ref:`job` definitions.  If the project has more than one
        branch, the branch containing the job definition is used as an
        implied branch specifier.

      This allows for the very simple and expected workflow where if a
      project defines a job on the ``master`` branch with no branch
      specifier, and then creates a new branch based on ``master``,
      any changes to that job definition within the new branch only
      affect that branch, and likewise, changes to the master branch
      only affect it.

      See :attr:`pragma.implied-branch-matchers` for how to override
      this behavior on a per-file basis.

   .. attr:: files

      This indicates that the job should only run on changes where the
      specified files are modified.  Unlike **branches**, this value
      is subject to inheritance and overriding, so only the final
      value is used to determine if the job should run. This is a
      regular expression or list of regular expressions.

   .. attr:: irrelevant-files

      This is a negative complement of **files**.  It indicates that
      the job should run unless *all* of the files changed match this
      list.  In other words, if the regular expression ``docs/.*`` is
      supplied, then this job will not run if the only files changed
      are in the docs directory.  A regular expression or list of
      regular expressions.

   .. attr:: match-on-config-updates
      :default: true

      If this is set to ``true`` (the default), then the job's file
      matchers are ignored if a change alters the job's configuration.
      This means that changes to jobs with file matchers will be
      self-testing without requiring that the file matchers include
      the Zuul configuration file defining the job.
