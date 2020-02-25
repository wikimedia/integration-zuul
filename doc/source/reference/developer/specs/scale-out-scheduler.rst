Scale out scheduler
===================

.. warning:: This is not authoritative documentation.  These features
   are not currently available in Zuul.  They may change significantly
   before final implementation, or may never be fully completed.

Zuul has a microservices architecture with the goal of no single point of
failure in mind. This has not yet been achieved for the zuul-scheduler
component.

Especially within large Zuul deployments with many also long running jobs the
cost of a scheduler crash can be quite high. In this case currently all
in-flight jobs are lost and need to be restarted. A scale out scheduler approach
can avoid this.

The same problem holds true when updating the scheduler. Currently there is no
possibility to upgrade the scheduler without downtime. While the pipeline state
can be saved and re-enqueued this still looses all in-flight jobs. Further on a
larger deployment the startup of the scheduler easily can be in the multi minute
range. Having the ability to do zero downtime upgrades can make updates much
more easier.

Further having multiple schedulers can facilitate parallel processing of several
tenants and help reducing global locks within installations with many tenants.

In this document we will outline an approach towards a completely single point
of failure free zuul system. This will be a transition with multiple phases.


Status quo
----------

Zuul is an event driven system with several event loops that interact with each
other:

* Driver event loop: Drivers like Github or Gerrit have its own event loops.
  They perform preprocessing of the received events and add events into the
  scheduler event loop.

* Scheduler event loop: This event loop processes the pipelines and
  reconfigurations.

All of these event loops currently run within the scheduler process without
persisting their state. So the path to a scale out scheduler involves mainly
making all event loops scale out capable.



Target architecture
-------------------

In addition to the event loops mentioned above we need an additional event queue
per pipeline. This will make it easy to process several pipelines in parallel. A
new driver event would first be processed in the driver event queue. This will
add a new event into the scheduler event queue. The scheduler event queue then
checks which pipeline may be interested in this event according to the tenant
configuration and layout. Based on this the event is dispatched to all matching
pipeline queues.

As it is today different event types will have different priorities. This will
be expressed like in node-requests with a prefix.

The event queues will be stored in Zookeeper in the following paths:

* ``/zuul/events/connection/<connection name>/<sequence>``: Event queue of a
  connection

* ``/zuul/events/scheduler-global/<prio>-<sequence>``: Global event queue of
  scheduler

* ``/zuul/events/tenant/<tenant name>/<pipeline>/<prio>-<sequence>``: Pipeline
  event queue

In order to make reconfigurations efficient we also need to store the parsed
branch config in Zookeeper. This makes it possible to create the current layout
without the need to ask the mergers multiple times for the configuration. This
also can be used by zuul-web to keep an up-to-date layout that can be used for
api requests.

We also need to store the pipeline state in Zookeeper. This will be similar to
the status.json but also needs to contain the frozen jobs and their current
state.

Further we need to replace gearman by Zookeeper as rpc mechanism to the
executors. This will make it possible that different schedulers can continue
smoothly with pipeline execution. The jobs will be stored in
``/zuul/jobs/<tenant>/<sequence>``.


Driver event ingestion
----------------------

Currently the drivers immediately get events from Gerrit or Github, process them
and forward the events to the scheduler event loop. Thus currently all events
are lost during a downtime of the zuul-scheduler. In order to decouple this we
can push the raw events into Zookeeper and pop them in the driver event loop.

We will split the drivers into an event receiving and an event processing
component. The event receiving component will store the events in a squenced
znode in the path ``/zuul/events/connection/<connection name>/<sequence>``.
The event receiving part may or may not run within the scheduler context.
The event processing part will be part of the scheduler context.

There are three types of event receive mechanisms in Zuul:

* Active event gathering: The connection actively subscribes for events (Gerrit)
  or generates them itself (git, timer, zuul)

* Passive event gathering: The events are sent to zuul from outside (Github
  webhooks)

* Internal event generation: The events are generated within zuul itself and
  typically get injected directly into the scheduler event loop and thus don't
  need to be changed in this phase.

The active and passive event gathering need to be handled slightly different.

Active event gathering
~~~~~~~~~~~~~~~~~~~~~~

This is mainly done by the Gerrit driver. We actively maintain a connection to
the target and receive events. This means that if we have more than one instance
we need to find a way to handle duplicated events. This type of event gathering
can run within the scheduler process. Optionally if there is interest we can
also make it possible to run this as a standalone process.

We can utilize leader election to make sure there is exactly one instance
receiving the events. This makes sure that we don't need to handle duplicated
events at all. A drawback is that there is a short time when the current leader
stops until the next leader has started event gathering. This could lead to a
few missed events. But as this is the most easiest way we can accept this in
the initial version.

If there is a need to guarantee that there is no missed event during a
leadership change the above algorithm can be enhanced later with parallel
gathering and deduplication strategies. As this is much more complicated this
will not be in scope of this spec but subject to later enhancements.


Passive event gathering
~~~~~~~~~~~~~~~~~~~~~~~

In case of passive event gathering the events are sent to Zuul typically via
webhooks. These types of events will be received in zuul-web that stores them in
Zookeeper. This type of event gathering is used for example by the Github and
Gerrit driver (other drivers, possibly implemented before this is realized,
should be checked too). In this
case we can have multiple instances but still receive only one event. So we
don't need to take special care of event deduplication or leader election -
multiple instances behind a load balancer are save to use and recommended for
such passive event gathering.


Store unparsed branch config in Zookeeper
-----------------------------------------

We need to store the global configuration in zookeeper. However zookeeper is not
designed as a database with a large amount of data we should store as little as
possible in zookeeper. Thus we only store the per project-branch unparsed config
in zookeeper. From this every part of zuul like the scheduler or also zuul-web
can quickly recalculate the layout of each tenant and keep it up to date by
watching for changes in the unparsed project-branch-config. We will lock the
complete global config with one lock and maintain a checkpoint version of it.
This way each component can watch the config version number and react
accordingly. Although we lock the complete global config we still should store
the actual config in distinct nodes per project and branch. This is needed
because of the 1MB limit per znode in zookeeper. It further makes it less
expensive to cache the global config in each component as this cache will be
updated incrementally.

The configs will be stored in the path ``/zuul/config/<project>/<branch>`` per
branch segmented in ``/zuul/config/<project>/<branch>/<path-to-config>/<shard>``.
The ``shard`` is a sequence number and will be used to store larger than 1MB
files due to the limitation mentioned above.


Store pipeline and tenant state in Zookeeper
--------------------------------------------

The pipeline state is similar to the current status.json. However the frozen
jobs and their state are needed for seemless continuation of the pipeline
execution on a different scheduler. Further this can make it easy to generate
the status.json directly in zuul-web by inspecting the data in Zookeeper.
Buildsets that are enqueued in a pipeline will be stored in
``/zuul/tenant/<tenant>/pipeline/<pipeline>/queue/<queue>/<buildset uuid>``.

Each buildset will contain a child znode per job that holds a data structure
with the frozen job as well as the current state. This will also contain a
reference to the node request that was used for this job. When the node request
is fulfilled the pipeline processor creates an execution-request which
will be locked by an executor before processing the job. The buildset will
contain a link to the execution request. The executor will accept the referenced
node request, lock the nodes and run the job. If the job needs to be canceled
the pipeline processor just pulls the execution-request. The executor will
notice this, abort the job and return the nodes.

We also need to store tenant state like semaphores in Zookeeper. This will be
stored in ``/zuul/tenant/<tenant>/semaphores/<name>``.


Mandatory SQL connection
------------------------

Currently the times database is stored on the local filesystem of the scheduler.
We already have an optional SQL database that holds the needed information. We
need to be able to rely on this information so we'll make the SQL db mandatory.

Zuul currently supports multiple database connections. At the moment the SQL
reporters can be configured on pipelines. This should be changed to global and
tenant-based SQL reporters. When we make the database
connection mandatory zuul needs to know which one is the primary database
connection. If there is only one configured connection it will be automatically
the primary. If there are more configured connections one will need to be
configured as primary database. Reporters will use the primary
database in any case.

The primary database can be used to query the last 10 successful build times
and use this as the times database.


Executor via Zookeeper
----------------------

In order to prepare for distributed pipeline execution we need to use Zookeeper
for scheduling jobs on the executors. This is needed so that any scheduler can
take over a pipeline execution without having to restart jobs.

As discribed above the executor will look for builds. These will be stored in
``/zuul/builds/<sequence>``. The builds will contain every information that is
needed to run the job. The builds are stored outside of the pipeline itself
for two reasons. First the executors should not need to do a deep search when
looking for new builds to do. Second this makes it clear that they are not
subject to the pipeline lock but have their own locks. However the buildsets
in the pipeline will contain a reference to their builds.

During the lifecycle of a build the executor can update the states by their own.
But should enqueue result events to the corresponding pipeline event queue as
pipeline processing relies on build started, paused, finished events. The
lifecycle will be as follows.

* Build gets created in state REQUESTED
* Executor locks it and sets the state to RUNNING. It will enqueue a build
  started event to the pipeline event queue.
* If requested the executor sets the state to PAUSED after the run phase and
  enqueues a build paused event to the pipeline event queue
* If build is PAUSED a resume can be requested by the pipeline processor by
  adding an empty ``resume`` child node to the build. This way we don't have to
  update a locked znode while ignoring the lock. The executor will then change
  the state back to RUNNING and continue the execution.
* When the build is finished the executor changes the state to COMPLETE, unlocks
  the build and enqueues a build finished event to the pipeline.
* If a build should be canceled the pipeline processor adds a ``cancel`` child
  znode that will be recognized by the executor which will act accordingly.

It can be that an executor crashes. In this case it will loose the lock. We need
to be able to recover from this and emit the right event to the pipeline.
Such a lost builds can be detected if it is in a state other than REQUESTED or
COMPLETED but unlocked. Any executor that sees such a request while looking for
new builds to execute will lock and mark it as COMPLETED and failed. It then
will emit a build completed event such that the pipeline event processor can
react on this and reschedule the build. There is no special handling needed to
return the nodes as in this case the failing executor will also loose its lock
on the nodes so they will be deleted or recycled by nodepool automatically.


Parallelize pipeline processing
-------------------------------

Once we have the above data in place we can create the per pipeline event and
the global scheduler event queues in Zookeeper. The global scheduler event queue
will receive the trigger, management and result events that are not tenant
specific. The purpose of this queue is to take these events and dispatch them to
the pipeline queues of the tenants as appropriate. This event queue can easily
processed using a locking mechanism.

We also have tenant global events like tenant reconfigurations. These need
exclusive access to all pipelines in the tenant. So we need a two layer locking
approach during pipeline processing. At first we need an RW lock at the tenant
level. This will allow to be locked by all pipeline processors at the same time
(call them readers as they don't modify the global tenant state). Management
events (e.g. tenant-reconfiguration) however will get this lock exclusive (call
them writers as they modify the global tenant state).

Each pipeline processor will loop over all pipelines that have outstanding
events. Before processing an event it will first try to lock the tenant. If it
fails it will continue with pipelines in the the next tenant having outstanding
events. If it got the tenant lock it will try to lock the pipeline. If it fails
it will continue with the next pipeline. If it succeeds it will process all
outstanding events of that pipeline. To prevent starvation of tenants we can
define a max processing time after which the pipeline processor will switch to
the next tenant or pipeline even if there are outstanding events.

In order to reduce stalls when doing reconfigurations or tenant reconfigurations
we can run one pipeline processor in one thread and reconfigurations in a
separate thread(s). This way a tenant that is running a longer reconfiguration
won't block other tenants.


Zuul-web changes
----------------

Now zuul can be changed to directly use the data in Zookeeper instead if
asking the scheduler via gearman.


Security considerations
-----------------------

When switching the executor job queue to Zookeeper we need to take precautions
because this will also contain decrypted secrets. In order to secure this
communication channel we need to make sure that we use authenticated and
encrypted connections to zookeeper.

* There is already a change that adds Zookeeper auth:
  https://review.openstack.org/619156
* Kazoo SSL support just has landed: https://github.com/python-zk/kazoo/pull/513

Further we will encrypt every secret that is stored in zookeeper using a
symmetric cipher with a shared key that is known to all zuul services but not
zookeeper. This way we can avoid dumping decrypted secrets into the transaction
log of zookeeper.


Roadmap
-------

In order to manage the workload and minimize rebasing efforts, we suggest to
break the above into smaller changes. Each such change should be then
implemented separately.

#. Mandatory SQL connection, definition of primary SQL connection and add SQL
   reporters for tenants
#. Storing parsed branch config in zookeeper
#. Storing raw events in zookeeper using drivers or a separate service
#. Event queue per pipeline
#. Storing pipeline state and tenant state in zookeeper
#. Adapt drivers to pop events from zookeeper (split drivers into event
   receiving and event processing components)
#. Parallel pipeline processing
#. Switch to using zookeeper instead of gearman
