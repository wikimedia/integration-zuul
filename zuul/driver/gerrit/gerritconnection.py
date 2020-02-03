# Copyright 2011 OpenStack, LLC.
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

import copy
import datetime
import itertools
import json
import math
import logging
import paramiko
import pprint
import queue
import re
import re2
import requests
import select
import shlex
import threading
import time
import urllib
import urllib.parse

from typing import Dict, List
from uuid import uuid4

from zuul import version as zuul_version
from zuul.connection import BaseConnection
from zuul.driver.gerrit.auth import FormAuth
from zuul.driver.gerrit.gcloudauth import GCloudAuth
from zuul.driver.gerrit.gerritmodel import GerritChange, GerritTriggerEvent
from zuul.lib.logutil import get_annotated_logger
from zuul.model import Ref, Tag, Branch, Project

# HTTP timeout in seconds
TIMEOUT = 30


class GerritChangeData(object):
    """Compatability layer for SSH/HTTP

    This class holds the raw data returned from a change query over
    SSH or HTTP.  Most of the work of parsing the data and storing it
    on the change is in the gerritmodel.GerritChange class, however
    this does perform a small amount of parsing of dependencies since
    they are handled outside of that class.  This provides an API to
    that data independent of the source.

    """

    SSH = 1
    HTTP = 2

    def __init__(self, fmt, data, related=None):
        self.format = fmt
        self.data = data

        if fmt == self.SSH:
            self.parseSSH(data)
        else:
            self.parseHTTP(data)
            if related:
                self.parseRelatedHTTP(data, related)

    def parseSSH(self, data):
        self.needed_by = []
        self.depends_on = None
        self.message = data['commitMessage']
        self.current_patchset = str(data['currentPatchSet']['number'])
        self.number = str(data['number'])

        if 'dependsOn' in data:
            parts = data['dependsOn'][0]['ref'].split('/')
            self.depends_on = (parts[3], parts[4])

        for needed in data.get('neededBy', []):
            parts = needed['ref'].split('/')
            self.needed_by.append((parts[3], parts[4]))

    def parseHTTP(self, data):
        rev = data['revisions'][data['current_revision']]
        self.message = rev['commit']['message']
        self.current_patchset = str(rev['_number'])
        self.number = str(data['_number'])

    def parseRelatedHTTP(self, data, related):
        self.needed_by = []
        self.depends_on = None
        current_rev = data['revisions'][data['current_revision']]
        for change in related['changes']:
            for parent in current_rev['commit']['parents']:
                if change['commit']['commit'] == parent['commit']:
                    self.depends_on = (change['_change_number'],
                                       change['_revision_number'])
                    break
            else:
                self.needed_by.append((change['_change_number'],
                                       change['_revision_number']))


class GerritEventConnector(threading.Thread):
    """Move events from Gerrit to the scheduler."""

    IGNORED_EVENTS = (
        'cache-eviction',  # evict-cache plugin
    )

    log = logging.getLogger("zuul.GerritEventConnector")
    delay = 10.0

    def __init__(self, connection):
        super(GerritEventConnector, self).__init__()
        self.daemon = True
        self.connection = connection
        self._stopped = False

    def stop(self):
        self._stopped = True
        self.connection.addEvent(None)

    def _handleEvent(self):
        ts, data = self.connection.getEvent()
        if self._stopped:
            return
        # Gerrit can produce inconsistent data immediately after an
        # event, So ensure that we do not deliver the event to Zuul
        # until at least a certain amount of time has passed.  Note
        # that if we receive several events in succession, we will
        # only need to delay for the first event.  In essence, Zuul
        # should always be a constant number of seconds behind Gerrit.
        now = time.time()
        time.sleep(max((ts + self.delay) - now, 0.0))
        event = GerritTriggerEvent()
        event.timestamp = ts

        # Gerrit events don't have an event id that could be used to globally
        # identify this event in the system so we have to generate one.
        event.zuul_event_id = str(uuid4().hex)
        log = get_annotated_logger(self.log, event)

        event.type = data.get('type')
        event.uuid = data.get('uuid')

        # NOTE(mnaser): Certain plugins fire events which end up causing
        #               an unrecognized event log *and* a traceback if they
        #               do not contain full project information, we skip them
        #               here to keep logs clean.
        if event.type in GerritEventConnector.IGNORED_EVENTS:
            return

        # This catches when a change is merged, as it could potentially
        # have merged layout info which will need to be read in.
        # Ideally this would be done with a refupdate event so as to catch
        # directly pushed things as well as full changes being merged.
        # But we do not yet get files changed data for pure refupdate events.
        # TODO(jlk): handle refupdated events instead of just changes
        if event.type == 'change-merged':
            event.branch_updated = True
        event.trigger_name = 'gerrit'
        change = data.get('change')
        event.project_hostname = self.connection.canonical_hostname
        if change:
            event.project_name = change.get('project')
            event.branch = change.get('branch')
            event.change_number = str(change.get('number'))
            event.change_url = change.get('url')
            patchset = data.get('patchSet')
            if patchset:
                event.patch_number = str(patchset.get('number'))
                event.ref = patchset.get('ref')
            event.approvals = data.get('approvals', [])
            event.comment = data.get('comment')
        refupdate = data.get('refUpdate')
        if refupdate:
            event.project_name = refupdate.get('project')
            event.ref = refupdate.get('refName')
            event.oldrev = refupdate.get('oldRev')
            event.newrev = refupdate.get('newRev')
        if event.project_name is None:
            # ref-replica* events
            event.project_name = data.get('project')
        if event.type == 'project-created':
            event.project_name = data.get('projectName')
        # Map the event types to a field name holding a Gerrit
        # account attribute. See Gerrit stream-event documentation
        # in cmd-stream-events.html
        accountfield_from_type = {
            'patchset-created': 'uploader',
            'draft-published': 'uploader',  # Gerrit 2.5/2.6
            'change-abandoned': 'abandoner',
            'change-restored': 'restorer',
            'change-merged': 'submitter',
            'merge-failed': 'submitter',  # Gerrit 2.5/2.6
            'comment-added': 'author',
            'ref-updated': 'submitter',
            'reviewer-added': 'reviewer',  # Gerrit 2.5/2.6
            'ref-replicated': None,
            'ref-replication-done': None,
            'ref-replication-scheduled': None,
            'topic-changed': 'changer',
            'project-created': None,  # Gerrit 2.14
            'pending-check': None,  # Gerrit 3.0+
        }
        event.account = None
        if event.type in accountfield_from_type:
            field = accountfield_from_type[event.type]
            if field:
                event.account = data.get(accountfield_from_type[event.type])
        else:
            log.warning("Received unrecognized event type '%s' "
                        "from Gerrit. Can not get account information." %
                        (event.type,))

        # This checks whether the event created or deleted a branch so
        # that Zuul may know to perform a reconfiguration on the
        # project.
        if (event.type == 'ref-updated' and
            ((not event.ref.startswith('refs/')) or
             event.ref.startswith('refs/heads'))):
            if event.oldrev == '0' * 40:
                event.branch_created = True
                event.branch = event.ref
                project = self.connection.source.getProject(event.project_name)
                self.connection._clearBranchCache(project)
            if event.newrev == '0' * 40:
                event.branch_deleted = True
                event.branch = event.ref
                project = self.connection.source.getProject(event.project_name)
                self.connection._clearBranchCache(project)

        self._getChange(event)
        self.connection.logEvent(event)
        self.connection.sched.addEvent(event)

    def _getChange(self, event):
        # Grab the change if we are managing the project or if it exists in the
        # cache as it may be a dependency
        if event.change_number:
            refresh = True
            if event.change_number not in self.connection._change_cache:
                refresh = False
                for tenant in self.connection.sched.abide.tenants.values():
                    # TODO(fungi): it would be better to have some simple means
                    # of inferring the hostname from the connection, or at
                    # least split this into separate method arguments, rather
                    # than assembling and passing in a baked string.
                    if (None, None) != tenant.getProject('/'.join((
                            self.connection.canonical_hostname,
                            event.project_name))):
                        refresh = True
                        break

            if refresh:
                # Call _getChange for the side effect of updating the
                # cache.  Note that this modifies Change objects outside
                # the main thread.
                # NOTE(jhesketh): Ideally we'd just remove the change from the
                # cache to denote that it needs updating. However the change
                # object is already used by Items and hence BuildSets etc. and
                # we need to update those objects by reference so that they
                # have the correct/new information and also avoid hitting
                # gerrit multiple times.
                self.connection._getChange(event.change_number,
                                           event.patch_number,
                                           refresh=True, event=event)

    def run(self):
        while True:
            if self._stopped:
                return
            try:
                self._handleEvent()
            except Exception:
                self.log.exception("Exception moving Gerrit event:")
            finally:
                self.connection.eventDone()


class GerritWatcher(threading.Thread):
    log = logging.getLogger("gerrit.GerritWatcher")
    poll_timeout = 500

    def __init__(self, gerrit_connection, username, hostname, port=29418,
                 keyfile=None, keepalive=60):
        threading.Thread.__init__(self)
        self.username = username
        self.keyfile = keyfile
        self.hostname = hostname
        self.port = port
        self.gerrit_connection = gerrit_connection
        self.keepalive = keepalive
        self._stopped = False

    def _read(self, fd):
        while True:
            l = fd.readline()
            data = json.loads(l)
            self.log.debug("Received data from Gerrit event stream: \n%s" %
                           pprint.pformat(data))
            self.gerrit_connection.addEvent(data)
            # Continue until all the lines received are consumed
            if fd._pos == fd._realpos:
                break

    def _listen(self, stdout, stderr):
        poll = select.poll()
        poll.register(stdout.channel)
        while not self._stopped:
            ret = poll.poll(self.poll_timeout)
            for (fd, event) in ret:
                if fd == stdout.channel.fileno():
                    if event == select.POLLIN:
                        self._read(stdout)
                    else:
                        raise Exception("event on ssh connection")

    def _run(self):
        try:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.WarningPolicy())
            client.connect(self.hostname,
                           username=self.username,
                           port=self.port,
                           key_filename=self.keyfile)
            transport = client.get_transport()
            transport.set_keepalive(self.keepalive)

            stdin, stdout, stderr = client.exec_command("gerrit stream-events")

            self._listen(stdout, stderr)

            if not stdout.channel.exit_status_ready():
                # The stream-event is still running but we are done polling
                # on stdout most likely due to being asked to stop.
                # Try to stop the stream-events command sending Ctrl-C
                stdin.write("\x03")
                time.sleep(.2)
                if not stdout.channel.exit_status_ready():
                    # we're still not ready to exit, lets force the channel
                    # closed now.
                    stdout.channel.close()
            ret = stdout.channel.recv_exit_status()
            self.log.debug("SSH exit status: %s" % ret)

            if ret and ret not in [-1, 130]:
                raise Exception("Gerrit error executing stream-events")
        except Exception:
            self.log.exception("Exception on ssh event stream with %s:",
                               self.gerrit_connection.connection_name)
            time.sleep(5)
        finally:
            # If we don't close on exceptions to connect we can leak the
            # connection and DoS Gerrit.
            client.close()

    def run(self):
        while not self._stopped:
            self._run()

    def stop(self):
        self.log.debug("Stopping watcher")
        self._stopped = True


class GerritPoller(threading.Thread):
    # Poll gerrit without stream-events
    log = logging.getLogger("gerrit.GerritPoller")
    poll_interval = 30

    def __init__(self, connection):
        threading.Thread.__init__(self)
        self.connection = connection
        self.last_merged_poll = 0
        self._stopped = False
        self._stop_event = threading.Event()

    def _makePendingCheckEvent(self, change, uuid, check):
        return {'type': 'pending-check',
                'uuid': uuid,
                'change': {
                    'project': change['patch_set']['repository'],
                    'number': change['patch_set']['change_number'],
                },
                'patchSet': {
                    'number': change['patch_set']['patch_set_id'],
                }}

    def _makeChangeMergedEvent(self, change):
        """Make a simulated change-merged event

        Mostly for the benefit of scheduler reconfiguration.
        """
        rev = change['revisions'][change['current_revision']]
        return {'type': 'change-merged',
                'change': {
                    'project': change['project'],
                    'number': change['_number'],
                },
                'patchSet': {
                    'number': rev['_number'],
                }}

    def _poll_checkers(self):
        try:
            for checker in self.connection.watched_checkers:
                changes = self.connection.get(
                    'plugins/checks/checks.pending/?'
                    'query=checker:%s+(state:NOT_STARTED)' % checker)
                for change in changes:
                    for uuid, check in change['pending_checks'].items():
                        event = self._makePendingCheckEvent(
                            change, uuid, check)
                        self.connection.addEvent(event)
        except Exception:
            self.log.exception("Exception on Gerrit poll with %s:",
                               self.connection.connection_name)

    def _poll_merged_changes(self):
        try:
            now = datetime.datetime.utcnow()
            age = self.last_merged_poll
            if age:
                # Allow an extra 4 seconds for request time
                age = int(math.ceil((now - age).total_seconds())) + 4
            changes = self.connection.simpleQueryHTTP(
                "status:merged -age:%ss" % (age,))
            self.last_merged_poll = now
            for change in changes:
                event = self._makeChangeMergedEvent(change)
                self.connection.addEvent(event)
        except Exception:
            self.log.exception("Exception on Gerrit poll with %s:",
                               self.connection.connection_name)

    def _run(self):
        self._poll_checkers()
        if not self.connection.enable_stream_events:
            self._poll_merged_changes()

    def run(self):
        last_start = time.time()
        while not self._stopped:
            next_start = last_start + self.poll_interval
            self._stop_event.wait(max(next_start - time.time(), 0))
            if self._stopped:
                break
            last_start = time.time()
            self._run()

    def stop(self):
        self.log.debug("Stopping watcher")
        self._stopped = True
        self._stop_event.set()


class GerritConnection(BaseConnection):
    driver_name = 'gerrit'
    log = logging.getLogger("zuul.GerritConnection")
    iolog = logging.getLogger("zuul.GerritConnection.io")
    depends_on_re = re.compile(r"^Depends-On: (I[0-9a-f]{40})\s*$",
                               re.MULTILINE | re.IGNORECASE)
    refname_bad_sequences = re2.compile(
        r"[ \\*\[?:^~\x00-\x1F\x7F]|"  # Forbidden characters
        r"@{|\.\.|\.$|^@$|/$|^/|//+")  # everything else we can check with re2
    replication_timeout = 300
    replication_retry_interval = 5
    _poller_class = GerritPoller

    def __init__(self, driver, connection_name, connection_config):
        super(GerritConnection, self).__init__(driver, connection_name,
                                               connection_config)
        self._project_branch_cache = {}
        if 'server' not in self.connection_config:
            raise Exception('server is required for gerrit connections in '
                            '%s' % self.connection_name)
        if 'user' not in self.connection_config:
            raise Exception('user is required for gerrit connections in '
                            '%s' % self.connection_name)

        self.user = self.connection_config.get('user')
        self.server = self.connection_config.get('server')
        self.canonical_hostname = self.connection_config.get(
            'canonical_hostname', self.server)
        self.port = int(self.connection_config.get('port', 29418))
        self.keyfile = self.connection_config.get('sshkey', None)
        self.keepalive = int(self.connection_config.get('keepalive', 60))
        # TODO(corvus): Document this when the checks api is stable;
        # it's not useful without it.
        self.enable_stream_events = self.connection_config.get(
            'stream_events', True)
        if self.enable_stream_events not in [
                'true', 'True', '1', 1, 'TRUE', True]:
            self.enable_stream_events = False
        self.watcher_thread = None
        self.poller_thread = None
        self.event_queue = queue.Queue()
        self.client = None
        self.watched_checkers = []
        self.project_checker_map = {}
        self.version = (0, 0, 0)

        self.baseurl = self.connection_config.get(
            'baseurl', 'https://%s' % self.server).rstrip('/')
        default_gitweb_url_template = '{baseurl}/gitweb?' \
                                      'p={project.name}.git;' \
                                      'a=commitdiff;h={sha}'
        url_template = self.connection_config.get('gitweb_url_template',
                                                  default_gitweb_url_template)
        self.gitweb_url_template = url_template

        self._change_cache = {}
        self.projects = {}
        self.gerrit_event_connector = None
        self.source = driver.getSource(self)

        self.session = None
        self.password = self.connection_config.get('password', None)
        self.auth_type = self.connection_config.get('auth_type', None)
        self.anonymous_git = False
        if self.password or self.auth_type == 'gcloud_service':
            self.verify_ssl = self.connection_config.get('verify_ssl', True)
            if self.verify_ssl not in ['true', 'True', '1', 1, 'TRUE']:
                self.verify_ssl = False
            self.user_agent = 'Zuul/%s %s' % (
                zuul_version.release_string,
                requests.utils.default_user_agent())
            self.session = requests.Session()
            if self.auth_type == 'digest':
                authclass = requests.auth.HTTPDigestAuth
            elif self.auth_type == 'form':
                authclass = FormAuth
            elif self.auth_type == 'gcloud_service':
                authclass = GCloudAuth
                # The executors in google cloud may not have access
                # to the gerrit account credentials, so just use
                # anonymous http access for git
                self.anonymous_git = True
            else:
                authclass = requests.auth.HTTPBasicAuth
            self.auth = authclass(self.user, self.password)

    def setWatchedCheckers(self, checkers_to_watch):
        self.log.debug("Setting watched checkers to %s", checkers_to_watch)
        self.watched_checkers = set()
        self.project_checker_map = {}
        schemes_to_watch = set()
        uuids_to_watch = set()
        for x in checkers_to_watch:
            if 'scheme' in x:
                schemes_to_watch.add(x['scheme'])
            if 'uuid' in x:
                uuids_to_watch.add(x['uuid'])
        if schemes_to_watch:
            # get a list of all configured checkers
            try:
                configured_checkers = self.get('plugins/checks/checkers/')
            except Exception:
                self.log.exception("Unable to get checkers")
                configured_checkers = []

            # filter it through scheme matches in checkers_to_watch
            for checker in configured_checkers:
                if checker['status'] != 'ENABLED':
                    continue
                checker_scheme, checker_id = checker['uuid'].split(':')
                repo = checker['repository']
                repo = self.canonical_hostname + '/' + repo
                # map scheme matches to project names
                if checker_scheme in schemes_to_watch:
                    repo_checkers = self.project_checker_map.setdefault(
                        repo, set())
                    repo_checkers.add(checker['uuid'])
                    self.watched_checkers.add(checker['uuid'])
        # add uuids from checkers_to_watch
        for x in uuids_to_watch:
            self.watched_checkers.add(x)

    def toDict(self):
        d = super().toDict()
        d.update({
            "baseurl": self.baseurl,
            "canonical_hostname": self.canonical_hostname,
            "server": self.server,
            "port": self.port,
        })
        return d

    def url(self, path):
        return self.baseurl + '/a/' + path

    def get(self, path):
        url = self.url(path)
        self.log.debug('GET: %s' % (url,))
        r = self.session.get(
            url,
            verify=self.verify_ssl,
            auth=self.auth, timeout=TIMEOUT,
            headers={'User-Agent': self.user_agent})
        self.log.debug('Received: %s %s' % (r.status_code, r.text,))
        if r.status_code != 200:
            raise Exception("Received response %s" % (r.status_code,))
        ret = None
        if r.text and len(r.text) > 4:
            try:
                ret = json.loads(r.text[4:])
            except Exception:
                self.log.exception(
                    "Unable to parse result %s from post to %s" %
                    (r.text, url))
                raise
        return ret

    def post(self, path, data):
        url = self.url(path)
        self.log.debug('POST: %s' % (url,))
        self.log.debug('data: %s' % (data,))
        r = self.session.post(
            url, data=json.dumps(data).encode('utf8'),
            verify=self.verify_ssl,
            auth=self.auth, timeout=TIMEOUT,
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'User-Agent': self.user_agent})
        self.log.debug('Received: %s %s' % (r.status_code, r.text,))
        if r.status_code != 200:
            raise Exception("Received response %s" % (r.status_code,))
        ret = None
        if r.text and len(r.text) > 4:
            try:
                ret = json.loads(r.text[4:])
            except Exception:
                self.log.exception(
                    "Unable to parse result %s from post to %s" %
                    (r.text, url))
                raise
        return ret

    def getProject(self, name: str) -> Project:
        return self.projects.get(name)

    def addProject(self, project: Project) -> None:
        self.projects[project.name] = project

    def clearBranchCache(self):
        self._project_branch_cache = {}

    def _clearBranchCache(self, project):
        try:
            del self._project_branch_cache[project.name]
        except KeyError:
            pass

    def maintainCache(self, relevant):
        # This lets the user supply a list of change objects that are
        # still in use.  Anything in our cache that isn't in the supplied
        # list should be safe to remove from the cache.
        remove = {}
        for change_number, patchsets in self._change_cache.items():
            for patchset, change in patchsets.items():
                if change not in relevant:
                    remove.setdefault(change_number, [])
                    remove[change_number].append(patchset)
        for change_number, patchsets in remove.items():
            for patchset in patchsets:
                del self._change_cache[change_number][patchset]
            if not self._change_cache[change_number]:
                del self._change_cache[change_number]

    def getChange(self, event, refresh=False):
        if event.change_number:
            change = self._getChange(event.change_number, event.patch_number,
                                     refresh=refresh)
        elif event.ref and event.ref.startswith('refs/tags/'):
            project = self.source.getProject(event.project_name)
            change = Tag(project)
            change.tag = event.ref[len('refs/tags/'):]
            change.ref = event.ref
            change.oldrev = event.oldrev
            change.newrev = event.newrev
            change.url = self._getWebUrl(project, sha=event.newrev)
        elif event.ref and not event.ref.startswith('refs/'):
            # Pre 2.13 Gerrit ref-updated events don't have branch prefixes.
            project = self.source.getProject(event.project_name)
            change = Branch(project)
            change.branch = event.ref
            change.ref = 'refs/heads/' + event.ref
            change.oldrev = event.oldrev
            change.newrev = event.newrev
            change.url = self._getWebUrl(project, sha=event.newrev)
        elif event.ref and event.ref.startswith('refs/heads/'):
            # From the timer trigger or Post 2.13 Gerrit
            project = self.source.getProject(event.project_name)
            change = Branch(project)
            change.ref = event.ref
            change.branch = event.ref[len('refs/heads/'):]
            change.oldrev = event.oldrev
            change.newrev = event.newrev
            change.url = self._getWebUrl(project, sha=event.newrev)
        elif event.ref:
            # catch-all ref (ie, not a branch or head)
            project = self.source.getProject(event.project_name)
            change = Ref(project)
            change.ref = event.ref
            change.oldrev = event.oldrev
            change.newrev = event.newrev
            change.url = self._getWebUrl(project, sha=event.newrev)
        else:
            self.log.warning("Unable to get change for %s" % (event,))
            change = None
        return change

    def _getChange(self, number, patchset, refresh=False, history=None,
                   event=None):
        # Ensure number and patchset are str
        number = str(number)
        patchset = str(patchset)
        change = self._change_cache.get(number, {}).get(patchset)
        if change and not refresh:
            return change
        if not change:
            change = GerritChange(None)
            change.number = number
            change.patchset = patchset
        self._change_cache.setdefault(change.number, {})
        self._change_cache[change.number][change.patchset] = change
        try:
            self._updateChange(change, event, history)
        except Exception:
            if self._change_cache.get(change.number, {}).get(change.patchset):
                del self._change_cache[change.number][change.patchset]
                if not self._change_cache[change.number]:
                    del self._change_cache[change.number]
            raise
        return change

    def _getDependsOnFromCommit(self, message, change, event):
        log = get_annotated_logger(self.log, event)
        records = []
        seen = set()
        for match in self.depends_on_re.findall(message):
            if match in seen:
                log.debug("Ignoring duplicate Depends-On: %s", match)
                continue
            seen.add(match)
            query = "change:%s" % (match,)
            log.debug("Updating %s: Running query %s to find needed changes",
                      change, query)
            records.extend(self.simpleQuery(query, event=event))
        return [(x.number, x.current_patchset) for x in records]

    def _getNeededByFromCommit(self, change_id, change, event):
        log = get_annotated_logger(self.log, event)
        records = []
        seen = set()
        query = 'message:{%s}' % change_id
        log.debug("Updating %s: Running query %s to find changes needed-by",
                  change, query)
        results = self.simpleQuery(query, event=event)
        for result in results:
            for match in self.depends_on_re.findall(
                    result.message):
                if match != change_id:
                    continue
                key = (result.number, result.current_patchset)
                if key in seen:
                    continue
                log.debug("Updating %s: Found change %s,%s "
                          "needs %s from commit",
                          change, key[0], key[1], change_id)
                seen.add(key)
                records.append(result)
        return [(x.number, x.current_patchset) for x in records]

    def _updateChange(self, change, event, history):
        log = get_annotated_logger(self.log, event)

        # In case this change is already in the history we have a
        # cyclic dependency and don't need to update ourselves again
        # as this gets done in a previous frame of the call stack.
        # NOTE(jeblair): The only case where this can still be hit is
        # when we get an event for a change with no associated
        # patchset; for instance, when the gerrit topic is changed.
        # In that case, we will update change 1234,None, which will be
        # inserted into the cache as its own entry, but then we will
        # resolve the patchset before adding it to the history list,
        # then if there are dependencies, we can walk down and then
        # back up to the version of this change with a patchset which
        # will match the history list but will have bypassed the
        # change cache because the previous object had a patchset of
        # None.  All paths hit the change cache first.  To be able to
        # drop history, we need to resolve the patchset on events with
        # no patchsets before adding the entry to the change cache.
        if (history and change.number and change.patchset and
            (change.number, change.patchset) in history):
            log.debug("Change %s is in history", change)
            return change

        log.info("Updating %s", change)
        data = self.queryChange(change.number, event=event)
        change.update(data, self)

        if not change.is_merged:
            self._updateChangeDependencies(log, change, data, event, history)

        self.sched.onChangeUpdated(change, event)

        return change

    def _updateChangeDependencies(self, log, change, data, event, history):
        if history is None:
            history = []
        else:
            history = history[:]
        history.append((change.number, change.patchset))

        needs_changes = set()
        git_needs_changes = []
        if data.depends_on is not None:
            dep_num, dep_ps = data.depends_on
            log.debug("Updating %s: Getting git-dependent change %s,%s",
                      change, dep_num, dep_ps)
            dep = self._getChange(dep_num, dep_ps, history=history,
                                  event=event)
            # This is a git commit dependency. So we only ignore it if it is
            # already merged. So even if it is "ABANDONED", we should not
            # ignore it.
            if (not dep.is_merged) and dep not in needs_changes:
                git_needs_changes.append(dep)
                needs_changes.add(dep)
        change.git_needs_changes = git_needs_changes

        compat_needs_changes = []
        for (dep_num, dep_ps) in self._getDependsOnFromCommit(
                change.message, change, event):
            log.debug("Updating %s: Getting commit-dependent "
                      "change %s,%s", change, dep_num, dep_ps)
            dep = self._getChange(dep_num, dep_ps, history=history,
                                  event=event)
            if dep.open and dep not in needs_changes:
                compat_needs_changes.append(dep)
                needs_changes.add(dep)
        change.compat_needs_changes = compat_needs_changes

        needed_by_changes = set()
        git_needed_by_changes = []
        for (dep_num, dep_ps) in data.needed_by:
            log.debug("Updating %s: Getting git-needed change %s,%s",
                      change, dep_num, dep_ps)
            dep = self._getChange(dep_num, dep_ps, history=history,
                                  event=event)
            if (dep.open and dep.is_current_patchset and
                dep not in needed_by_changes):
                git_needed_by_changes.append(dep)
                needed_by_changes.add(dep)
        change.git_needed_by_changes = git_needed_by_changes

        compat_needed_by_changes = []
        for (dep_num, dep_ps) in self._getNeededByFromCommit(
                change.id, change, event):
            log.debug("Updating %s: Getting commit-needed change %s,%s",
                      change, dep_num, dep_ps)
            # Because a commit needed-by may be a cross-repo
            # dependency, cause that change to refresh so that it will
            # reference the latest patchset of its Depends-On (this
            # change). In case the dep is already in history we already
            # refreshed this change so refresh is not needed in this case.
            refresh = (dep_num, dep_ps) not in history
            dep = self._getChange(
                dep_num, dep_ps, refresh=refresh, history=history, event=event)
            if (dep.open and dep.is_current_patchset
                and dep not in needed_by_changes):
                compat_needed_by_changes.append(dep)
                needed_by_changes.add(dep)
        change.compat_needed_by_changes = compat_needed_by_changes

    def isMerged(self, change, head=None):
        self.log.debug("Checking if change %s is merged" % change)
        if not change.number:
            self.log.debug("Change has no number; considering it merged")
            # Good question.  It's probably ref-updated, which, ah,
            # means it's merged.
            return True

        data = self.queryChange(change.number)
        change.update(data, self)
        if change.is_merged:
            self.log.debug("Change %s is merged" % (change,))
        else:
            self.log.debug("Change %s is not merged" % (change,))
        if not head:
            return change.is_merged
        if not change.is_merged:
            return False

        ref = 'refs/heads/' + change.branch
        self.log.debug("Waiting for %s to appear in git repo" % (change))
        if self._waitForRefSha(change.project, ref, change._ref_sha):
            self.log.debug("Change %s is in the git repo" %
                           (change))
            return True
        self.log.debug("Change %s did not appear in the git repo" %
                       (change))
        return False

    def _waitForRefSha(self, project: Project,
                       ref: str, old_sha: str='') -> bool:
        # Wait for the ref to show up in the repo
        start = time.time()
        while time.time() - start < self.replication_timeout:
            sha = self.getRefSha(project, ref)
            if old_sha != sha:
                return True
            time.sleep(self.replication_retry_interval)
        return False

    def getRefSha(self, project: Project, ref: str) -> str:
        refs = {}  # type: Dict[str, str]
        try:
            refs = self.getInfoRefs(project)
        except Exception:
            self.log.exception("Exception looking for ref %s" %
                               ref)
        sha = refs.get(ref, '')
        return sha

    def canMerge(self, change, allow_needs, event=None):
        log = get_annotated_logger(self.log, event)
        if not change.number:
            log.debug("Change has no number; considering it merged")
            # Good question.  It's probably ref-updated, which, ah,
            # means it's merged.
            return True
        if change.missing_labels <= set(allow_needs):
            return True
        return False

    def getProjectOpenChanges(self, project: Project) -> List[GerritChange]:
        # This is a best-effort function in case Gerrit is unable to return
        # a particular change.  It happens.
        query = "project:{%s} status:open" % (project.name,)
        self.log.debug("Running query %s to get project open changes" %
                       (query,))
        data = self.simpleQuery(query)
        changes = []  # type: List[GerritChange]
        for record in data:
            try:
                changes.append(
                    self._getChange(record.number, record.current_patchset))
            except Exception:
                self.log.exception("Unable to query change %s",
                                   record.number)
        return changes

    @staticmethod
    def _checkRefFormat(refname: str) -> bool:
        # These are the requirements for valid ref names as per
        # man git-check-ref-format
        parts = refname.split('/')
        return \
            (GerritConnection.refname_bad_sequences.search(refname) is None and
             len(parts) > 1 and
             not any(part.startswith('.') or part.endswith('.lock')
                     for part in parts))

    def getProjectBranches(self, project: Project, tenant) -> List[str]:
        branches = self._project_branch_cache.get(project.name)
        if branches is not None:
            return branches

        refs = self.getInfoRefs(project)
        heads = [str(k[len('refs/heads/'):]) for k in refs
                 if k.startswith('refs/heads/') and
                 GerritConnection._checkRefFormat(k)]
        self._project_branch_cache[project.name] = heads
        return heads

    def addEvent(self, data):
        return self.event_queue.put((time.time(), data))

    def getEvent(self):
        return self.event_queue.get()

    def eventDone(self):
        self.event_queue.task_done()

    def review(self, item, message, submit, labels, checks_api,
               file_comments, zuul_event_id=None):
        if self.session:
            meth = self.review_http
        else:
            meth = self.review_ssh
        return meth(item, message, submit, labels, checks_api,
                    file_comments, zuul_event_id=zuul_event_id)

    def review_ssh(self, item, message, submit, labels, checks_api,
                   file_comments, zuul_event_id=None):
        if checks_api:
            log = get_annotated_logger(self.log, zuul_event_id)
            log.error("Zuul is configured to report to the checks API, "
                      "but no HTTP password is present for the connection "
                      "in the configuration file.")
        change = item.change
        project = change.project.name
        cmd = 'gerrit review --project %s' % project
        if message:
            cmd += ' --message %s' % shlex.quote(message)
        if submit:
            cmd += ' --submit'
        for key, val in labels.items():
            if val is True:
                cmd += ' --%s' % key
            else:
                cmd += ' --label %s=%s' % (key, val)
        changeid = '%s,%s' % (change.number, change.patchset)
        cmd += ' %s' % changeid
        out, err = self._ssh(cmd, zuul_event_id=zuul_event_id)
        return err

    def report_checks(self, log, item, changeid, checkinfo):
        change = item.change
        checkinfo = checkinfo.copy()
        uuid = checkinfo.pop('uuid', None)
        scheme = checkinfo.pop('scheme', None)
        if uuid is None:
            uuids = self.project_checker_map.get(
                change.project.canonical_name, set())
            for u in uuids:
                if u.split(':')[0] == scheme:
                    uuid = u
                    break
        if uuid is None:
            log.error("Unable to find matching checker for %s %s",
                      item, checkinfo)
            return

        def fmt(t):
            return str(datetime.datetime.fromtimestamp(t))

        if item.enqueue_time:
            checkinfo['started'] = fmt(item.enqueue_time)
            if item.report_time:
                checkinfo['finished'] = fmt(item.report_time)
                url = item.formatStatusUrl()
                if url:
                    checkinfo['url'] = url
        if checkinfo:
            for x in range(1, 4):
                try:
                    self.post('changes/%s/revisions/%s/checks/%s' %
                              (changeid, change.commit, uuid),
                              checkinfo)
                    break
                except Exception:
                    log.exception("Error submitting check data to gerrit, "
                                  "attempt %s", x)
                    time.sleep(x * 10)

    def review_http(self, item, message, submit, labels,
                    checks_api, file_comments, zuul_event_id=None):
        change = item.change
        log = get_annotated_logger(self.log, zuul_event_id)
        data = dict(message=message,
                    strict_labels=False)
        if change.is_current_patchset:
            if labels:
                data['labels'] = labels
            if file_comments:
                if self.version >= (2, 15, 0):
                    file_comments = copy.deepcopy(file_comments)
                    url = item.formatStatusUrl()
                    for comments in itertools.chain(file_comments.values()):
                        for comment in comments:
                            comment['robot_id'] = 'zuul'
                            comment['robot_run_id'] = \
                                item.current_build_set.uuid
                            if url:
                                comment['url'] = url
                    data['robot_comments'] = file_comments
                else:
                    data['comments'] = file_comments
        data['tag'] = 'autogenerated:zuul:%s' % (item.pipeline.name)
        changeid = "%s~%s~%s" % (
            urllib.parse.quote(str(change.project), safe=''),
            urllib.parse.quote(str(change.branch), safe=''),
            change.id)
        if checks_api:
            self.report_checks(log, item, changeid, checks_api)
        if (message or data.get('labels') or data.get('comments')):
            for x in range(1, 4):
                try:
                    self.post('changes/%s/revisions/%s/review' %
                              (changeid, change.commit),
                              data)
                    break
                except Exception:
                    log.exception(
                        "Error submitting data to gerrit, attempt %s", x)
                    time.sleep(x * 10)
        if change.is_current_patchset and submit:
            for x in range(1, 4):
                try:
                    self.post('changes/%s/submit' % (changeid,), {})
                    break
                except Exception:
                    log.exception(
                        "Error submitting data to gerrit, attempt %s", x)
                    time.sleep(x * 10)

    def queryChangeSSH(self, number, event=None):
        args = '--all-approvals --comments --commit-message'
        args += ' --current-patch-set --dependencies --files'
        args += ' --patch-sets --submit-records'
        cmd = 'gerrit query --format json %s %s' % (args, number)
        out, err = self._ssh(cmd)
        if not out:
            return False
        lines = out.split('\n')
        if not lines:
            return False
        data = json.loads(lines[0])
        if not data:
            return False
        iolog = get_annotated_logger(self.iolog, event)
        iolog.debug("Received data from Gerrit query: \n%s",
                    pprint.pformat(data))
        return data

    def queryChangeHTTP(self, number, event=None):
        data = self.get('changes/%s?o=DETAILED_ACCOUNTS&o=CURRENT_REVISION&'
                        'o=CURRENT_COMMIT&o=CURRENT_FILES&o=LABELS&'
                        'o=DETAILED_LABELS' % (number,))
        related = self.get('changes/%s/revisions/%s/related' % (
            number, data['current_revision']))
        return data, related

    def queryChange(self, number, event=None):
        if self.session:
            data, related = self.queryChangeHTTP(number, event=event)
            return GerritChangeData(GerritChangeData.HTTP, data, related)
        else:
            data = self.queryChangeSSH(number, event=event)
            return GerritChangeData(GerritChangeData.SSH, data)

    def simpleQuerySSH(self, query, event=None):
        def _query_chunk(query, event):
            args = '--commit-message --current-patch-set'

            cmd = 'gerrit query --format json %s %s' % (
                args, query)
            out, err = self._ssh(cmd)
            if not out:
                return False
            lines = out.split('\n')
            if not lines:
                return False

            # filter out blank lines
            data = [json.loads(line) for line in lines
                    if line.startswith('{')]

            # check last entry for more changes
            more_changes = None
            if 'moreChanges' in data[-1]:
                more_changes = data[-1]['moreChanges']

            # we have to remove the statistics line
            del data[-1]

            if not data:
                return False, more_changes
            iolog = get_annotated_logger(self.iolog, event)
            iolog.debug("Received data from Gerrit query: \n%s",
                        pprint.pformat(data))
            return data, more_changes

        # gerrit returns 500 results by default, so implement paging
        # for large projects like nova
        alldata = []
        chunk, more_changes = _query_chunk(query, event)
        while(chunk):
            alldata.extend(chunk)
            if more_changes is None:
                # continue sortKey based (before Gerrit 2.9)
                resume = "resume_sortkey:'%s'" % chunk[-1]["sortKey"]
            elif more_changes:
                # continue moreChanges based (since Gerrit 2.9)
                resume = "-S %d" % len(alldata)
            else:
                # no more changes
                break

            chunk, more_changes = _query_chunk(
                "%s %s" % (query, resume), event)
        return alldata

    def simpleQueryHTTP(self, query, event=None):
        iolog = get_annotated_logger(self.iolog, event)
        changes = []
        sortkey = ''
        done = False
        offset = 0
        query = urllib.parse.quote(query, safe='')
        while not done:
            # We don't actually want to limit to 500, but that's the
            # server-side default, and if we don't specify this, we
            # won't get a _more_changes flag.
            q = ('changes/?n=500%s&o=CURRENT_REVISION&o=CURRENT_COMMIT&'
                 'q=%s' % (sortkey, query))
            iolog.debug('Query: %s', q)
            batch = self.get(q)
            iolog.debug("Received data from Gerrit query: \n%s",
                        pprint.pformat(batch))
            done = True
            if batch:
                changes += batch
                if '_more_changes' in batch[-1]:
                    done = False
                    if '_sortkey' in batch[-1]:
                        sortkey = '&N=%s' % (batch[-1]['_sortkey'],)
                    else:
                        offset += len(batch)
                        sortkey = '&start=%s' % (offset,)
        return changes

    def simpleQuery(self, query, event=None):
        if self.session:
            # None of the users of this method require dependency
            # data, so we only perform the change query and omit the
            # related changes query.
            alldata = self.simpleQueryHTTP(query, event=event)
            return [GerritChangeData(GerritChangeData.HTTP, data)
                    for data in alldata]
        else:
            alldata = self.simpleQuerySSH(query, event=event)
            return [GerritChangeData(GerritChangeData.SSH, data)
                    for data in alldata]

    def _uploadPack(self, project: Project) -> str:
        if self.session:
            url = ('%s/%s/info/refs?service=git-upload-pack' %
                   (self.baseurl, project.name))
            r = self.session.get(
                url,
                verify=self.verify_ssl,
                auth=self.auth, timeout=TIMEOUT,
                headers={'User-Agent': self.user_agent})
            self.iolog.debug('Received: %s %s' % (r.status_code, r.text,))
            if r.status_code != 200:
                raise Exception("Received response %s" % (r.status_code,))
            out = r.text[r.text.find('\n') + 5:]
        else:
            cmd = "git-upload-pack %s" % project.name
            out, err = self._ssh(cmd, "0000")
        return out

    def _open(self):
        if self.client:
            # Paramiko needs explicit closes, its possible we will open even
            # with an unclosed client so explicitly close here.
            self.client.close()
        try:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.WarningPolicy())
            client.connect(self.server,
                           username=self.user,
                           port=self.port,
                           key_filename=self.keyfile)
            transport = client.get_transport()
            transport.set_keepalive(self.keepalive)
            self.client = client
        except Exception:
            client.close()
            self.client = None
            raise

    def _ssh(self, command, stdin_data=None, zuul_event_id=None):
        log = get_annotated_logger(self.log, zuul_event_id)
        if not self.client:
            self._open()

        try:
            log.debug("SSH command:\n%s", command)
            stdin, stdout, stderr = self.client.exec_command(command)
        except Exception:
            self._open()
            stdin, stdout, stderr = self.client.exec_command(command)

        if stdin_data:
            stdin.write(stdin_data)

        out = stdout.read().decode('utf-8')
        self.iolog.debug("SSH received stdout:\n%s" % out)

        ret = stdout.channel.recv_exit_status()
        log.debug("SSH exit status: %s", ret)

        err = stderr.read().decode('utf-8')
        if err.strip():
            log.debug("SSH received stderr:\n%s", err)

        if ret:
            log.debug("SSH received stdout:\n%s", out)
            raise Exception("Gerrit error executing %s" % command)
        return (out, err)

    def getInfoRefs(self, project: Project) -> Dict[str, str]:
        try:
            data = self._uploadPack(project)
        except Exception:
            self.log.error("Cannot get references from %s" % project)
            raise  # keeps error information
        ret = {}
        read_advertisement = False
        i = 0
        while i < len(data):
            if len(data) - i < 4:
                raise Exception("Invalid length in info/refs")
            plen = int(data[i:i + 4], 16)
            i += 4
            # It's the length of the packet, including the 4 bytes of the
            # length itself, unless it's null, in which case the length is
            # not included.
            if plen > 0:
                plen -= 4
            if len(data) - i < plen:
                raise Exception("Invalid data in info/refs")
            line = data[i:i + plen]
            i += plen
            if not read_advertisement:
                read_advertisement = True
                continue
            if plen == 0:
                # The terminating null
                continue
            line = line.strip()
            revision, ref = line.split()
            ret[ref] = revision
        return ret

    def getGitUrl(self, project: Project) -> str:
        if self.anonymous_git:
            url = ('%s/%s' % (self.baseurl, project.name))
        elif self.session:
            baseurl = list(urllib.parse.urlparse(self.baseurl))
            # Make sure we escape '/' symbols, otherwise git's url
            # parser will think the username is a hostname.
            baseurl[1] = '%s:%s@%s' % (
                urllib.parse.quote(self.user, safe=''),
                urllib.parse.quote(self.password, safe=''),
                baseurl[1])
            baseurl = urllib.parse.urlunparse(baseurl)
            url = ('%s/%s' % (baseurl, project.name))
        else:
            url = 'ssh://%s@%s:%s/%s' % (self.user, self.server, self.port,
                                         project.name)
        return url

    def _getWebUrl(self, project: Project, sha: str=None) -> str:
        return self.gitweb_url_template.format(
            baseurl=self.baseurl,
            project=project.getSafeAttributes(),
            sha=sha)

    def _getRemoteVersion(self):
        version = self.get('config/server/version')
        base = version.split('-')[0]
        parts = base.split('.')
        major = minor = micro = 0
        if len(parts) > 0:
            major = int(parts[0])
        if len(parts) > 1:
            minor = int(parts[1])
        if len(parts) > 2:
            micro = int(parts[2])
        self.version = (major, minor, micro)
        self.log.info("Remote version is: %s (parsed as %s)" %
                      (version, self.version))

    def onLoad(self):
        self.log.debug("Starting Gerrit Connection/Watchers")
        try:
            if self.session:
                self._getRemoteVersion()
        except Exception:
            self.log.exception("Unable to determine remote Gerrit version")

        if self.enable_stream_events:
            self._start_watcher_thread()
        self._start_poller_thread()
        self._start_event_connector()

    def onStop(self):
        self.log.debug("Stopping Gerrit Connection/Watchers")
        self._stop_watcher_thread()
        self._stop_poller_thread()
        self._stop_event_connector()

    def _stop_watcher_thread(self):
        if self.watcher_thread:
            self.watcher_thread.stop()
            self.watcher_thread.join()

    def _start_watcher_thread(self):
        self.watcher_thread = GerritWatcher(
            self,
            self.user,
            self.server,
            self.port,
            keyfile=self.keyfile,
            keepalive=self.keepalive)
        self.watcher_thread.start()

    def _stop_poller_thread(self):
        if self.poller_thread:
            self.poller_thread.stop()
            self.poller_thread.join()

    def _start_poller_thread(self):
        self.poller_thread = self._poller_class(self)
        self.poller_thread.start()

    def _stop_event_connector(self):
        if self.gerrit_event_connector:
            self.gerrit_event_connector.stop()
            self.gerrit_event_connector.join()

    def _start_event_connector(self):
        self.gerrit_event_connector = GerritEventConnector(self)
        self.gerrit_event_connector.start()
