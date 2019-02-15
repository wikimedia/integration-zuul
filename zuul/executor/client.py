# Copyright 2012 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import gear
import json
import logging
import os
import time
import threading
from uuid import uuid4

import zuul.model
from zuul.lib.config import get_default
from zuul.lib.gear_utils import getGearmanFunctions
from zuul.lib.jsonutil import json_dumps
from zuul.model import Build


class GearmanCleanup(threading.Thread):
    """ A thread that checks to see if outstanding builds have
    completed without reporting back. """
    log = logging.getLogger("zuul.GearmanCleanup")

    def __init__(self, gearman):
        threading.Thread.__init__(self)
        self.daemon = True
        self.gearman = gearman
        self.wake_event = threading.Event()
        self._stopped = False

    def stop(self):
        self._stopped = True
        self.wake_event.set()

    def run(self):
        while True:
            self.wake_event.wait(300)
            if self._stopped:
                return
            try:
                self.gearman.lookForLostBuilds()
            except Exception:
                self.log.exception("Exception checking builds:")


def getJobData(job):
    if not len(job.data):
        return {}
    d = job.data[-1]
    if not d:
        return {}
    return json.loads(d)


class ZuulGearmanClient(gear.Client):
    def __init__(self, zuul_gearman):
        super(ZuulGearmanClient, self).__init__('Zuul Executor Client')
        self.__zuul_gearman = zuul_gearman

    def handleWorkComplete(self, packet):
        job = super(ZuulGearmanClient, self).handleWorkComplete(packet)
        self.__zuul_gearman.onBuildCompleted(job)
        return job

    def handleWorkFail(self, packet):
        job = super(ZuulGearmanClient, self).handleWorkFail(packet)
        self.__zuul_gearman.onBuildCompleted(job)
        return job

    def handleWorkException(self, packet):
        job = super(ZuulGearmanClient, self).handleWorkException(packet)
        self.__zuul_gearman.onBuildCompleted(job)
        return job

    def handleWorkStatus(self, packet):
        job = super(ZuulGearmanClient, self).handleWorkStatus(packet)
        self.__zuul_gearman.onWorkStatus(job)
        return job

    def handleWorkData(self, packet):
        job = super(ZuulGearmanClient, self).handleWorkData(packet)
        self.__zuul_gearman.onWorkStatus(job)
        return job

    def handleDisconnect(self, job):
        job = super(ZuulGearmanClient, self).handleDisconnect(job)
        self.__zuul_gearman.onDisconnect(job)

    def handleStatusRes(self, packet):
        try:
            job = super(ZuulGearmanClient, self).handleStatusRes(packet)
        except gear.UnknownJobError:
            handle = packet.getArgument(0)
            for build in self.__zuul_gearman.builds.values():
                if build.__gearman_job.handle == handle:
                    self.__zuul_gearman.onUnknownJob(job)


class ExecutorClient(object):
    log = logging.getLogger("zuul.ExecutorClient")

    def __init__(self, config, sched):
        self.config = config
        self.sched = sched
        self.builds = {}
        self.meta_jobs = {}  # A list of meta-jobs like stop or describe

        server = config.get('gearman', 'server')
        port = get_default(self.config, 'gearman', 'port', 4730)
        ssl_key = get_default(self.config, 'gearman', 'ssl_key')
        ssl_cert = get_default(self.config, 'gearman', 'ssl_cert')
        ssl_ca = get_default(self.config, 'gearman', 'ssl_ca')
        self.gearman = ZuulGearmanClient(self)
        self.gearman.addServer(server, port, ssl_key, ssl_cert, ssl_ca)

        self.cleanup_thread = GearmanCleanup(self)
        self.cleanup_thread.start()

    def stop(self):
        self.log.debug("Stopping")
        self.cleanup_thread.stop()
        self.cleanup_thread.join()
        self.gearman.shutdown()
        self.log.debug("Stopped")

    def execute(self, job, item, pipeline, dependent_changes=[],
                merger_items=[]):
        tenant = pipeline.tenant
        uuid = str(uuid4().hex)
        nodeset = item.current_build_set.getJobNodeSet(job.name)
        self.log.info(
            "Execute job %s (uuid: %s) on nodes %s for change %s "
            "with dependent changes %s" % (
                job, uuid, nodeset, item.change, dependent_changes))

        project = dict(
            name=item.change.project.name,
            short_name=item.change.project.name.split('/')[-1],
            canonical_hostname=item.change.project.canonical_hostname,
            canonical_name=item.change.project.canonical_name,
            src_dir=os.path.join('src', item.change.project.canonical_name),
        )

        zuul_params = dict(build=uuid,
                           buildset=item.current_build_set.uuid,
                           ref=item.change.ref,
                           pipeline=pipeline.name,
                           job=job.name,
                           voting=job.voting,
                           project=project,
                           tenant=tenant.name,
                           timeout=job.timeout,
                           jobtags=sorted(job.tags),
                           _inheritance_path=list(job.inheritance_path))
        if job.override_checkout:
            zuul_params['override_checkout'] = job.override_checkout
        if hasattr(item.change, 'branch'):
            zuul_params['branch'] = item.change.branch
        if hasattr(item.change, 'tag'):
            zuul_params['tag'] = item.change.tag
        if hasattr(item.change, 'number'):
            zuul_params['change'] = str(item.change.number)
        if hasattr(item.change, 'url'):
            zuul_params['change_url'] = item.change.url
        if hasattr(item.change, 'patchset'):
            zuul_params['patchset'] = str(item.change.patchset)
        if hasattr(item.change, 'message'):
            zuul_params['message'] = item.change.message
        if (hasattr(item.change, 'oldrev') and item.change.oldrev
            and item.change.oldrev != '0' * 40):
            zuul_params['oldrev'] = item.change.oldrev
        if (hasattr(item.change, 'newrev') and item.change.newrev
            and item.change.newrev != '0' * 40):
            zuul_params['newrev'] = item.change.newrev
        zuul_params['projects'] = {}  # Set below
        zuul_params['items'] = dependent_changes
        zuul_params['child_jobs'] = list(item.job_graph.getDirectDependentJobs(
            job.name))

        params = dict()
        params['job'] = job.name
        params['timeout'] = job.timeout
        params['post_timeout'] = job.post_timeout
        params['items'] = merger_items
        params['projects'] = []
        if hasattr(item.change, 'branch'):
            params['branch'] = item.change.branch
        else:
            params['branch'] = None
        params['override_branch'] = job.override_branch
        params['override_checkout'] = job.override_checkout
        params['repo_state'] = item.current_build_set.repo_state

        def make_playbook(playbook):
            d = playbook.toDict()
            for role in d['roles']:
                if role['type'] != 'zuul':
                    continue
                project_metadata = item.layout.getProjectMetadata(
                    role['project_canonical_name'])
                if project_metadata:
                    role['project_default_branch'] = \
                        project_metadata.default_branch
                else:
                    role['project_default_branch'] = 'master'
                role_trusted, role_project = item.layout.tenant.getProject(
                    role['project_canonical_name'])
                role_connection = role_project.source.connection
                role['connection'] = role_connection.connection_name
                role['project'] = role_project.name
            return d

        if job.name != 'noop':
            params['playbooks'] = [make_playbook(x) for x in job.run]
            params['pre_playbooks'] = [make_playbook(x) for x in job.pre_run]
            params['post_playbooks'] = [make_playbook(x) for x in job.post_run]

        nodes = []
        for node in nodeset.getNodes():
            n = node.toDict()
            n.update(dict(name=node.name, label=node.label))
            nodes.append(n)
        params['nodes'] = nodes
        params['groups'] = [group.toDict() for group in nodeset.getGroups()]
        params['ssh_keys'] = []
        if pipeline.post_review:
            params['ssh_keys'].append(dict(
                name='%s project key' % item.change.project.canonical_name,
                key=item.change.project.private_ssh_key))
        params['vars'] = job.variables
        params['extra_vars'] = job.extra_variables
        params['host_vars'] = job.host_variables
        params['group_vars'] = job.group_variables
        params['zuul'] = zuul_params
        projects = set()
        required_projects = set()

        def make_project_dict(project, override_branch=None,
                              override_checkout=None):
            project_metadata = item.layout.getProjectMetadata(
                project.canonical_name)
            if project_metadata:
                project_default_branch = project_metadata.default_branch
            else:
                project_default_branch = 'master'
            connection = project.source.connection
            return dict(connection=connection.connection_name,
                        name=project.name,
                        canonical_name=project.canonical_name,
                        override_branch=override_branch,
                        override_checkout=override_checkout,
                        default_branch=project_default_branch)

        if job.required_projects:
            for job_project in job.required_projects.values():
                (trusted, project) = tenant.getProject(
                    job_project.project_name)
                if project is None:
                    raise Exception("Unknown project %s" %
                                    (job_project.project_name,))
                params['projects'].append(
                    make_project_dict(project,
                                      job_project.override_branch,
                                      job_project.override_checkout))
                projects.add(project)
                required_projects.add(project)
        for change in dependent_changes:
            # We have to find the project this way because it may not
            # be registered in the tenant (ie, a foreign project).
            source = self.sched.connections.getSourceByCanonicalHostname(
                change['project']['canonical_hostname'])
            project = source.getProject(change['project']['name'])
            if project not in projects:
                params['projects'].append(make_project_dict(project))
                projects.add(project)
        for p in projects:
            zuul_params['projects'][p.canonical_name] = (dict(
                name=p.name,
                short_name=p.name.split('/')[-1],
                # Duplicate this into the dict too, so that iterating
                # project.values() is easier for callers
                canonical_name=p.canonical_name,
                canonical_hostname=p.canonical_hostname,
                src_dir=os.path.join('src', p.canonical_name),
                required=(p in required_projects),
            ))

        build = Build(job, uuid)
        build.parameters = params
        build.nodeset = nodeset

        self.log.debug("Adding build %s of job %s to item %s" %
                       (build, job, item))
        item.addBuild(build)

        if job.name == 'noop':
            self.sched.onBuildStarted(build)
            self.sched.onBuildCompleted(build, 'SUCCESS', {}, [])
            return build

        functions = getGearmanFunctions(self.gearman)
        function_name = 'executor:execute'
        # Because all nodes belong to the same provider, region and
        # availability zone we can get executor_zone from only the first
        # node.
        executor_zone = None
        if nodes and nodes[0].get('attributes'):
            executor_zone = nodes[0]['attributes'].get('executor-zone')

        if executor_zone:
            _fname = '%s:%s' % (
                function_name,
                executor_zone)
            if _fname in functions:
                function_name = _fname
            else:
                self.log.warning(
                    "Job requested '%s' zuul-executor zone, but no "
                    "zuul-executors found for this zone; ignoring zone "
                    "request" % executor_zone)

        gearman_job = gear.TextJob(
            function_name, json_dumps(params), unique=uuid)

        build.__gearman_job = gearman_job
        build.__gearman_worker = None
        self.builds[uuid] = build

        if pipeline.precedence == zuul.model.PRECEDENCE_NORMAL:
            precedence = gear.PRECEDENCE_NORMAL
        elif pipeline.precedence == zuul.model.PRECEDENCE_HIGH:
            precedence = gear.PRECEDENCE_HIGH
        elif pipeline.precedence == zuul.model.PRECEDENCE_LOW:
            precedence = gear.PRECEDENCE_LOW

        try:
            self.gearman.submitJob(gearman_job, precedence=precedence,
                                   timeout=300)
        except Exception:
            self.log.exception("Unable to submit job to Gearman")
            self.onBuildCompleted(gearman_job, 'EXCEPTION')
            return build

        if not gearman_job.handle:
            self.log.error("No job handle was received for %s after"
                           " 300 seconds; marking as lost." %
                           gearman_job)
            self.onBuildCompleted(gearman_job, 'NO_HANDLE')

        self.log.debug("Received handle %s for %s" % (gearman_job.handle,
                                                      build))

        return build

    def cancel(self, build):
        # Returns whether a running build was canceled
        self.log.info("Cancel build %s for job %s" % (build, build.job))

        build.canceled = True
        try:
            job = build.__gearman_job  # noqa
        except AttributeError:
            self.log.debug("Build %s has no associated gearman job" % build)
            return False

        # TODOv3(jeblair): make a nicer way of recording build start.
        if build.url is not None:
            self.log.debug("Build %s has already started" % build)
            self.cancelRunningBuild(build)
            self.log.debug("Canceled running build %s" % build)
            return True
        else:
            self.log.debug("Build %s has not started yet" % build)

        self.log.debug("Looking for build %s in queue" % build)
        if self.cancelJobInQueue(build):
            self.log.debug("Removed build %s from queue" % build)
            return False

        time.sleep(1)

        self.log.debug("Still unable to find build %s to cancel" % build)
        if build.url:
            self.log.debug("Build %s has just started" % build)
            self.log.debug("Canceled running build %s" % build)
            self.cancelRunningBuild(build)
            return True
        self.log.debug("Unable to cancel build %s" % build)

    def onBuildCompleted(self, job, result=None):
        if job.unique in self.meta_jobs:
            del self.meta_jobs[job.unique]
            return

        build = self.builds.get(job.unique)
        if build:
            data = getJobData(job)
            build.node_labels = data.get('node_labels', [])
            build.node_name = data.get('node_name')
            if result is None:
                result = data.get('result')
                build.error_detail = data.get('error_detail')
            if result is None:
                if (build.build_set.getTries(build.job.name) >=
                    build.job.attempts):
                    result = 'RETRY_LIMIT'
                else:
                    build.retry = True
            if result in ('DISCONNECT', 'ABORTED'):
                # Always retry if the executor just went away
                build.retry = True
            if result == 'MERGER_FAILURE':
                # The build result MERGER_FAILURE is a bit misleading here
                # because when we got here we know that there are no merge
                # conflicts. Instead this is most likely caused by some
                # infrastructure failure. This can be anything like connection
                # issue, drive corruption, full disk, corrupted git cache, etc.
                # This may or may not be a recoverable failure so we should
                # retry here respecting the max retries. But to be able to
                # distinguish from RETRY_LIMIT which normally indicates pre
                # playbook failures we keep the build result after the max
                # attempts.
                if (build.build_set.getTries(build.job.name) <
                    build.job.attempts):
                    build.retry = True

            result_data = data.get('data', {})
            warnings = data.get('warnings', [])
            self.log.info("Build %s complete, result %s, warnings %s" %
                          (job, result, warnings))
            # If the build should be retried, don't supply the result
            # so that elsewhere we don't have to deal with keeping
            # track of which results are non-final.
            if build.retry:
                result = None
            self.sched.onBuildCompleted(build, result, result_data, warnings)
            # The test suite expects the build to be removed from the
            # internal dict after it's added to the report queue.
            del self.builds[job.unique]
        else:
            if not job.name.startswith("executor:stop:"):
                self.log.error("Unable to find build %s" % job.unique)

    def onWorkStatus(self, job):
        data = getJobData(job)
        self.log.debug("Build %s update %s" % (job, data))
        build = self.builds.get(job.unique)
        if build:
            started = (build.url is not None)
            # Allow URL to be updated
            build.url = data.get('url', build.url)
            # Update information about worker
            build.worker.updateFromData(data)

            if 'paused' in data and build.paused != data['paused']:
                build.paused = data['paused']
                if build.paused:
                    result_data = data.get('data', {})
                    self.sched.onBuildPaused(build, result_data)

            if not started:
                self.log.info("Build %s started" % job)
                build.__gearman_worker = data.get('worker_name')
                self.sched.onBuildStarted(build)
        else:
            self.log.error("Unable to find build %s" % job.unique)

    def onDisconnect(self, job):
        self.log.info("Gearman job %s lost due to disconnect" % job)
        self.onBuildCompleted(job, 'DISCONNECT')

    def onUnknownJob(self, job):
        self.log.info("Gearman job %s lost due to unknown handle" % job)
        self.onBuildCompleted(job, 'LOST')

    def cancelJobInQueue(self, build):
        job = build.__gearman_job

        req = gear.CancelJobAdminRequest(job.handle)
        job.connection.sendAdminRequest(req, timeout=300)
        self.log.debug("Response to cancel build %s request: %s" %
                       (build, req.response.strip()))
        if req.response.startswith(b"OK"):
            try:
                del self.builds[job.unique]
            except Exception:
                pass
            # Since this isn't otherwise going to get a build complete
            # event, send one to the scheduler so that it can unlock
            # the nodes.
            self.sched.onBuildCompleted(build, 'CANCELED', {}, [])
            return True
        return False

    def cancelRunningBuild(self, build):
        if not build.__gearman_worker:
            self.log.error("Build %s has no manager while canceling" %
                           (build,))
        stop_uuid = str(uuid4().hex)
        data = dict(uuid=build.__gearman_job.unique)
        stop_job = gear.TextJob("executor:stop:%s" % build.__gearman_worker,
                                json_dumps(data), unique=stop_uuid)
        self.meta_jobs[stop_uuid] = stop_job
        self.log.debug("Submitting stop job: %s", stop_job)
        self.gearman.submitJob(stop_job, precedence=gear.PRECEDENCE_HIGH,
                               timeout=300)
        return True

    def resumeBuild(self, build):
        if not build.__gearman_worker:
            self.log.error("Build %s has no manager while resuming" %
                           (build,))
        resume_uuid = str(uuid4().hex)
        data = dict(uuid=build.__gearman_job.unique)
        stop_job = gear.TextJob("executor:resume:%s" % build.__gearman_worker,
                                json_dumps(data), unique=resume_uuid)
        self.meta_jobs[resume_uuid] = stop_job
        self.log.debug("Submitting resume job: %s", stop_job)
        self.gearman.submitJob(stop_job, precedence=gear.PRECEDENCE_HIGH,
                               timeout=300)

    def lookForLostBuilds(self):
        self.log.debug("Looking for lost builds")
        # Construct a list from the values iterator to protect from it changing
        # out from underneath us.
        for build in list(self.builds.values()):
            if build.result:
                # The build has finished, it will be removed
                continue
            job = build.__gearman_job
            if not job.handle:
                # The build hasn't been enqueued yet
                continue
            p = gear.Packet(gear.constants.REQ, gear.constants.GET_STATUS,
                            job.handle)
            job.connection.sendPacket(p)
