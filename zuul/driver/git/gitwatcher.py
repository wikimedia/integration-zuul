# Copyright 2011 OpenStack, LLC.
# Copyright 2012 Hewlett-Packard Development Company, L.P.
# Copyright 2020 Red Hat, Inc.
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

import os
import logging
import threading
import time

import git

from zuul.driver.git.gitmodel import EMPTY_GIT_REF


# This class may be used by any driver to implement git head polling.
class GitWatcher(threading.Thread):
    log = logging.getLogger("zuul.connection.git.watcher")

    def __init__(self, connection, baseurl, poll_delay, callback):
        """Watch for branch changes

        Watch every project listed in the connection and call a
        callback method with information about branch changes.

        :param zuul.Connection connection:
           The Connection to watch.
        :param str baseurl:
           The HTTP(S) URL where git repos are hosted.
        :param int poll_delay:
           The interval between polls.
        :param function callback:
           A callback method to be called for each updated ref.  The sole
           argument is a dictionary describing the update.
        """
        threading.Thread.__init__(self)
        self.daemon = True
        self.connection = connection
        self.baseurl = baseurl
        self.poll_delay = poll_delay
        self._stopped = False
        self.projects_refs = {}
        self.callback = callback
        # This is used by the test framework
        self._event_count = 0
        self._pause = False

    def lsRemote(self, project):
        refs = {}
        client = git.cmd.Git()
        output = client.ls_remote(
            "--heads", "--tags",
            os.path.join(self.baseurl, project))
        for line in output.splitlines():
            sha, ref = line.split('\t')
            if ref.startswith('refs/'):
                refs[ref] = sha
        return refs

    def compareRefs(self, project, refs):
        events = []
        # Fetch previous refs state
        base_refs = self.projects_refs.get(project)
        # Create list of created refs
        rcreateds = set(refs.keys()) - set(base_refs.keys())
        # Create list of deleted refs
        rdeleteds = set(base_refs.keys()) - set(refs.keys())
        # Create the list of updated refs
        updateds = {}
        for ref, sha in refs.items():
            if ref in base_refs and base_refs[ref] != sha:
                updateds[ref] = sha
        for ref in rcreateds:
            event = {
                'project': project,
                'ref': ref,
                'branch_created': True,
                'oldrev': EMPTY_GIT_REF,
                'newrev': refs[ref]
            }
            events.append(event)
        for ref in rdeleteds:
            event = {
                'project': project,
                'ref': ref,
                'branch_deleted': True,
                'oldrev': base_refs[ref],
                'newrev': EMPTY_GIT_REF
            }
            events.append(event)
        for ref, sha in updateds.items():
            event = {
                'project': project,
                'ref': ref,
                'branch_updated': True,
                'oldrev': base_refs[ref],
                'newrev': sha
            }
            events.append(event)
        return events

    def _run(self):
        self.log.debug("Walk through projects refs for connection: %s" %
                       self.connection.connection_name)
        try:
            for project in self.connection.projects:
                refs = self.lsRemote(project)
                self.log.debug("Read %s refs for project %s",
                               len(refs), project)
                if not self.projects_refs.get(project):
                    # State for this project does not exist yet so add it.
                    # No event will be triggered in this loop as
                    # projects_refs['project'] and refs are equal
                    self.projects_refs[project] = refs
                events = self.compareRefs(project, refs)
                self.projects_refs[project] = refs
                # Send events to the scheduler
                for event in events:
                    self.log.debug("Sending event: %s" % event)
                    self.callback(event)
                    self._event_count += 1
        except Exception as e:
            self.log.debug("Unexpected issue in _run loop: %s" % str(e))

    def run(self):
        while not self._stopped:
            if not self._pause:
                self._run()
                # Polling wait delay
            else:
                self.log.debug("Watcher is on pause")
            time.sleep(self.poll_delay)

    def stop(self):
        self._stopped = True
