#!/usr/bin/env python

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

from six.moves import configparser as ConfigParser
import gc
import hashlib
import json
import logging
import os
import pprint
from six.moves import queue as Queue
from six.moves import urllib
import random
import re
import select
import shutil
from six.moves import reload_module
import socket
import string
import subprocess
import swiftclient
import threading
import time
import uuid


import git
import gear
import fixtures
import pymysql
import statsd
import testtools
from git import GitCommandError

import zuul.connection.gerrit
import zuul.connection.smtp
import zuul.connection.sql
import zuul.scheduler
import zuul.webapp
import zuul.rpclistener
import zuul.launcher.gearman
import zuul.lib.swift
import zuul.merger.client
import zuul.merger.merger
import zuul.merger.server
import zuul.reporter.gerrit
import zuul.reporter.smtp
import zuul.source.gerrit
import zuul.trigger.gerrit
import zuul.trigger.timer
import zuul.trigger.zuultrigger

FIXTURE_DIR = os.path.join(os.path.dirname(__file__),
                           'fixtures')
USE_TEMPDIR = True

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(name)-32s '
                    '%(levelname)-8s %(message)s')


def repack_repo(path):
    cmd = ['git', '--git-dir=%s/.git' % path, 'repack', '-afd']
    output = subprocess.Popen(cmd, close_fds=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
    out = output.communicate()
    if output.returncode:
        raise Exception("git repack returned %d" % output.returncode)
    return out


def random_sha1():
    return hashlib.sha1(str(random.random())).hexdigest()


def iterate_timeout(max_seconds, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(0)
    raise Exception("Timeout waiting for %s" % purpose)


class ChangeReference(git.Reference):
    _common_path_default = "refs/changes"
    _points_to_commits_only = True


class FakeChange(object):
    categories = {'APRV': ('Approved', -1, 1),
                  'CRVW': ('Code-Review', -2, 2),
                  'VRFY': ('Verified', -2, 2)}

    def __init__(self, gerrit, number, project, branch, subject,
                 status='NEW', upstream_root=None):
        self.gerrit = gerrit
        self.reported = 0
        self.queried = 0
        self.patchsets = []
        self.number = number
        self.project = project
        self.branch = branch
        self.subject = subject
        self.latest_patchset = 0
        self.depends_on_change = None
        self.needed_by_changes = []
        self.fail_merge = False
        self.messages = []
        self.data = {
            'branch': branch,
            'comments': [],
            'commitMessage': subject,
            'createdOn': time.time(),
            'id': 'I' + random_sha1(),
            'lastUpdated': time.time(),
            'number': str(number),
            'open': status == 'NEW',
            'owner': {'email': 'user@example.com',
                      'name': 'User Name',
                      'username': 'username'},
            'patchSets': self.patchsets,
            'project': project,
            'status': status,
            'subject': subject,
            'submitRecords': [],
            'url': 'https://hostname/%s' % number}

        self.upstream_root = upstream_root
        self.addPatchset()
        self.data['submitRecords'] = self.getSubmitRecords()
        self.open = status == 'NEW'

    def add_fake_change_to_repo(self, msg, fn, large):
        path = os.path.join(self.upstream_root, self.project)
        repo = git.Repo(path)
        ref = ChangeReference.create(repo, '1/%s/%s' % (self.number,
                                                        self.latest_patchset),
                                     'refs/tags/init')
        repo.head.reference = ref
        zuul.merger.merger.reset_repo_to_head(repo)
        repo.git.clean('-x', '-f', '-d')

        path = os.path.join(self.upstream_root, self.project)
        if not large:
            fn = os.path.join(path, fn)
            f = open(fn, 'w')
            f.write("test %s %s %s\n" %
                    (self.branch, self.number, self.latest_patchset))
            f.close()
            repo.index.add([fn])
        else:
            for fni in range(100):
                fn = os.path.join(path, str(fni))
                f = open(fn, 'w')
                for ci in range(4096):
                    f.write(random.choice(string.printable))
                f.close()
                repo.index.add([fn])

        r = repo.index.commit(msg)
        repo.head.reference = 'master'
        zuul.merger.merger.reset_repo_to_head(repo)
        repo.git.clean('-x', '-f', '-d')
        repo.heads['master'].checkout()
        return r

    def addPatchset(self, files=[], large=False):
        self.latest_patchset += 1
        if files:
            fn = files[0]
        else:
            fn = '%s-%s' % (self.branch.replace('/', '_'), self.number)
        msg = self.subject + '-' + str(self.latest_patchset)
        c = self.add_fake_change_to_repo(msg, fn, large)
        ps_files = [{'file': '/COMMIT_MSG',
                     'type': 'ADDED'},
                    {'file': 'README',
                     'type': 'MODIFIED'}]
        for f in files:
            ps_files.append({'file': f, 'type': 'ADDED'})
        d = {'approvals': [],
             'createdOn': time.time(),
             'files': ps_files,
             'number': str(self.latest_patchset),
             'ref': 'refs/changes/1/%s/%s' % (self.number,
                                              self.latest_patchset),
             'revision': c.hexsha,
             'uploader': {'email': 'user@example.com',
                          'name': 'User name',
                          'username': 'user'}}
        self.data['currentPatchSet'] = d
        self.patchsets.append(d)
        self.data['submitRecords'] = self.getSubmitRecords()

    def getPatchsetCreatedEvent(self, patchset):
        event = {"type": "patchset-created",
                 "change": {"project": self.project,
                            "branch": self.branch,
                            "id": "I5459869c07352a31bfb1e7a8cac379cabfcb25af",
                            "number": str(self.number),
                            "subject": self.subject,
                            "owner": {"name": "User Name"},
                            "url": "https://hostname/3"},
                 "patchSet": self.patchsets[patchset - 1],
                 "uploader": {"name": "User Name"}}
        return event

    def getChangeRestoredEvent(self):
        event = {"type": "change-restored",
                 "change": {"project": self.project,
                            "branch": self.branch,
                            "id": "I5459869c07352a31bfb1e7a8cac379cabfcb25af",
                            "number": str(self.number),
                            "subject": self.subject,
                            "owner": {"name": "User Name"},
                            "url": "https://hostname/3"},
                 "restorer": {"name": "User Name"},
                 "patchSet": self.patchsets[-1],
                 "reason": ""}
        return event

    def getChangeAbandonedEvent(self):
        event = {"type": "change-abandoned",
                 "change": {"project": self.project,
                            "branch": self.branch,
                            "id": "I5459869c07352a31bfb1e7a8cac379cabfcb25af",
                            "number": str(self.number),
                            "subject": self.subject,
                            "owner": {"name": "User Name"},
                            "url": "https://hostname/3"},
                 "abandoner": {"name": "User Name"},
                 "patchSet": self.patchsets[-1],
                 "reason": ""}
        return event

    def getChangeCommentEvent(self, patchset):
        event = {"type": "comment-added",
                 "change": {"project": self.project,
                            "branch": self.branch,
                            "id": "I5459869c07352a31bfb1e7a8cac379cabfcb25af",
                            "number": str(self.number),
                            "subject": self.subject,
                            "owner": {"name": "User Name"},
                            "url": "https://hostname/3"},
                 "patchSet": self.patchsets[patchset - 1],
                 "author": {"name": "User Name"},
                 "approvals": [{"type": "Code-Review",
                                "description": "Code-Review",
                                "value": "0"}],
                 "comment": "This is a comment"}
        return event

    def getRefUpdatedEvent(self):
        path = os.path.join(self.upstream_root, self.project)
        repo = git.Repo(path)
        oldrev = repo.heads[self.branch].commit.hexsha

        event = {
            "type": "ref-updated",
            "submitter": {
                "name": "User Name",
            },
            "refUpdate": {
                "oldRev": oldrev,
                "newRev": self.patchsets[-1]['revision'],
                "refName": self.branch,
                "project": self.project,
            }
        }
        return event

    def addApproval(self, category, value, username='reviewer_john',
                    granted_on=None, message=''):
        if not granted_on:
            granted_on = time.time()
        approval = {
            'description': self.categories[category][0],
            'type': category,
            'value': str(value),
            'by': {
                'username': username,
                'email': username + '@example.com',
            },
            'grantedOn': int(granted_on)
        }
        for i, x in enumerate(self.patchsets[-1]['approvals'][:]):
            if x['by']['username'] == username and x['type'] == category:
                del self.patchsets[-1]['approvals'][i]
        self.patchsets[-1]['approvals'].append(approval)
        event = {'approvals': [approval],
                 'author': {'email': 'author@example.com',
                            'name': 'Patchset Author',
                            'username': 'author_phil'},
                 'change': {'branch': self.branch,
                            'id': 'Iaa69c46accf97d0598111724a38250ae76a22c87',
                            'number': str(self.number),
                            'owner': {'email': 'owner@example.com',
                                      'name': 'Change Owner',
                                      'username': 'owner_jane'},
                            'project': self.project,
                            'subject': self.subject,
                            'topic': 'master',
                            'url': 'https://hostname/459'},
                 'comment': message,
                 'patchSet': self.patchsets[-1],
                 'type': 'comment-added'}
        self.data['submitRecords'] = self.getSubmitRecords()
        return json.loads(json.dumps(event))

    def getSubmitRecords(self):
        status = {}
        for cat in self.categories.keys():
            status[cat] = 0

        for a in self.patchsets[-1]['approvals']:
            cur = status[a['type']]
            cat_min, cat_max = self.categories[a['type']][1:]
            new = int(a['value'])
            if new == cat_min:
                cur = new
            elif abs(new) > abs(cur):
                cur = new
            status[a['type']] = cur

        labels = []
        ok = True
        for typ, cat in self.categories.items():
            cur = status[typ]
            cat_min, cat_max = cat[1:]
            if cur == cat_min:
                value = 'REJECT'
                ok = False
            elif cur == cat_max:
                value = 'OK'
            else:
                value = 'NEED'
                ok = False
            labels.append({'label': cat[0], 'status': value})
        if ok:
            return [{'status': 'OK'}]
        return [{'status': 'NOT_READY',
                 'labels': labels}]

    def setDependsOn(self, other, patchset):
        self.depends_on_change = other
        d = {'id': other.data['id'],
             'number': other.data['number'],
             'ref': other.patchsets[patchset - 1]['ref']
             }
        self.data['dependsOn'] = [d]

        other.needed_by_changes.append(self)
        needed = other.data.get('neededBy', [])
        d = {'id': self.data['id'],
             'number': self.data['number'],
             'ref': self.patchsets[patchset - 1]['ref'],
             'revision': self.patchsets[patchset - 1]['revision']
             }
        needed.append(d)
        other.data['neededBy'] = needed

    def query(self):
        self.queried += 1
        d = self.data.get('dependsOn')
        if d:
            d = d[0]
            if (self.depends_on_change.patchsets[-1]['ref'] == d['ref']):
                d['isCurrentPatchSet'] = True
            else:
                d['isCurrentPatchSet'] = False
        return json.loads(json.dumps(self.data))

    def setMerged(self):
        if (self.depends_on_change and
                self.depends_on_change.data['status'] != 'MERGED'):
            return
        if self.fail_merge:
            return
        self.data['status'] = 'MERGED'
        self.open = False

        path = os.path.join(self.upstream_root, self.project)
        repo = git.Repo(path)
        repo.heads[self.branch].commit = \
            repo.commit(self.patchsets[-1]['revision'])

    def setReported(self):
        self.reported += 1


class FakeGerritConnection(zuul.connection.gerrit.GerritConnection):
    log = logging.getLogger("zuul.test.FakeGerritConnection")

    def __init__(self, connection_name, connection_config,
                 changes_db=None, queues_db=None, upstream_root=None):
        super(FakeGerritConnection, self).__init__(connection_name,
                                                   connection_config)

        self.event_queue = queues_db
        self.fixture_dir = os.path.join(FIXTURE_DIR, 'gerrit')
        self.change_number = 0
        self.changes = changes_db
        self.queries = []
        self.upstream_root = upstream_root

    def addFakeChange(self, project, branch, subject, status='NEW'):
        self.change_number += 1
        c = FakeChange(self, self.change_number, project, branch, subject,
                       upstream_root=self.upstream_root,
                       status=status)
        self.changes[self.change_number] = c
        return c

    def review(self, project, changeid, message, action):
        number, ps = changeid.split(',')
        change = self.changes[int(number)]

        # Add the approval back onto the change (ie simulate what gerrit would
        # do).
        # Usually when zuul leaves a review it'll create a feedback loop where
        # zuul's review enters another gerrit event (which is then picked up by
        # zuul). However, we can't mimic this behaviour (by adding this
        # approval event into the queue) as it stops jobs from checking what
        # happens before this event is triggered. If a job needs to see what
        # happens they can add their own verified event into the queue.
        # Nevertheless, we can update change with the new review in gerrit.

        for cat in ['CRVW', 'VRFY', 'APRV']:
            if cat in action:
                change.addApproval(cat, action[cat], username=self.user)

        if 'label' in action:
            parts = action['label'].split('=')
            change.addApproval(parts[0], parts[2], username=self.user)

        change.messages.append(message)

        if 'submit' in action:
            change.setMerged()
        if message:
            change.setReported()

    def query(self, number):
        change = self.changes.get(int(number))
        if change:
            return change.query()
        return {}

    def simpleQuery(self, query):
        self.log.debug("simpleQuery: %s" % query)
        self.queries.append(query)
        if query.startswith('change:'):
            # Query a specific changeid
            changeid = query[len('change:'):]
            l = [change.query() for change in self.changes.values()
                 if change.data['id'] == changeid]
        elif query.startswith('message:'):
            # Query the content of a commit message
            msg = query[len('message:'):].strip()
            l = [change.query() for change in self.changes.values()
                 if msg in change.data['commitMessage']]
        else:
            # Query all open changes
            l = [change.query() for change in self.changes.values()]
        return l

    def _start_watcher_thread(self, *args, **kw):
        pass

    def getGitUrl(self, project):
        return os.path.join(self.upstream_root, project.name)


class BuildHistory(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self):
        return ("<Completed build, result: %s name: %s #%s changes: %s>" %
                (self.result, self.name, self.number, self.changes))


class FakeURLOpener(object):
    def __init__(self, upstream_root, url):
        self.upstream_root = upstream_root
        self.url = url

    def read(self):
        res = urllib.parse.urlparse(self.url)
        path = res.path
        project = '/'.join(path.split('/')[2:-2])
        ret = '001e# service=git-upload-pack\n'
        ret += ('000000a31270149696713ba7e06f1beb760f20d359c4abed HEAD\x00'
                'multi_ack thin-pack side-band side-band-64k ofs-delta '
                'shallow no-progress include-tag multi_ack_detailed no-done\n')
        path = os.path.join(self.upstream_root, project)
        repo = git.Repo(path)
        for ref in repo.refs:
            r = ref.object.hexsha + ' ' + ref.path + '\n'
            ret += '%04x%s' % (len(r) + 4, r)
        ret += '0000'
        return ret


class FakeStatsd(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', 0))
        self.port = self.sock.getsockname()[1]
        self.wake_read, self.wake_write = os.pipe()
        self.stats = []

    def run(self):
        while True:
            poll = select.poll()
            poll.register(self.sock, select.POLLIN)
            poll.register(self.wake_read, select.POLLIN)
            ret = poll.poll()
            for (fd, event) in ret:
                if fd == self.sock.fileno():
                    data = self.sock.recvfrom(1024)
                    if not data:
                        return
                    self.stats.append(data[0])
                if fd == self.wake_read:
                    return

    def stop(self):
        os.write(self.wake_write, '1\n')


class FakeBuild(threading.Thread):
    log = logging.getLogger("zuul.test")

    def __init__(self, worker, job, number, node):
        threading.Thread.__init__(self)
        self.daemon = True
        self.worker = worker
        self.job = job
        self.name = job.name.split(':')[1]
        self.number = number
        self.node = node
        self.parameters = json.loads(job.arguments)
        self.unique = self.parameters['ZUUL_UUID']
        self.wait_condition = threading.Condition()
        self.waiting = False
        self.aborted = False
        self.requeue = False
        self.created = time.time()
        self.description = ''
        self.run_error = False

    def release(self):
        self.wait_condition.acquire()
        self.wait_condition.notify()
        self.waiting = False
        self.log.debug("Build %s released" % self.unique)
        self.wait_condition.release()

    def isWaiting(self):
        self.wait_condition.acquire()
        if self.waiting:
            ret = True
        else:
            ret = False
        self.wait_condition.release()
        return ret

    def _wait(self):
        self.wait_condition.acquire()
        self.waiting = True
        self.log.debug("Build %s waiting" % self.unique)
        self.wait_condition.wait()
        self.wait_condition.release()

    def run(self):
        data = {
            'url': 'https://server/job/%s/%s/' % (self.name, self.number),
            'name': self.name,
            'number': self.number,
            'manager': self.worker.worker_id,
            'worker_name': 'My Worker',
            'worker_hostname': 'localhost',
            'worker_ips': ['127.0.0.1', '192.168.1.1'],
            'worker_fqdn': 'zuul.example.org',
            'worker_program': 'FakeBuilder',
            'worker_version': 'v1.1',
            'worker_extra': {'something': 'else'}
        }

        self.log.debug('Running build %s' % self.unique)

        self.job.sendWorkData(json.dumps(data))
        self.log.debug('Sent WorkData packet with %s' % json.dumps(data))
        self.job.sendWorkStatus(0, 100)

        if self.worker.hold_jobs_in_build:
            self.log.debug('Holding build %s' % self.unique)
            self._wait()
        self.log.debug("Build %s continuing" % self.unique)

        self.worker.lock.acquire()

        result = 'SUCCESS'
        if (('ZUUL_REF' in self.parameters) and
            self.worker.shouldFailTest(self.name,
                                       self.parameters['ZUUL_REF'])):
            result = 'FAILURE'
        if self.aborted:
            result = 'ABORTED'
        if self.requeue:
            result = None

        if self.run_error:
            work_fail = True
            result = 'RUN_ERROR'
        else:
            data['result'] = result
            data['node_labels'] = ['bare-necessities']
            data['node_name'] = 'foo'
            work_fail = False

        changes = None
        if 'ZUUL_CHANGE_IDS' in self.parameters:
            changes = self.parameters['ZUUL_CHANGE_IDS']

        self.worker.build_history.append(
            BuildHistory(name=self.name, number=self.number,
                         result=result, changes=changes, node=self.node,
                         uuid=self.unique, description=self.description,
                         parameters=self.parameters,
                         pipeline=self.parameters['ZUUL_PIPELINE'])
        )

        self.job.sendWorkData(json.dumps(data))
        if work_fail:
            self.job.sendWorkFail()
        else:
            self.job.sendWorkComplete(json.dumps(data))
        del self.worker.gearman_jobs[self.job.unique]
        self.worker.running_builds.remove(self)
        self.worker.lock.release()


class FakeWorker(gear.Worker):
    def __init__(self, worker_id, test):
        super(FakeWorker, self).__init__(worker_id)
        self.gearman_jobs = {}
        self.build_history = []
        self.running_builds = []
        self.build_counter = 0
        self.fail_tests = {}
        self.test = test

        self.hold_jobs_in_build = False
        self.lock = threading.Lock()
        self.__work_thread = threading.Thread(target=self.work)
        self.__work_thread.daemon = True
        self.__work_thread.start()

    def handleJob(self, job):
        parts = job.name.split(":")
        cmd = parts[0]
        name = parts[1]
        if len(parts) > 2:
            node = parts[2]
        else:
            node = None
        if cmd == 'build':
            self.handleBuild(job, name, node)
        elif cmd == 'stop':
            self.handleStop(job, name)
        elif cmd == 'set_description':
            self.handleSetDescription(job, name)

    def handleBuild(self, job, name, node):
        build = FakeBuild(self, job, self.build_counter, node)
        job.build = build
        self.gearman_jobs[job.unique] = job
        self.build_counter += 1

        self.running_builds.append(build)
        build.start()

    def handleStop(self, job, name):
        self.log.debug("handle stop")
        parameters = json.loads(job.arguments)
        name = parameters['name']
        number = parameters['number']
        for build in self.running_builds:
            if build.name == name and build.number == number:
                build.aborted = True
                build.release()
                job.sendWorkComplete()
                return
        job.sendWorkFail()

    def handleSetDescription(self, job, name):
        self.log.debug("handle set description")
        parameters = json.loads(job.arguments)
        name = parameters['name']
        number = parameters['number']
        descr = parameters['html_description']
        for build in self.running_builds:
            if build.name == name and build.number == number:
                build.description = descr
                job.sendWorkComplete()
                return
        for build in self.build_history:
            if build.name == name and build.number == number:
                build.description = descr
                job.sendWorkComplete()
                return
        job.sendWorkFail()

    def work(self):
        while self.running:
            try:
                job = self.getJob()
            except gear.InterruptedError:
                continue
            try:
                self.handleJob(job)
            except:
                self.log.exception("Worker exception:")

    def addFailTest(self, name, change):
        l = self.fail_tests.get(name, [])
        l.append(change)
        self.fail_tests[name] = l

    def shouldFailTest(self, name, ref):
        l = self.fail_tests.get(name, [])
        for change in l:
            if self.test.ref_has_change(ref, change):
                return True
        return False

    def release(self, regex=None):
        builds = self.running_builds[:]
        self.log.debug("releasing build %s (%s)" % (regex,
                                                    len(self.running_builds)))
        for build in builds:
            if not regex or re.match(regex, build.name):
                self.log.debug("releasing build %s" %
                               (build.parameters['ZUUL_UUID']))
                build.release()
            else:
                self.log.debug("not releasing build %s" %
                               (build.parameters['ZUUL_UUID']))
        self.log.debug("done releasing builds %s (%s)" %
                       (regex, len(self.running_builds)))


class FakeGearmanServer(gear.Server):
    def __init__(self):
        self.hold_jobs_in_queue = False
        super(FakeGearmanServer, self).__init__(0)

    def getJobForConnection(self, connection, peek=False):
        for queue in [self.high_queue, self.normal_queue, self.low_queue]:
            for job in queue:
                if not hasattr(job, 'waiting'):
                    if job.name.startswith('build:'):
                        job.waiting = self.hold_jobs_in_queue
                    else:
                        job.waiting = False
                if job.waiting:
                    continue
                if job.name in connection.functions:
                    if not peek:
                        queue.remove(job)
                        connection.related_jobs[job.handle] = job
                        job.worker_connection = connection
                    job.running = True
                    return job
        return None

    def release(self, regex=None):
        released = False
        qlen = (len(self.high_queue) + len(self.normal_queue) +
                len(self.low_queue))
        self.log.debug("releasing queued job %s (%s)" % (regex, qlen))
        for job in self.getQueue():
            cmd, name = job.name.split(':')
            if cmd != 'build':
                continue
            if not regex or re.match(regex, name):
                self.log.debug("releasing queued job %s" %
                               job.unique)
                job.waiting = False
                released = True
            else:
                self.log.debug("not releasing queued job %s" %
                               job.unique)
        if released:
            self.wakeConnections()
        qlen = (len(self.high_queue) + len(self.normal_queue) +
                len(self.low_queue))
        self.log.debug("done releasing queued jobs %s (%s)" % (regex, qlen))


class FakeSMTP(object):
    log = logging.getLogger('zuul.FakeSMTP')

    def __init__(self, messages, server, port):
        self.server = server
        self.port = port
        self.messages = messages

    def sendmail(self, from_email, to_email, msg):
        self.log.info("Sending email from %s, to %s, with msg %s" % (
                      from_email, to_email, msg))

        headers = msg.split('\n\n', 1)[0]
        body = msg.split('\n\n', 1)[1]

        self.messages.append(dict(
            from_email=from_email,
            to_email=to_email,
            msg=msg,
            headers=headers,
            body=body,
        ))

        return True

    def quit(self):
        return True


class FakeSwiftClientConnection(swiftclient.client.Connection):
    def post_account(self, headers):
        # Do nothing
        pass

    def get_auth(self):
        # Returns endpoint and (unused) auth token
        endpoint = os.path.join('https://storage.example.org', 'V1',
                                'AUTH_account')
        return endpoint, ''


class MySQLSchemaFixture(fixtures.Fixture):
    def setUp(self):
        super(MySQLSchemaFixture, self).setUp()

        random_bits = ''.join(random.choice(string.ascii_lowercase +
                                            string.ascii_uppercase)
                              for x in range(8))
        self.name = '%s_%s' % (random_bits, os.getpid())
        self.passwd = uuid.uuid4().hex
        db = pymysql.connect(host="localhost",
                             user="openstack_citest",
                             passwd="openstack_citest",
                             db="openstack_citest")
        cur = db.cursor()
        cur.execute("create database %s" % self.name)
        cur.execute(
            "grant all on %s.* to '%s'@'localhost' identified by '%s'" %
            (self.name, self.name, self.passwd))
        cur.execute("flush privileges")

        self.dburi = 'mysql+pymysql://%s:%s@localhost/%s' % (self.name,
                                                             self.passwd,
                                                             self.name)
        self.addDetail('dburi', testtools.content.text_content(self.dburi))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        db = pymysql.connect(host="localhost",
                             user="openstack_citest",
                             passwd="openstack_citest",
                             db="openstack_citest")
        cur = db.cursor()
        cur.execute("drop database %s" % self.name)
        cur.execute("drop user '%s'@'localhost'" % self.name)
        cur.execute("flush privileges")


class BaseTestCase(testtools.TestCase):
    log = logging.getLogger("zuul.test")

    def setUp(self):
        super(BaseTestCase, self).setUp()
        test_timeout = os.environ.get('OS_TEST_TIMEOUT', 0)
        try:
            test_timeout = int(test_timeout)
        except ValueError:
            # If timeout value is invalid do not set a timeout.
            test_timeout = 0
        if test_timeout > 0:
            self.useFixture(fixtures.Timeout(test_timeout, gentle=False))

        if (os.environ.get('OS_STDOUT_CAPTURE') == 'True' or
            os.environ.get('OS_STDOUT_CAPTURE') == '1'):
            stdout = self.useFixture(fixtures.StringStream('stdout')).stream
            self.useFixture(fixtures.MonkeyPatch('sys.stdout', stdout))
        if (os.environ.get('OS_STDERR_CAPTURE') == 'True' or
            os.environ.get('OS_STDERR_CAPTURE') == '1'):
            stderr = self.useFixture(fixtures.StringStream('stderr')).stream
            self.useFixture(fixtures.MonkeyPatch('sys.stderr', stderr))
        if (os.environ.get('OS_LOG_CAPTURE') == 'True' or
            os.environ.get('OS_LOG_CAPTURE') == '1'):
            log_level = logging.DEBUG
            if os.environ.get('OS_LOG_LEVEL') == 'DEBUG':
                log_level = logging.DEBUG
            elif os.environ.get('OS_LOG_LEVEL') == 'INFO':
                log_level = logging.INFO
            elif os.environ.get('OS_LOG_LEVEL') == 'WARNING':
                log_level = logging.WARNING
            elif os.environ.get('OS_LOG_LEVEL') == 'ERROR':
                log_level = logging.ERROR
            elif os.environ.get('OS_LOG_LEVEL') == 'CRITICAL':
                log_level = logging.CRITICAL
            self.useFixture(fixtures.FakeLogger(
                level=log_level,
                format='%(asctime)s %(name)-32s '
                '%(levelname)-8s %(message)s'))

            # NOTE(notmorgan): Extract logging overrides for specific libraries
            # from the OS_LOG_DEFAULTS env and create FakeLogger fixtures for
            # each. This is used to limit the output during test runs from
            # libraries that zuul depends on such as gear.
            log_defaults_from_env = os.environ.get('OS_LOG_DEFAULTS')

            if log_defaults_from_env:
                for default in log_defaults_from_env.split(','):
                    try:
                        name, level_str = default.split('=', 1)
                        level = getattr(logging, level_str, logging.DEBUG)
                        self.useFixture(fixtures.FakeLogger(
                            name=name,
                            level=level,
                            format='%(asctime)s %(name)-32s '
                                   '%(levelname)-8s %(message)s'))
                    except ValueError:
                        # NOTE(notmorgan): Invalid format of the log default,
                        # skip and don't try and apply a logger for the
                        # specified module
                        pass


class ZuulTestCase(BaseTestCase):

    def setUp(self):
        super(ZuulTestCase, self).setUp()
        if USE_TEMPDIR:
            tmp_root = self.useFixture(fixtures.TempDir(
                rootdir=os.environ.get("ZUUL_TEST_ROOT"))
            ).path
        else:
            tmp_root = os.environ.get("ZUUL_TEST_ROOT")
        self.test_root = os.path.join(tmp_root, "zuul-test")
        self.upstream_root = os.path.join(self.test_root, "upstream")
        self.git_root = os.path.join(self.test_root, "git")
        self.state_root = os.path.join(self.test_root, "lib")

        if os.path.exists(self.test_root):
            shutil.rmtree(self.test_root)
        os.makedirs(self.test_root)
        os.makedirs(self.upstream_root)
        os.makedirs(self.state_root)

        # Make per test copy of Configuration.
        self.setup_config()
        self.config.set('zuul', 'layout_config',
                        os.path.join(FIXTURE_DIR,
                                     self.config.get('zuul', 'layout_config')))
        self.config.set('merger', 'git_dir', self.git_root)
        self.config.set('zuul', 'state_dir', self.state_root)

        # For each project in config:
        self.init_repo("org/project")
        self.init_repo("org/project1")
        self.init_repo("org/project2")
        self.init_repo("org/project3")
        self.init_repo("org/project4")
        self.init_repo("org/project5")
        self.init_repo("org/project6")
        self.init_repo("org/one-job-project")
        self.init_repo("org/nonvoting-project")
        self.init_repo("org/templated-project")
        self.init_repo("org/layered-project")
        self.init_repo("org/node-project")
        self.init_repo("org/conflict-project")
        self.init_repo("org/noop-project")
        self.init_repo("org/experimental-project")
        self.init_repo("org/no-jobs-project")

        self.statsd = FakeStatsd()
        # note, use 127.0.0.1 rather than localhost to avoid getting ipv6
        # see: https://github.com/jsocol/pystatsd/issues/61
        os.environ['STATSD_HOST'] = '127.0.0.1'
        os.environ['STATSD_PORT'] = str(self.statsd.port)
        self.statsd.start()
        # the statsd client object is configured in the statsd module import
        reload_module(statsd)
        reload_module(zuul.scheduler)

        self.gearman_server = FakeGearmanServer()

        self.config.set('gearman', 'port', str(self.gearman_server.port))

        self.worker = FakeWorker('fake_worker', self)
        self.worker.addServer('127.0.0.1', self.gearman_server.port)
        self.gearman_server.worker = self.worker

        zuul.source.gerrit.GerritSource.replication_timeout = 1.5
        zuul.source.gerrit.GerritSource.replication_retry_interval = 0.5
        zuul.connection.gerrit.GerritEventConnector.delay = 0.0

        self.sched = zuul.scheduler.Scheduler(self.config)

        self.useFixture(fixtures.MonkeyPatch('swiftclient.client.Connection',
                                             FakeSwiftClientConnection))
        self.swift = zuul.lib.swift.Swift(self.config)

        self.event_queues = [
            self.sched.result_event_queue,
            self.sched.trigger_event_queue
        ]

        self.configure_connections()
        self.sched.registerConnections(self.connections)

        def URLOpenerFactory(*args, **kw):
            if isinstance(args[0], urllib.request.Request):
                return old_urlopen(*args, **kw)
            return FakeURLOpener(self.upstream_root, *args, **kw)

        old_urlopen = urllib.request.urlopen
        urllib.request.urlopen = URLOpenerFactory

        self.merge_server = zuul.merger.server.MergeServer(self.config,
                                                           self.connections)
        self.merge_server.start()

        self.launcher = zuul.launcher.gearman.Gearman(self.config, self.sched,
                                                      self.swift)
        self.merge_client = zuul.merger.client.MergeClient(
            self.config, self.sched)

        self.sched.setLauncher(self.launcher)
        self.sched.setMerger(self.merge_client)

        self.webapp = zuul.webapp.WebApp(
            self.sched, port=0, listen_address='127.0.0.1')
        self.rpc = zuul.rpclistener.RPCListener(self.config, self.sched)

        self.sched.start()
        self.sched.reconfigure(self.config)
        self.sched.resume()
        self.webapp.start()
        self.rpc.start()
        self.launcher.gearman.waitForServer()
        self.registerJobs()
        self.builds = self.worker.running_builds
        self.history = self.worker.build_history

        self.addCleanup(self.assertFinalState)
        self.addCleanup(self.shutdown)

    def configure_connections(self):
        # TODO(jhesketh): This should come from lib.connections for better
        # coverage
        # Register connections from the config
        self.smtp_messages = []

        def FakeSMTPFactory(*args, **kw):
            args = [self.smtp_messages] + list(args)
            return FakeSMTP(*args, **kw)

        self.useFixture(fixtures.MonkeyPatch('smtplib.SMTP', FakeSMTPFactory))

        # Set a changes database so multiple FakeGerrit's can report back to
        # a virtual canonical database given by the configured hostname
        self.gerrit_changes_dbs = {}
        self.gerrit_queues_dbs = {}
        self.connections = {}

        for section_name in self.config.sections():
            con_match = re.match(r'^connection ([\'\"]?)(.*)(\1)$',
                                 section_name, re.I)
            if not con_match:
                continue
            con_name = con_match.group(2)
            con_config = dict(self.config.items(section_name))

            if 'driver' not in con_config:
                raise Exception("No driver specified for connection %s."
                                % con_name)

            con_driver = con_config['driver']

            # TODO(jhesketh): load the required class automatically
            if con_driver == 'gerrit':
                if con_config['server'] not in self.gerrit_changes_dbs.keys():
                    self.gerrit_changes_dbs[con_config['server']] = {}
                if con_config['server'] not in self.gerrit_queues_dbs.keys():
                    self.gerrit_queues_dbs[con_config['server']] = \
                        Queue.Queue()
                    self.event_queues.append(
                        self.gerrit_queues_dbs[con_config['server']])
                self.connections[con_name] = FakeGerritConnection(
                    con_name, con_config,
                    changes_db=self.gerrit_changes_dbs[con_config['server']],
                    queues_db=self.gerrit_queues_dbs[con_config['server']],
                    upstream_root=self.upstream_root
                )
                setattr(self, 'fake_' + con_name, self.connections[con_name])
            elif con_driver == 'smtp':
                self.connections[con_name] = \
                    zuul.connection.smtp.SMTPConnection(con_name, con_config)
            elif con_driver == 'sql':
                self.connections[con_name] = \
                    zuul.connection.sql.SQLConnection(con_name, con_config)
            else:
                raise Exception("Unknown driver, %s, for connection %s"
                                % (con_config['driver'], con_name))

        # If the [gerrit] or [smtp] sections still exist, load them in as a
        # connection named 'gerrit' or 'smtp' respectfully

        if 'gerrit' in self.config.sections():
            self.gerrit_changes_dbs['gerrit'] = {}
            self.gerrit_queues_dbs['gerrit'] = Queue.Queue()
            self.event_queues.append(self.gerrit_queues_dbs['gerrit'])
            self.connections['gerrit'] = FakeGerritConnection(
                '_legacy_gerrit', dict(self.config.items('gerrit')),
                changes_db=self.gerrit_changes_dbs['gerrit'],
                queues_db=self.gerrit_queues_dbs['gerrit'])

        if 'smtp' in self.config.sections():
            self.connections['smtp'] = \
                zuul.connection.smtp.SMTPConnection(
                    '_legacy_smtp', dict(self.config.items('smtp')))

    def setup_config(self, config_file='zuul.conf'):
        """Per test config object. Override to set different config."""
        self.config = ConfigParser.ConfigParser()
        self.config.read(os.path.join(FIXTURE_DIR, config_file))

    def assertFinalState(self):
        # Make sure that git.Repo objects have been garbage collected.
        repos = []
        gc.collect()
        for obj in gc.get_objects():
            if isinstance(obj, git.Repo):
                repos.append(obj)
        self.assertEqual(len(repos), 0)
        self.assertEmptyQueues()
        for pipeline in self.sched.layout.pipelines.values():
            if isinstance(pipeline.manager,
                          zuul.scheduler.IndependentPipelineManager):
                self.assertEqual(len(pipeline.queues), 0)

    def shutdown(self):
        self.log.debug("Shutting down after tests")
        self.launcher.stop()
        self.merge_server.stop()
        self.merge_server.join()
        self.merge_client.stop()
        self.worker.shutdown()
        self.sched.stop()
        self.sched.join()
        self.statsd.stop()
        self.statsd.join()
        self.webapp.stop()
        self.webapp.join()
        self.rpc.stop()
        self.rpc.join()
        self.gearman_server.shutdown()
        threads = threading.enumerate()
        if len(threads) > 1:
            self.log.error("More than one thread is running: %s" % threads)

    def init_repo(self, project):
        parts = project.split('/')
        path = os.path.join(self.upstream_root, *parts[:-1])
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(self.upstream_root, project)
        repo = git.Repo.init(path)

        repo.config_writer().set_value('user', 'email', 'user@example.com')
        repo.config_writer().set_value('user', 'name', 'User Name')
        repo.config_writer().write()

        fn = os.path.join(path, 'README')
        f = open(fn, 'w')
        f.write("test\n")
        f.close()
        repo.index.add([fn])
        repo.index.commit('initial commit')
        master = repo.create_head('master')
        repo.create_tag('init')

        repo.head.reference = master
        zuul.merger.merger.reset_repo_to_head(repo)
        repo.git.clean('-x', '-f', '-d')

        self.create_branch(project, 'mp')

    def create_branch(self, project, branch):
        path = os.path.join(self.upstream_root, project)
        repo = git.Repo.init(path)
        fn = os.path.join(path, 'README')

        branch_head = repo.create_head(branch)
        repo.head.reference = branch_head
        f = open(fn, 'a')
        f.write("test %s\n" % branch)
        f.close()
        repo.index.add([fn])
        repo.index.commit('%s commit' % branch)

        repo.head.reference = repo.heads['master']
        zuul.merger.merger.reset_repo_to_head(repo)
        repo.git.clean('-x', '-f', '-d')

    def create_commit(self, project):
        path = os.path.join(self.upstream_root, project)
        repo = git.Repo(path)
        repo.head.reference = repo.heads['master']
        file_name = os.path.join(path, 'README')
        with open(file_name, 'a') as f:
            f.write('creating fake commit\n')
        repo.index.add([file_name])
        commit = repo.index.commit('Creating a fake commit')
        return commit.hexsha

    def ref_has_change(self, ref, change):
        path = os.path.join(self.git_root, change.project)
        repo = git.Repo(path)
        try:
            for commit in repo.iter_commits(ref):
                if commit.message.strip() == ('%s-1' % change.subject):
                    return True
        except GitCommandError:
            pass
        return False

    def job_has_changes(self, *args):
        job = args[0]
        commits = args[1:]
        if isinstance(job, FakeBuild):
            parameters = job.parameters
        else:
            parameters = json.loads(job.arguments)
        project = parameters['ZUUL_PROJECT']
        path = os.path.join(self.git_root, project)
        repo = git.Repo(path)
        ref = parameters['ZUUL_REF']
        sha = parameters['ZUUL_COMMIT']
        repo_messages = [c.message.strip() for c in repo.iter_commits(ref)]
        repo_shas = [c.hexsha for c in repo.iter_commits(ref)]
        commit_messages = ['%s-1' % commit.subject for commit in commits]
        self.log.debug("Checking if job %s has changes; commit_messages %s;"
                       " repo_messages %s; sha %s" % (job, commit_messages,
                                                      repo_messages, sha))
        for msg in commit_messages:
            if msg not in repo_messages:
                self.log.debug("  messages do not match")
                return False
        if repo_shas[0] != sha:
            self.log.debug("  sha does not match")
            return False
        self.log.debug("  OK")
        return True

    def registerJobs(self):
        count = 0
        for job in self.sched.layout.jobs.keys():
            self.worker.registerFunction('build:' + job)
            count += 1
        self.worker.registerFunction('stop:' + self.worker.worker_id)
        count += 1

        while len(self.gearman_server.functions) < count:
            time.sleep(0)

    def orderedRelease(self):
        # Run one build at a time to ensure non-race order:
        while len(self.builds):
            self.release(self.builds[0])
            self.waitUntilSettled()

    def release(self, job):
        if isinstance(job, FakeBuild):
            job.release()
        else:
            job.waiting = False
            self.log.debug("Queued job %s released" % job.unique)
            self.gearman_server.wakeConnections()

    def getParameter(self, job, name):
        if isinstance(job, FakeBuild):
            return job.parameters[name]
        else:
            parameters = json.loads(job.arguments)
            return parameters[name]

    def resetGearmanServer(self):
        self.worker.setFunctions([])
        while True:
            done = True
            for connection in self.gearman_server.active_connections:
                if (connection.functions and
                    connection.client_id not in ['Zuul RPC Listener',
                                                 'Zuul Merger']):
                    done = False
            if done:
                break
            time.sleep(0)
        self.gearman_server.functions = set()
        self.rpc.register()
        self.merge_server.register()

    def haveAllBuildsReported(self):
        # See if Zuul is waiting on a meta job to complete
        if self.launcher.meta_jobs:
            return False
        # Find out if every build that the worker has completed has been
        # reported back to Zuul.  If it hasn't then that means a Gearman
        # event is still in transit and the system is not stable.
        for build in self.worker.build_history:
            zbuild = self.launcher.builds.get(build.uuid)
            if not zbuild:
                # It has already been reported
                continue
            # It hasn't been reported yet.
            return False
        # Make sure that none of the worker connections are in GRAB_WAIT
        for connection in self.worker.active_connections:
            if connection.state == 'GRAB_WAIT':
                return False
        return True

    def areAllBuildsWaiting(self):
        builds = self.launcher.builds.values()
        for build in builds:
            client_job = None
            for conn in self.launcher.gearman.active_connections:
                for j in conn.related_jobs.values():
                    if j.unique == build.uuid:
                        client_job = j
                        break
            if not client_job:
                self.log.debug("%s is not known to the gearman client" %
                               build)
                return False
            if not client_job.handle:
                self.log.debug("%s has no handle" % client_job)
                return False
            server_job = self.gearman_server.jobs.get(client_job.handle)
            if not server_job:
                self.log.debug("%s is not known to the gearman server" %
                               client_job)
                return False
            if not hasattr(server_job, 'waiting'):
                self.log.debug("%s is being enqueued" % server_job)
                return False
            if server_job.waiting:
                continue
            worker_job = self.worker.gearman_jobs.get(server_job.unique)
            if worker_job:
                if build.number is None:
                    self.log.debug("%s has not reported start" % worker_job)
                    return False
                if worker_job.build.isWaiting():
                    continue
                else:
                    self.log.debug("%s is running" % worker_job)
                    return False
            else:
                self.log.debug("%s is unassigned" % server_job)
                return False
        return True

    def eventQueuesEmpty(self):
        for queue in self.event_queues:
            yield queue.empty()

    def eventQueuesJoin(self):
        for queue in self.event_queues:
            queue.join()

    def waitUntilSettled(self):
        self.log.debug("Waiting until settled...")
        start = time.time()
        while True:
            if time.time() - start > 10:
                self.log.debug("Queue status:")
                for queue in self.event_queues:
                    self.log.debug("  %s: %s" % (queue, queue.empty()))
                self.log.debug("All builds waiting: %s" %
                               (self.areAllBuildsWaiting(),))
                raise Exception("Timeout waiting for Zuul to settle")
            # Make sure no new events show up while we're checking
            self.worker.lock.acquire()
            # have all build states propogated to zuul?
            if self.haveAllBuildsReported():
                # Join ensures that the queue is empty _and_ events have been
                # processed
                self.eventQueuesJoin()
                self.sched.run_handler_lock.acquire()
                if (not self.merge_client.build_sets and
                    all(self.eventQueuesEmpty()) and
                    self.haveAllBuildsReported() and
                    self.areAllBuildsWaiting()):
                    self.sched.run_handler_lock.release()
                    self.worker.lock.release()
                    self.log.debug("...settled.")
                    return
                self.sched.run_handler_lock.release()
            self.worker.lock.release()
            self.sched.wake_event.wait(0.1)

    def countJobResults(self, jobs, result):
        jobs = filter(lambda x: x.result == result, jobs)
        return len(jobs)

    def getJobFromHistory(self, name):
        history = self.worker.build_history
        for job in history:
            if job.name == name:
                return job
        raise Exception("Unable to find job %s in history" % name)

    def assertEmptyQueues(self):
        # Make sure there are no orphaned jobs
        for pipeline in self.sched.layout.pipelines.values():
            for queue in pipeline.queues:
                if len(queue.queue) != 0:
                    print('pipeline %s queue %s contents %s' % (
                        pipeline.name, queue.name, queue.queue))
                self.assertEqual(len(queue.queue), 0,
                                 "Pipelines queues should be empty")

    def assertReportedStat(self, key, value=None, kind=None):
        start = time.time()
        while time.time() < (start + 5):
            for stat in self.statsd.stats:
                pprint.pprint(self.statsd.stats)
                k, v = stat.split(':')
                if key == k:
                    if value is None and kind is None:
                        return
                    elif value:
                        if value == v:
                            return
                    elif kind:
                        if v.endswith('|' + kind):
                            return
            time.sleep(0.1)

        pprint.pprint(self.statsd.stats)
        raise Exception("Key %s not found in reported stats" % key)


class ZuulDBTestCase(ZuulTestCase):
    def setup_config(self, config_file='zuul-connections-same-gerrit.conf'):
        super(ZuulDBTestCase, self).setup_config(config_file)
        for section_name in self.config.sections():
            con_match = re.match(r'^connection ([\'\"]?)(.*)(\1)$',
                                 section_name, re.I)
            if not con_match:
                continue

            if self.config.get(section_name, 'driver') == 'sql':
                f = MySQLSchemaFixture()
                self.useFixture(f)
                if (self.config.get(section_name, 'dburi') ==
                    '$MYSQL_FIXTURE_DBURI$'):
                    self.config.set(section_name, 'dburi', f.dburi)
