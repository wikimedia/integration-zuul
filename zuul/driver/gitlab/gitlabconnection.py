# Copyright 2019 Red Hat, Inc.
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

import logging
import threading
import json
import queue
import cherrypy
import voluptuous as v
import time

from zuul.connection import BaseConnection
from zuul.web.handler import BaseWebController
from zuul.lib.gearworker import ZuulGearWorker


class GitlabGearmanWorker(object):
    """A thread that answers gearman requests"""
    log = logging.getLogger("zuul.GitlabGearmanWorker")

    def __init__(self, connection):
        self.config = connection.sched.config
        self.connection = connection
        handler = "gitlab:%s:payload" % self.connection.connection_name
        self.jobs = {
            handler: self.handle_payload,
        }
        self.gearworker = ZuulGearWorker(
            'Zuul Gitlab Worker',
            'zuul.GitlabGearmanWorker',
            'gitlab',
            self.config,
            self.jobs)

    def handle_payload(self, job):
        args = json.loads(job.arguments)
        payload = args["payload"]

        self.log.info(
            "Gitlab Webhook Received (event kind: %(object_kind)s ",
            "event name: %(event_name)s)" % payload)

        try:
            self.__dispatch_event(payload)
            output = {'return_code': 200}
        except Exception:
            output = {'return_code': 503}
            self.log.exception("Exception handling Gitlab event:")

        job.sendWorkComplete(json.dumps(output))

    def __dispatch_event(self, payload):
        event = payload['event_name']
        try:
            self.log.info("Dispatching event %s" % event)
            self.connection.addEvent(payload, event)
        except Exception as err:
            message = 'Exception dispatching event: %s' % str(err)
            self.log.exception(message)
            raise Exception(message)

    def start(self):
        self.gearworker.start()

    def stop(self):
        self.gearworker.stop()


class GitlabEventConnector(threading.Thread):
    """Move events from Gitlab into the scheduler"""

    log = logging.getLogger("zuul.GitlabEventConnector")

    def __init__(self, connection):
        super(GitlabEventConnector, self).__init__()
        self.daemon = True
        self.connection = connection
        self._stopped = False
        self.event_handler_mapping = {}

    def stop(self):
        self._stopped = True
        self.connection.addEvent(None)

    def _handleEvent(self):
        ts, json_body, event_type = self.connection.getEvent()
        if self._stopped:
            return

        self.log.info("Received event: %s" % str(event_type))

        if event_type not in self.event_handler_mapping:
            message = "Unhandled Gitlab event: %s" % event_type
            self.log.info(message)
            return

    def run(self):
        while True:
            if self._stopped:
                return
            try:
                self._handleEvent()
            except Exception:
                self.log.exception("Exception moving Gitlab event:")
            finally:
                self.connection.eventDone()


class GitlabConnection(BaseConnection):
    driver_name = 'gitlab'
    log = logging.getLogger("zuul.GitlabConnection")
    payload_path = 'payload'

    def __init__(self, driver, connection_name, connection_config):
        super(GitlabConnection, self).__init__(
            driver, connection_name, connection_config)
        self.projects = {}
        self.server = self.connection_config.get('server', 'gitlab.com')
        self.canonical_hostname = self.connection_config.get(
            'canonical_hostname', self.server)
        self.webhook_token = self.connection_config.get(
            'webhook_token', '')
        self.sched = None
        self.event_queue = queue.Queue()

    def _start_event_connector(self):
        self.gitlab_event_connector = GitlabEventConnector(self)
        self.gitlab_event_connector.start()

    def _stop_event_connector(self):
        if self.gitlab_event_connector:
            self.gitlab_event_connector.stop()
            self.gitlab_event_connector.join()

    def onLoad(self):
        self.log.info('Starting Gitlab connection: %s' % self.connection_name)
        self.gearman_worker = GitlabGearmanWorker(self)
        self.log.info('Starting event connector')
        self._start_event_connector()
        self.log.info('Starting GearmanWorker')
        self.gearman_worker.start()

    def onStop(self):
        if hasattr(self, 'gearman_worker'):
            self.gearman_worker.stop()
            self._stop_event_connector()

    def addEvent(self, data, event=None):
        return self.event_queue.put((time.time(), data, event))

    def getEvent(self):
        return self.event_queue.get()

    def eventDone(self):
        self.event_queue.task_done()

    def getWebController(self, zuul_web):
        return GitlabWebController(zuul_web, self)

    def getChange(self, event):
        return None

    def getProject(self, name):
        return self.projects.get(name)

    def addProject(self, project):
        self.projects[project.name] = project


class GitlabWebController(BaseWebController):

    log = logging.getLogger("zuul.GitlabWebController")

    def __init__(self, zuul_web, connection):
        self.connection = connection
        self.zuul_web = zuul_web

    def _validate_token(self, headers):
        try:
            event_token = headers['x-gitlab-token']
        except KeyError:
            raise cherrypy.HTTPError(401, 'x-gitlab-token header missing.')

        configured_token = self.connection.webhook_token
        if not configured_token == event_token:
            self.log.debug(
                "Missmatch (Incoming token: %s, Configured token: %s)" % (
                    event_token, configured_token))
            raise cherrypy.HTTPError(
                401,
                'Token does not match the server side configured token')

    @cherrypy.expose
    @cherrypy.tools.json_out(content_type='application/json; charset=utf-8')
    def payload(self):
        headers = dict()
        for key, value in cherrypy.request.headers.items():
            headers[key.lower()] = value
        body = cherrypy.request.body.read()
        self.log.info("Event header: %s" % headers)
        self.log.info("Event body: %s" % body)
        self._validate_token(headers)
        json_payload = json.loads(body.decode('utf-8'))

        job = self.zuul_web.rpc.submitJob(
            'gitlab:%s:payload' % self.connection.connection_name,
            {'payload': json_payload})

        return json.loads(job.data[0])


def getSchema():
    return v.Any(str, v.Schema(dict))
