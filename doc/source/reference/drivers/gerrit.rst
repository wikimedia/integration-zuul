:title: Gerrit Driver

Gerrit
======

`Gerrit`_ is a code review system.  The Gerrit driver supports
sources, triggers, and reporters.

.. _Gerrit: https://www.gerritcodereview.com/

Zuul will need access to a Gerrit user.

Create an SSH keypair for Zuul to use if there isn't one already, and
create a Gerrit user with that key::

  cat ~/id_rsa.pub | ssh -p29418 review.example.com gerrit create-account --ssh-key - --full-name Zuul zuul

.. note:: If you use an RSA key, ensure it is encoded in the PEM
          format (use the ``-t rsa -m PEM`` arguments to
          `ssh-keygen`).

Give that user whatever permissions will be needed on the projects you
want Zuul to report on.  For instance, you may want to grant
``Verified +/-1`` and ``Submit`` to the user.  Additional categories
or values may be added to Gerrit.  Zuul is very flexible and can take
advantage of those.

Connection Configuration
------------------------

The supported options in ``zuul.conf`` connections are:

.. attr:: <gerrit connection>

   .. attr:: driver
      :required:

      .. value:: gerrit

         The connection must set ``driver=gerrit`` for Gerrit connections.

   .. attr:: server
      :required:

      Fully qualified domain name of Gerrit server.

   .. attr:: canonical_hostname

      The canonical hostname associated with the git repos on the
      Gerrit server.  Defaults to the value of
      :attr:`<gerrit connection>.server`.  This is used to identify
      projects from this connection by name and in preparing repos on
      the filesystem for use by jobs.  Note that Zuul will still only
      communicate with the Gerrit server identified by ``server``;
      this option is useful if users customarily use a different
      hostname to clone or pull git repos so that when Zuul places
      them in the job's working directory, they appear under this
      directory name.

   .. attr:: port
      :default: 29418

      Gerrit server port.

   .. attr:: baseurl
      :default: https://{server}

      Path to Gerrit web interface.  Omit the trailing ``/``.

   .. attr:: gitweb_url_template
      :default: {baseurl}/gitweb?p={project.name}.git;a=commitdiff;h={sha}

      Url template for links to specific git shas. By default this will
      point at Gerrit's built in gitweb but you can customize this value
      to point elsewhere (like cgit or github).

      The three values available for string interpolation are baseurl
      which points back to Gerrit, project and all of its safe attributes,
      and sha which is the git sha1.

   .. attr:: user
      :default: zuul

      User name to use when logging into Gerrit via ssh.

   .. attr:: sshkey
      :default: ~zuul/.ssh/id_rsa

      Path to SSH key to use when logging into Gerrit.

   .. attr:: keepalive
      :default: 60

      SSH connection keepalive timeout; ``0`` disables.

   .. attr:: password

      The HTTP authentication password for the user.  This is
      optional, but if it is provided, Zuul will report to Gerrit via
      HTTP rather than SSH.  It is required in order for file and line
      comments to reported (the Gerrit SSH API only supports review
      messages).  Retrieve this password from the ``HTTP Password``
      section of the ``Settings`` page in Gerrit.

   .. attr:: auth_type
      :default: basic

      The HTTP authentication mechanism.

      .. value:: basic

         HTTP Basic authentication; the default for most Gerrit
         installations.

      .. value:: digest

         HTTP Digest authentication; only used in versions of Gerrit
         prior to 2.15.

      .. value:: form

         Zuul will submit a username and password to a form in order
         to authenticate.

      .. value:: gcloud_service

         Only valid when running in Google Cloud.  This will use the
         default service account to authenticate to Gerrit.  Note that
         this will only be used for interacting with the Gerrit API;
         anonymous HTTP access will be used to access the git
         repositories, therefore private repos or draft changes will
         not be available.

   .. attr:: verify_ssl
      :default: true

      When using a self-signed certificate, this may be set to
      ``false`` to disable SSL certificate verification.

Trigger Configuration
---------------------

Zuul works with standard versions of Gerrit by invoking the ``gerrit
stream-events`` command over an SSH connection.  It also reports back
to Gerrit using SSH.

If using Gerrit 2.7 or later, make sure the user is a member of a group
that is granted the ``Stream Events`` permission, otherwise it will not
be able to invoke the ``gerrit stream-events`` command over SSH.

.. attr:: pipeline.trigger.<gerrit source>

   The dictionary passed to the Gerrit pipeline ``trigger`` attribute
   supports the following attributes:

   .. attr:: event
      :required:

      The event name from gerrit.  Examples: ``patchset-created``,
      ``comment-added``, ``ref-updated``.  This field is treated as a
      regular expression.

   .. attr:: branch

      The branch associated with the event.  Example: ``master``.
      This field is treated as a regular expression, and multiple
      branches may be listed.

   .. attr:: ref

      On ref-updated events, the branch parameter is not used, instead
      the ref is provided.  Currently Gerrit has the somewhat
      idiosyncratic behavior of specifying bare refs for branch names
      (e.g., ``master``), but full ref names for other kinds of refs
      (e.g., ``refs/tags/foo``).  Zuul matches this value exactly
      against what Gerrit provides.  This field is treated as a
      regular expression, and multiple refs may be listed.

   .. attr:: ignore-deletes
      :default: true

      When a branch is deleted, a ref-updated event is emitted with a
      newrev of all zeros specified. The ``ignore-deletes`` field is a
      boolean value that describes whether or not these newrevs
      trigger ref-updated events.

   .. attr:: approval

      This is only used for ``comment-added`` events.  It only matches
      if the event has a matching approval associated with it.
      Example: ``Code-Review: 2`` matches a ``+2`` vote on the code
      review category.  Multiple approvals may be listed.

   .. attr:: email

      This is used for any event.  It takes a regex applied on the
      performer email, i.e. Gerrit account email address.  If you want
      to specify several email filters, you must use a YAML list.
      Make sure to use non greedy matchers and to escapes dots!
      Example: ``email: ^.*?@example\.org$``.

   .. attr:: username

      This is used for any event.  It takes a regex applied on the
      performer username, i.e. Gerrit account name.  If you want to
      specify several username filters, you must use a YAML list.
      Make sure to use non greedy matchers and to escapes dots.
      Example: ``username: ^zuul$``.

   .. attr:: comment

      This is only used for ``comment-added`` events.  It accepts a
      list of regexes that are searched for in the comment string. If
      any of these regexes matches a portion of the comment string the
      trigger is matched. ``comment: retrigger`` will match when
      comments containing ``retrigger`` somewhere in the comment text
      are added to a change.

   .. attr:: require-approval

      This may be used for any event.  It requires that a certain kind
      of approval be present for the current patchset of the change
      (the approval could be added by the event in question).  It
      follows the same syntax as :attr:`pipeline.require.<gerrit
      source>.approval`. For each specified criteria there must exist
      a matching approval.

   .. attr:: reject-approval

      This takes a list of approvals in the same format as
      :attr:`pipeline.trigger.<gerrit source>.require-approval` but
      will fail to enter the pipeline if there is a matching approval.

Reporter Configuration
----------------------

Zuul works with standard versions of Gerrit by invoking the ``gerrit``
command over an SSH connection, unless the connection is configured
with an HTTP password, in which case the HTTP API is used.

.. attr:: pipeline.reporter.<gerrit reporter>

   The dictionary passed to the Gerrit reporter is used to provide label
   values to Gerrit.  To set the `Verified` label to `1`, add ``verified:
   1`` to the dictionary.

   The following additional keys are recognized:

   .. attr:: submit
      :default: False

      Set this to ``True`` to submit (merge) the change.

   .. attr:: comment
      :default: True

      If this is true (the default), Zuul will leave review messages
      on the change (including job results).  Set this to false to
      disable this behavior (file and line commands will still be sent
      if present).

A :ref:`connection<connections>` that uses the gerrit driver must be
supplied to the trigger.

Requirements Configuration
--------------------------

As described in :attr:`pipeline.require` and :attr:`pipeline.reject`,
pipelines may specify that items meet certain conditions in order to
be enqueued into the pipeline.  These conditions vary according to the
source of the project in question.  To supply requirements for changes
from a Gerrit source named ``my-gerrit``, create a configuration such
as the following:

.. code-block:: yaml

   pipeline:
     require:
       my-gerrit:
         approval:
           - Code-Review: 2

This indicates that changes originating from the Gerrit connection
named ``my-gerrit`` must have a ``Code-Review`` vote of ``+2`` in
order to be enqueued into the pipeline.

.. attr:: pipeline.require.<gerrit source>

   The dictionary passed to the Gerrit pipeline `require` attribute
   supports the following attributes:

   .. attr:: approval

      This requires that a certain kind of approval be present for the
      current patchset of the change (the approval could be added by
      the event in question).  It takes several sub-parameters, all of
      which are optional and are combined together so that there must
      be an approval matching all specified requirements.

      .. attr:: username

         If present, an approval from this username is required.  It is
         treated as a regular expression.

      .. attr:: email

         If present, an approval with this email address is required.  It is
         treated as a regular expression.

      .. attr:: older-than

         If present, the approval must be older than this amount of time
         to match.  Provide a time interval as a number with a suffix of
         "w" (weeks), "d" (days), "h" (hours), "m" (minutes), "s"
         (seconds).  Example ``48h`` or ``2d``.

      .. attr:: newer-than

         If present, the approval must be newer than this amount
         of time to match.  Same format as "older-than".

      Any other field is interpreted as a review category and value
      pair.  For example ``Verified: 1`` would require that the
      approval be for a +1 vote in the "Verified" column.  The value
      may either be a single value or a list: ``Verified: [1, 2]``
      would match either a +1 or +2 vote.

   .. attr:: open

      A boolean value (``true`` or ``false``) that indicates whether
      the change must be open or closed in order to be enqueued.

   .. attr:: current-patchset

      A boolean value (``true`` or ``false``) that indicates whether the
      change must be the current patchset in order to be enqueued.

   .. attr:: status

      A string value that corresponds with the status of the change
      reported by the trigger.

.. attr:: pipeline.reject.<gerrit source>

   The `reject` attribute is the mirror of the `require` attribute.  It
   also accepts a dictionary under the connection name.  This
   dictionary supports the following attributes:

   .. attr:: approval

      This takes a list of approvals. If an approval matches the
      provided criteria the change can not be entered into the
      pipeline. It follows the same syntax as
      :attr:`pipeline.require.<gerrit source>.approval`.

      Example to reject a change with any negative vote:

      .. code-block:: yaml

         reject:
           my-gerrit:
             approval:
               - Code-Review: [-1, -2]

Reference Pipelines Configuration
---------------------------------

Here is an example of standard pipelines you may want to define:

.. literalinclude:: /examples/pipelines/gerrit-reference-pipelines.yaml
   :language: yaml

Checks Plugin Support (Experimental)
------------------------------------

The Gerrit driver has experimental support for Gerrit's `checks`
plugin.  Neither the `checks` plugin itself nor Zuul's support for it
are stable yet, and this is not recommended for production use.  If
you wish to help develop this support, you should expect to be able to
upgrade both Zuul and Gerrit frequently as the two systems evolve.  No
backward-compatible support will be provided and configurations may
need to be updated frequently.

Caveats include (but are not limited to):

* This documentation is brief.

* Access control for the `checks` API in Gerrit depends on a single
  global administrative permission, ``administrateCheckers``.  This is
  required in order to use the `checks` API and can not be restricted
  by project.  This means that any system using the `checks` API can
  interfere with any other.

* Checkers are restricted to a single project.  This means that a
  system with many projects will require many checkers to be defined
  in Gerrit -- one for each project+pipeline.

* No support is provided for attaching checks to tags or commits,
  meaning that tag, release, and post pipelines are unable to be used
  with the `checks` API and must rely on `stream-events`.

* Sub-checks are not implemented yet, so in order to see the results
  of individual jobs on a change, users must either follow the
  buildset link, or the pipeline must be configured to leave a
  traditional comment.

* Familiarity with the `checks` API is recommended.

* Checkers may not be permanently deleted from Gerrit (only
  "soft-deleted" so they no longer apply), so any experiments you
  perform on a production system will leave data there forever.

In order to use the `checks` API, you must have HTTP access configured
in `zuul.conf`.

There are two ways to configure a pipeline for the `checks` API:
directly referencing the checker UUID, or referencing it's scheme.  It
is hoped that once multi-repository checks are supported, that an
administrator will be able to configure a single checker in Gerrit for
each Zuul pipeline, and those checkers can apply to all repositories.
If and when that happens, we will be able to reference the checker
UUID directly in Zuul's pipeline configuration.  If you only have a
single project, you may find this approach acceptable now.

To use this approach, create a checker named ``zuul:check`` and
configure a pipeline like this:

.. code-block:: yaml

   - pipeline:
       name: check
       manager: independent
       trigger:
         gerrit:
           - event: pending-check
             uuid: 'zuul:check'
       enqueue:
         gerrit:
           checks-api:
             uuid: 'zuul:check'
             state: SCHEDULED
             message: 'Change has been enqueued in check'
       start:
         gerrit:
           checks-api:
             uuid: 'zuul:check'
             state: RUNNING
             message: 'Jobs have started running'
       no-jobs:
         gerrit:
           checks-api:
             uuid: 'zuul:check'
             state: NOT_RELEVANT
             message: 'Change has no jobs configured'
       success:
         gerrit:
           checks-api:
             uuid: 'zuul:check'
             state: SUCCESSFUL
             message: 'Change passed all voting jobs'
       failure:
         gerrit:
           checks-api:
             uuid: 'zuul:check'
             state: FAILED
             message: 'Change failed'

For a system with multiple repositories and one or more checkers for
each repository, the `scheme` approach is recommended.  To use this,
create a checker for each pipeline in each repository.  Give them
names such as ``zuul_check:project1``, ``zuul_gate:project1``,
``zuul_check:project2``, etc.  The part before the ``:`` is the
`scheme`.  Then create a pipeline like this:

.. code-block:: yaml

   - pipeline:
       name: check
       manager: independent
       trigger:
         gerrit:
           - event: pending-check
             scheme: 'zuul_check'
       enqueue:
         gerrit:
           checks-api:
             scheme: 'zuul_check'
             state: SCHEDULED
             message: 'Change has been enqueued in check'
       start:
         gerrit:
           checks-api:
             scheme: 'zuul_check'
             state: RUNNING
             message: 'Jobs have started running'
       no-jobs:
         gerrit:
           checks-api:
             scheme: 'zuul_check'
             state: NOT_RELEVANT
             message: 'Change has no jobs configured'
       success:
         gerrit:
           checks-api:
             scheme: 'zuul_check'
             state: SUCCESSFUL
             message: 'Change passed all voting jobs'
       failure:
         gerrit:
           checks-api:
             scheme: 'zuul_check'
             state: FAILED
             message: 'Change failed'

This will match and report to the appropriate checker for a given
repository based on the scheme you provided.

.. The original design doc may be of use during development:
   https://gerrit-review.googlesource.com/c/gerrit/+/214733
