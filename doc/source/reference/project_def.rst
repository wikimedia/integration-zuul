.. _project:

Project
=======

A project corresponds to a source code repository with which Zuul is
configured to interact.  The main responsibility of the project
configuration item is to specify which jobs should run in which
pipelines for a given project.  Within each project definition, a
section for each :ref:`pipeline <pipeline>` may appear.  This
project-pipeline definition is what determines how a project
participates in a pipeline.

Multiple project definitions may appear for the same project (for
example, in a central :term:`config projects <config-project>` as well
as in a repo's own ``.zuul.yaml``).  In this case, all of the project
definitions for the relevant branch are combined (the jobs listed in
all of the matching definitions will be run).  If a project definition
appears in a :term:`config-project`, it will apply to all branches of
the project.  If it appears in a branch of an
:term:`untrusted-project` it will only apply to changes on that
branch.  In the case of an item which does not have a branch (for
example, a tag), all of the project definitions will be combined.

Consider the following project definition::

  - project:
      name: yoyodyne
      check:
        jobs:
          - check-syntax
          - unit-tests
      gate:
        queue: integrated
        jobs:
          - unit-tests
          - integration-tests

The project has two project-pipeline stanzas, one for the ``check``
pipeline, and one for ``gate``.  Each specifies which jobs should run
when a change for that project enters the respective pipeline -- when
a change enters ``check``, the ``check-syntax`` and ``unit-test`` jobs
are run.

Pipelines which use the dependent pipeline manager (e.g., the ``gate``
example shown earlier) maintain separate queues for groups of
projects.  When Zuul serializes a set of changes which represent
future potential project states, it must know about all of the
projects within Zuul which may have an effect on the outcome of the
jobs it runs.  If project *A* uses project *B* as a library, then Zuul
must be told about that relationship so that it knows to serialize
changes to A and B together, so that it does not merge a change to B
while it is testing a change to A.

Zuul could simply assume that all projects are related, or even infer
relationships by which projects a job indicates it uses, however, in a
large system that would become unwieldy very quickly, and
unnecessarily delay changes to unrelated projects.  To allow for
flexibility in the construction of groups of related projects, the
change queues used by dependent pipeline managers are specified
manually.  To group two or more related projects into a shared queue
for a dependent pipeline, set the ``queue`` parameter to the same
value for those projects.

The ``gate`` project-pipeline definition above specifies that this
project participates in the ``integrated`` shared queue for that
pipeline.

.. attr:: project

   The following attributes may appear in a project:

   .. attr:: name

      The name of the project.  If Zuul is configured with two or more
      unique projects with the same name, the canonical hostname for
      the project should be included (e.g., `git.example.com/foo`).
      This can also be a regex. In this case the regex must start with ``^``
      and match the full project name following the same rule as name without
      regex. If not given it is implicitly derived from the project where this
      is defined.

   .. attr:: templates

      A list of :ref:`project-template` references; the
      project-pipeline definitions of each Project Template will be
      applied to this project.  If more than one template includes
      jobs for a given pipeline, they will be combined, as will any
      jobs specified in project-pipeline definitions on the project
      itself.

   .. attr:: default-branch
      :default: master

      The name of a branch that Zuul should check out in jobs if no
      better match is found.  Typically Zuul will check out the branch
      which matches the change under test, or if a job has specified
      an :attr:`job.override-checkout`, it will check that out.
      However, if there is no matching or override branch, then Zuul
      will checkout the default branch.

      Each project may only have one ``default-branch`` therefore Zuul
      will use the first value that it encounters for a given project
      (regardless of in which branch the definition appears).  It may
      not appear in a :ref:`project-template` definition.

   .. attr:: merge-mode
      :default: merge-resolve

      The merge mode which is used by Git for this project.  Be sure
      this matches what the remote system which performs merges (i.e.,
      Gerrit). The requested merge mode will be used by the Github driver
      when performing merges.

      Each project may only have one ``merge-mode`` therefore Zuul
      will use the first value that it encounters for a given project
      (regardless of in which branch the definition appears).  It may
      not appear in a :ref:`project-template` definition.

      It must be one of the following values:

      .. value:: merge

         Uses the default git merge strategy (recursive). This maps to
         the merge mode ``merge`` in Github.

      .. value:: merge-resolve

         Uses the resolve git merge strategy.  This is a very
         conservative merge strategy which most closely matches the
         behavior of Gerrit. This maps to the merge mode ``merge`` in
         Github.

      .. value:: cherry-pick

         Cherry-picks each change onto the branch rather than
         performing any merges. This is not supported by Github.

      .. value:: squash-merge

         Squash merges each change onto the branch. This maps to the
         merge mode ``squash`` in Github.

   .. attr:: vars
      :default: None

      A dictionary of variables to be made available for all jobs in
      all pipelines of this project.  For more information see
      :ref:`variable inheritance <user_jobs_variable_inheritance>`.

   .. attr:: <pipeline>

      Each pipeline that the project participates in should have an
      entry in the project.  The value for this key should be a
      dictionary with the following format:

      .. attr:: jobs
         :required:

         A list of jobs that should be run when items for this project
         are enqueued into the pipeline.  Each item of this list may
         be a string, in which case it is treated as a job name, or it
         may be a dictionary, in which case it is treated as a job
         variant local to this project and pipeline.  In that case,
         the format of the dictionary is the same as the top level
         :attr:`job` definition.  Any attributes set on the job here
         will override previous versions of the job.

      .. attr:: queue

         If this pipeline is a :value:`dependent
         <pipeline.manager.dependent>` pipeline, this specifies the
         name of the shared queue this project is in.  Any projects
         which interact with each other in tests should be part of the
         same shared queue in order to ensure that they don't merge
         changes which break the others.  This is a free-form string;
         just set the same value for each group of projects.

         Each pipeline for a project can only belong to one queue,
         therefore Zuul will use the first value that it encounters.
         It need not appear in the first instance of a :attr:`project`
         stanza; it may appear in secondary instances or even in a
         :ref:`project-template` definition.

         Pipeline managers other than `dependent` do not use this
         attribute, however, it may still be used if
         :attr:`scheduler.relative_priority` is enabled.

         .. note:: This attribute is not evaluated speculatively and
                   its setting shall be merged to be effective.

      .. attr:: debug

         If this is set to `true`, Zuul will include debugging
         information in reports it makes about items in the pipeline.
         This should not normally be set, but in situations were it is
         difficult to determine why Zuul did or did not run a certain
         job, the additional information this provides may help.

      .. attr:: fail-fast
         :default: false

         If this is set to `true`, Zuul will report a build failure
         immediately and abort all still running builds. This can be used
         to save resources in resource constrained environments at the cost
         of potentially requiring multiple attempts if more than one problem
         is present.

         Once this is defined it cannot be overridden afterwards. So this
         can be forced to a specific value by e.g. defining it in a config
         repo.

.. _project-template:

Project Template
================

A Project Template defines one or more project-pipeline definitions
which can be re-used by multiple projects.

A Project Template uses the same syntax as a :ref:`project`
definition, however, in the case of a template, the
:attr:`project.name` attribute does not refer to the name of a
project, but rather names the template so that it can be referenced in
a :ref:`project` definition.

Because Project Templates may be used outside of the projects where
they are defined, they honor the implied branch :ref:`pragmas <pragma>`
(unlike Projects).  The same heuristics described in
:attr:`job.branches` that determine what implied branches a :ref:`job`
will receive apply to Project Templates (with the exception that it is
not possible to explicity set a branch matcher on a Project Template).
