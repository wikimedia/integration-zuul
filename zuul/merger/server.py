# Copyright 2014 OpenStack Foundation
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

import json
import logging
import threading

from zuul.lib import commandsocket
from zuul.lib.config import get_default
from zuul.lib.gearworker import ZuulGearWorker
from zuul.merger import merger


COMMANDS = ['stop']


class MergeServer(object):
    log = logging.getLogger("zuul.MergeServer")

    def __init__(self, config, connections={}):
        self.config = config

        merge_root = get_default(self.config, 'merger', 'git_dir',
                                 '/var/lib/zuul/merger-git')
        merge_email = get_default(self.config, 'merger', 'git_user_email')
        merge_name = get_default(self.config, 'merger', 'git_user_name')
        speed_limit = get_default(
            config, 'merger', 'git_http_low_speed_limit', '1000')
        speed_time = get_default(
            config, 'merger', 'git_http_low_speed_time', '30')
        git_timeout = get_default(config, 'merger', 'git_timeout', 300)
        self.merger = merger.Merger(
            merge_root, connections, merge_email, merge_name, speed_limit,
            speed_time, git_timeout=git_timeout)
        self.command_map = dict(
            stop=self.stop)
        command_socket = get_default(
            self.config, 'merger', 'command_socket',
            '/var/lib/zuul/merger.socket')
        self.command_socket = commandsocket.CommandSocket(command_socket)

        self.jobs = {
            'merger:merge': self.merge,
            'merger:cat': self.cat,
            'merger:refstate': self.refstate,
            'merger:fileschanges': self.fileschanges,
        }
        self.gearworker = ZuulGearWorker(
            'Zuul Merger',
            'zuul.MergeServer',
            'merger-gearman-worker',
            self.config,
            self.jobs)

    def start(self):
        self.log.debug("Starting worker")
        self.gearworker.start()

        self._command_running = True
        self.log.debug("Starting command processor")
        self.command_socket.start()
        self.command_thread = threading.Thread(
            target=self.runCommand, name='command')
        self.command_thread.daemon = True
        self.command_thread.start()

    def stop(self):
        self.log.debug("Stopping")
        self.gearworker.stop()
        self._command_running = False
        self.command_socket.stop()
        self.log.debug("Stopped")

    def join(self):
        self.gearworker.join()

    def runCommand(self):
        while self._command_running:
            try:
                command = self.command_socket.get().decode('utf8')
                if command != '_stop':
                    self.command_map[command]()
            except Exception:
                self.log.exception("Exception while processing command")

    def merge(self, job):
        self.log.debug("Got merge job: %s" % job.unique)
        args = json.loads(job.arguments)
        zuul_event_id = args.get('zuul_event_id')
        ret = self.merger.mergeChanges(
            args['items'], args.get('files'),
            args.get('dirs'), args.get('repo_state'),
            branches=args.get('branches'),
            zuul_event_id=zuul_event_id)
        result = dict(merged=(ret is not None))
        if ret is None:
            result['commit'] = result['files'] = result['repo_state'] = None
        else:
            (result['commit'], result['files'], result['repo_state'],
             recent, orig_commit) = ret
        result['zuul_event_id'] = zuul_event_id
        job.sendWorkComplete(json.dumps(result))

    def refstate(self, job):
        self.log.debug("Got refstate job: %s" % job.unique)
        args = json.loads(job.arguments)
        zuul_event_id = args.get('zuul_event_id')
        success, repo_state = self.merger.getRepoState(
            args['items'], branches=args.get('branches'))
        result = dict(updated=success,
                      repo_state=repo_state)
        result['zuul_event_id'] = zuul_event_id
        job.sendWorkComplete(json.dumps(result))

    def cat(self, job):
        self.log.debug("Got cat job: %s" % job.unique)
        args = json.loads(job.arguments)
        self.merger.updateRepo(args['connection'], args['project'])
        files = self.merger.getFiles(args['connection'], args['project'],
                                     args['branch'], args['files'],
                                     args.get('dirs'))
        result = dict(updated=True,
                      files=files)
        job.sendWorkComplete(json.dumps(result))

    def fileschanges(self, job):
        self.log.debug("Got fileschanges job: %s" % job.unique)
        args = json.loads(job.arguments)
        zuul_event_id = args.get('zuul_event_id')
        self.merger.updateRepo(args['connection'], args['project'],
                               zuul_event_id=zuul_event_id)
        files = self.merger.getFilesChanges(
            args['connection'], args['project'], args['branch'], args['tosha'],
            zuul_event_id=zuul_event_id)
        result = dict(updated=True,
                      files=files)
        result['zuul_event_id'] = zuul_event_id
        job.sendWorkComplete(json.dumps(result))
