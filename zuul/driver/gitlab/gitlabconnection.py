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
import uuid
import requests
from urllib.parse import quote_plus
from datetime import datetime

from zuul.connection import BaseConnection
from zuul.web.handler import BaseWebController
from zuul.lib.gearworker import ZuulGearWorker
from zuul.lib.logutil import get_annotated_logger

from zuul.driver.gitlab.gitlabmodel import GitlabTriggerEvent, MergeRequest


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
            "Gitlab Webhook Received event kind: %(object_kind)s" % payload)

        try:
            self.__dispatch_event(payload)
            output = {'return_code': 200}
        except Exception:
            output = {'return_code': 503}
            self.log.exception("Exception handling Gitlab event:")

        job.sendWorkComplete(json.dumps(output))

    def __dispatch_event(self, payload):
        self.log.info(payload)
        event = payload['object_kind']
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
        self.event_handler_mapping = {
            'merge_request': self._event_merge_request,
            'note': self._event_note,
        }

    def stop(self):
        self._stopped = True
        self.connection.addEvent(None)

    def _event_base(self, body):
        event = GitlabTriggerEvent()
        attrs = body['object_attributes']
        event.updated_at = int(datetime.strptime(
            attrs['updated_at'], '%Y-%m-%d %H:%M:%S %Z').strftime('%s'))
        event.created_at = int(datetime.strptime(
            attrs['created_at'], '%Y-%m-%d %H:%M:%S %Z').strftime('%s'))
        event.project_name = body['project']['path_with_namespace']
        event.ref = "refs/merge-requests/%s/head" % event.change_number
        return event

    # https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#merge-request-events
    def _event_merge_request(self, body):
        event = self._event_base(body)
        attrs = body['object_attributes']
        event.title = attrs['title']
        event.change_number = attrs['iid']
        event.branch = attrs['target_branch']
        event.patch_number = attrs['last_commit']['id']
        event.change_url = self.connection.getPullUrl(event.project_name,
                                                      event.change_number)
        if event.created_at == event.updated_at:
            event.action = 'opened'
        else:
            event.action = 'changed'
        event.type = 'gl_merge_request'
        return event

    # https://docs.gitlab.com/ee/user/project/integrations/webhooks.html#comment-on-merge-request
    def _event_note(self, body):
        event = self._event_base(body)
        event.comment = body['object_attributes']['note']
        mr = body['merge_request']
        event.title = mr['title']
        event.change_number = mr['iid']
        event.branch = mr['target_branch']
        event.patch_number = mr['last_commit']['id']
        event.change_url = self.connection.getPullUrl(event.project_name,
                                                      event.change_number)
        event.action = 'comment'
        event.type = 'gl_merge_request'
        return event

    def _handleEvent(self):
        ts, json_body, event_type = self.connection.getEvent()
        if self._stopped:
            return

        self.log.info("Received event: %s" % str(event_type))

        if event_type not in self.event_handler_mapping:
            message = "Unhandled Gitlab event: %s" % event_type
            self.log.info(message)
            return

        if event_type in self.event_handler_mapping:
            self.log.debug("Handling event: %s" % event_type)

        try:
            event = self.event_handler_mapping[event_type](json_body)
        except Exception:
            self.log.exception(
                'Exception when handling event: %s' % event_type)
            event = None

        if event:
            event.zuul_event_id = str(uuid.uuid4())
            event.timestamp = ts
            if event.change_number:
                project = self.connection.source.getProject(event.project_name)
                self.connection._getChange(project,
                                           event.change_number,
                                           event.patch_number,
                                           refresh=True,
                                           url=event.change_url,
                                           event=event)
            event.project_hostname = self.connection.canonical_hostname
            self.connection.logEvent(event)
            self.connection.sched.addEvent(event)

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


class GitlabAPIClientException(Exception):
    pass


class GitlabAPIClient():
    log = logging.getLogger("zuul.GitlabAPIClient")

    def __init__(self, baseurl, api_token):
        self.session = requests.Session()
        self.baseurl = '%s/api/v4/' % baseurl
        self.api_token = api_token
        self.headers = {'Authorization': 'Bearer %s' % (
            self.api_token)}

    def _manage_error(self, data, code, url, verb, zuul_event_id=None):
        if code < 400:
            return
        else:
            raise GitlabAPIClientException(
                "[e: %s] Unable to %s on %s (code: %s) due to: %s" % (
                    zuul_event_id, verb, url, code, data
                ))

    def get(self, url, zuul_event_id=None):
        log = get_annotated_logger(self.log, zuul_event_id)
        log.debug("Getting resource %s ..." % url)
        ret = self.session.get(url, headers=self.headers)
        log.debug("GET returned (code: %s): %s" % (
            ret.status_code, ret.text))
        return ret.json(), ret.status_code, ret.url, 'GET'

    def post(self, url, params=None, zuul_event_id=None):
        log = get_annotated_logger(self.log, zuul_event_id)
        log.info(
            "Posting on resource %s, params (%s) ..." % (url, params))
        ret = self.session.post(url, data=params, headers=self.headers)
        log.debug("POST returned (code: %s): %s" % (
            ret.status_code, ret.text))
        return ret.json(), ret.status_code, ret.url, 'POST'

    # https://docs.gitlab.com/ee/api/merge_requests.html#get-single-mr
    def get_mr(self, project_name, number, zuul_event_id=None):
        path = "/projects/%s/merge_requests/%s" % (
            quote_plus(project_name), number)
        resp = self.get(self.baseurl + path, zuul_event_id=zuul_event_id)
        self._manage_error(*resp, zuul_event_id=zuul_event_id)
        return resp[0]

    # https://docs.gitlab.com/ee/api/branches.html#list-repository-branches
    def get_project_branches(self, project_name, zuul_event_id=None):
        path = "/projects/%s/repository/branches" % (
            quote_plus(project_name))
        resp = self.get(self.baseurl + path, zuul_event_id=zuul_event_id)
        self._manage_error(*resp, zuul_event_id=zuul_event_id)
        return [branch['name'] for branch in resp[0]]

    # https://docs.gitlab.com/ee/api/notes.html#create-new-merge-request-note
    def comment_mr(self, project_name, number, msg, zuul_event_id=None):
        path = "/projects/%s/merge_requests/%s/notes" % (
            quote_plus(project_name), number)
        params = {'body': msg}
        resp = self.post(
            self.baseurl + path, params=params,
            zuul_event_id=zuul_event_id)
        self._manage_error(*resp, zuul_event_id=zuul_event_id)
        return resp[0]


class GitlabConnection(BaseConnection):
    driver_name = 'gitlab'
    log = logging.getLogger("zuul.GitlabConnection")
    payload_path = 'payload'

    def __init__(self, driver, connection_name, connection_config):
        super(GitlabConnection, self).__init__(
            driver, connection_name, connection_config)
        self.projects = {}
        self.project_branch_cache = {}
        self._change_cache = {}
        self.server = self.connection_config.get('server', 'gitlab.com')
        self.baseurl = self.connection_config.get(
            'baseurl', 'https://%s' % self.server).rstrip('/')
        self.cloneurl = self.connection_config.get(
            'cloneurl', self.baseurl).rstrip('/')
        self.canonical_hostname = self.connection_config.get(
            'canonical_hostname', self.server)
        self.webhook_token = self.connection_config.get(
            'webhook_token', '')
        self.api_token = self.connection_config.get(
            'api_token', '')
        self.gl_client = GitlabAPIClient(self.baseurl, self.api_token)
        self.sched = None
        self.event_queue = queue.Queue()
        self.source = driver.getSource(self)

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

    def getProject(self, name):
        return self.projects.get(name)

    def addProject(self, project):
        self.projects[project.name] = project

    def getProjectBranches(self, project, tenant):
        branches = self.project_branch_cache.get(project.name)

        if branches is not None:
            return branches

        branches = self.gl_client.get_project_branches(project.name)
        self.project_branch_cache[project.name] = branches

        self.log.info("Got branches for %s" % project.name)
        return branches

    def clearBranchCache(self):
        self.project_branch_cache = {}

    def getGitwebUrl(self, project, sha=None):
        url = '%s/%s' % (self.baseurl, project)
        if sha is not None:
            url += '/tree/%s' % sha
        return url

    def getPullUrl(self, project, number):
        return '%s/%s/merge_requests/%s' % (self.baseurl, project, number)

    def getGitUrl(self, project):
        return '%s/%s.git' % (self.cloneurl, project.name)

    def getChange(self, event, refresh=False):
        project = self.source.getProject(event.project_name)
        if event.change_number:
            self.log.info("Getting change for %s#%s" % (
                project, event.change_number))
            change = self._getChange(
                project, event.change_number, event.patch_number,
                refresh=refresh, event=event)
            change.source_event = event
            change.is_current_patchset = (change.patchset ==
                                          event.patch_number)
        else:
            self.log.info("Getting change for %s ref:%s" % (
                project, event.ref))
            raise NotImplementedError
        return change

    def _getChange(self, project, number, patchset=None,
                   refresh=False, url=None, event=None):
        log = get_annotated_logger(self.log, event)
        key = (project.name, number, patchset)
        change = self._change_cache.get(key)
        if change and not refresh:
            log.debug("Getting change from cache %s" % str(key))
            return change
        if not change:
            change = MergeRequest(project.name)
            change.project = project
            change.number = number
            # patchset is the tips commit of the PR
            change.patchset = patchset
            change.url = url
            change.uris = list(url)
        self._change_cache[key] = change
        try:
            log.debug("Getting change mr#%s from project %s" % (
                number, project.name))
            self._updateChange(change, event)
        except Exception:
            if key in self._change_cache:
                del self._change_cache[key]
            raise
        return change

    def _updateChange(self, change, event):
        log = get_annotated_logger(self.log, event)
        log.info("Updating change from Gitlab %s" % change)
        change.mr = self.getPull(
            change.project.name, change.number, event=event)
        change.ref = "refs/merge-requests/%s/head" % change.number
        change.branch = change.mr['target_branch']
        change.patchset = change.mr['sha']
        # Files changes are not part of the Merge Request data
        # See api/merge_requests.html#get-single-mr-changes
        # this endpoint includes file changes information
        change.files = None
        change.title = change.mr['title']
        change.open = change.mr['state'] == 'opened'
        change.is_merged = change.mr['merged_at'] is not None
        # Can be "can_be_merged"
        change.merge_status = change.mr['merge_status']
        change.message = change.mr['description']
        change.labels = change.mr['labels']
        change.updated_at = int(datetime.strptime(
            change.mr['updated_at'], '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%s'))
        log.info("Updated change from Gitlab %s" % change)

        if self.sched:
            self.sched.onChangeUpdated(change, event)

        return change

    def getPull(self, project_name, number, event=None):
        log = get_annotated_logger(self.log, event)
        mr = self.gl_client.get_mr(project_name, number, zuul_event_id=event)
        log.info('Got MR %s#%s', project_name, number)
        return mr

    def commentMR(self, project_name, number, message, event=None):
        log = get_annotated_logger(self.log, event)
        self.gl_client.comment_mr(
            project_name, number, message, zuul_event_id=event)
        log.info("Commented on MR %s#%s", project_name, number)


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
