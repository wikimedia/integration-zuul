#!/usr/bin/env python
# Copyright 2012 Hewlett-Packard Development Company, L.P.
# Copyright 2013 OpenStack Foundation
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

import argparse
import babel.dates
import datetime
import jwt
import logging
import prettytable
import re
import sys
import time
import textwrap
import requests
import urllib.parse

import zuul.rpcclient
import zuul.cmd
from zuul.lib.config import get_default


# todo This should probably live somewhere else
class ZuulRESTClient(object):
    """Basic client for Zuul's REST API"""
    def __init__(self, url, verify=False, auth_token=None):
        self.url = url
        self.auth_token = auth_token
        self.base_url = urllib.parse.urljoin(self.url, '/api/')
        self.verify = verify
        self.session = requests.Session(
            verify=self.verify,
            headers={'Authorization': 'Bearer %s' % self.auth_token})

    def _check_status(self, req):
        try:
            req.raise_for_status()
        except Exception as e:
            if req.status_code == 401:
                print('Unauthorized - your token might be invalid or expired.')
            elif req.status_code == 403:
                print('Insufficient privileges to perform the action.')
            else:
                print('Unknown error: "%e"' % e)

    def autohold(self, tenant, project, job, change, ref,
                 reason, count, node_hold_expiration):
        if not self.auth_token:
            raise Exception('Auth Token required')
        args = {"reason": reason,
                "count": count,
                "job": job,
                "change": change,
                "ref": ref,
                "node_hold_expiration": node_hold_expiration}
        url = urllib.parse.urljoin(
            self.base_url,
            'tenant/%s/project/%s/autohold' % (tenant, project))
        req = self.session.post(url, json=args)
        self._check_status(req)
        return req.json()

    def autohold_list(self, tenant):
        if not tenant:
            raise Exception('"--tenant" argument required')
        url = urllib.parse.urljoin(
            self.base_url,
            'tenant/%s/autohold' % tenant)
        req = requests.get(url, verify=self.verify)
        self._check_status(req)
        resp = req.json()
        # reformat the answer to match RPC format
        ret = {}
        for d in resp:
            key = ','.join([d['tenant'],
                            d['project'],
                            d['job'],
                            d['ref_filter']])
            ret[key] = (d['count'], d['reason'], d['node_hold_expiration'])

        return ret

    def enqueue(self, tenant, pipeline, project, trigger, change):
        if not self.auth_token:
            raise Exception('Auth Token required')
        args = {"trigger": trigger,
                "change": change,
                "pipeline": pipeline}
        url = urllib.parse.urljoin(
            self.base_url,
            'tenant/%s/project/%s/enqueue' % (tenant, project))
        req = self.session.post(url, json=args)
        self._check_status(req)
        return req.json()

    def enqueue_ref(self, tenant, pipeline, project,
                    trigger, ref, oldrev, newrev):
        if not self.auth_token:
            raise Exception('Auth Token required')
        args = {"trigger": trigger,
                "ref": ref,
                "oldrev": oldrev,
                "newrev": newrev,
                "pipeline": pipeline}
        url = urllib.parse.urljoin(
            self.base_url,
            'tenant/%s/project/%s/enqueue' % (tenant, project))
        req = self.session.post(url, json=args)
        self._check_status(req)
        return req.json()

    def dequeue(self, tenant, pipeline, project, change=None, ref=None):
        if not self.auth_token:
            raise Exception('Auth Token required')
        args = {"pipeline": pipeline}
        if change and not ref:
            args['change'] = change
        elif ref and not change:
            args['ref'] = ref
        else:
            raise Exception('need change OR ref')
        url = urllib.parse.urljoin(
            self.base_url,
            'tenant/%s/project/%s/dequeue' % (tenant, project))
        req = self.session.post(url, json=args)
        self._check_status(req)
        return req.json()

    def promote(self, *args, **kwargs):
        raise NotImplementedError(
            'This action is unsupported by the REST API')

    def get_running_jobs(self, *args, **kwargs):
        raise NotImplementedError(
            'This action is unsupported by the REST API')


class Client(zuul.cmd.ZuulApp):
    app_name = 'zuul'
    app_description = 'Zuul client.'
    log = logging.getLogger("zuul.Client")

    def createParser(self):
        parser = super(Client, self).createParser()
        parser.add_argument('-v', dest='verbose', action='store_true',
                            help='verbose output')
        parser.add_argument('--auth-token', dest='auth_token',
                            required=False,
                            default=None,
                            help='Authentication Token, needed if using the'
                                 'REST API')
        parser.add_argument('--zuul-url', dest='zuul_url',
                            required=False,
                            default=None,
                            help='Zuul API URL, needed if using the '
                                 'REST API without a configuration file')
        parser.add_argument('--insecure', dest='insecure_ssl',
                            required=False,
                            action='store_false',
                            help='Do not verify SSL connection to Zuul, '
                                 'when using the REST API (Defaults to False)')

        subparsers = parser.add_subparsers(title='commands',
                                           description='valid commands',
                                           help='additional help')

        cmd_autohold = subparsers.add_parser(
            'autohold', help='hold nodes for failed job')
        cmd_autohold.add_argument('--tenant', help='tenant name',
                                  required=True)
        cmd_autohold.add_argument('--project', help='project name',
                                  required=True)
        cmd_autohold.add_argument('--job', help='job name',
                                  required=True)
        cmd_autohold.add_argument('--change',
                                  help='specific change to hold nodes for',
                                  required=False, default='')
        cmd_autohold.add_argument('--ref', help='git ref to hold nodes for',
                                  required=False, default='')
        cmd_autohold.add_argument('--reason', help='reason for the hold',
                                  required=True)
        cmd_autohold.add_argument('--count',
                                  help='number of job runs (default: 1)',
                                  required=False, type=int, default=1)
        cmd_autohold.add_argument(
            '--node-hold-expiration',
            help=('how long in seconds should the node set be in HOLD status '
                  '(default: scheduler\'s default_hold_expiration value)'),
            required=False, type=int)
        cmd_autohold.set_defaults(func=self.autohold)

        cmd_autohold_delete = subparsers.add_parser(
            'autohold-delete', help='delete autohold request')
        cmd_autohold_delete.set_defaults(func=self.autohold_delete)
        cmd_autohold_delete.add_argument('id', metavar='REQUEST_ID',
                                         help='the hold request ID')

        cmd_autohold_info = subparsers.add_parser(
            'autohold-info', help='retrieve autohold request detailed info')
        cmd_autohold_info.set_defaults(func=self.autohold_info)
        cmd_autohold_info.add_argument('id', metavar='REQUEST_ID',
                                       help='the hold request ID')

        cmd_autohold_list = subparsers.add_parser(
            'autohold-list', help='list autohold requests')
        cmd_autohold_list.add_argument('--tenant', help='tenant name',
                                       required=False)
        cmd_autohold_list.set_defaults(func=self.autohold_list)

        cmd_enqueue = subparsers.add_parser('enqueue', help='enqueue a change')
        cmd_enqueue.add_argument('--tenant', help='tenant name',
                                 required=True)
        cmd_enqueue.add_argument('--trigger',
                                 help='trigger name (deprecated and ignored. '
                                      'Kept only for backward compatibility)',
                                 required=False, default=None)
        cmd_enqueue.add_argument('--pipeline', help='pipeline name',
                                 required=True)
        cmd_enqueue.add_argument('--project', help='project name',
                                 required=True)
        cmd_enqueue.add_argument('--change', help='change id',
                                 required=True)
        cmd_enqueue.set_defaults(func=self.enqueue)

        cmd_enqueue = subparsers.add_parser(
            'enqueue-ref', help='enqueue a ref',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=textwrap.dedent('''\
            Submit a trigger event

            Directly enqueue a trigger event.  This is usually used
            to manually "replay" a trigger received from an external
            source such as gerrit.'''))
        cmd_enqueue.add_argument('--tenant', help='tenant name',
                                 required=True)
        cmd_enqueue.add_argument('--trigger', help='trigger name',
                                 required=False, default=None)
        cmd_enqueue.add_argument('--pipeline', help='pipeline name',
                                 required=True)
        cmd_enqueue.add_argument('--project', help='project name',
                                 required=True)
        cmd_enqueue.add_argument('--ref', help='ref name',
                                 required=True)
        cmd_enqueue.add_argument(
            '--oldrev', help='old revision', default=None)
        cmd_enqueue.add_argument(
            '--newrev', help='new revision', default=None)
        cmd_enqueue.set_defaults(func=self.enqueue_ref)

        cmd_dequeue = subparsers.add_parser('dequeue',
                                            help='dequeue a buildset by its '
                                                 'change or ref')
        cmd_dequeue.add_argument('--tenant', help='tenant name',
                                 required=True)
        cmd_dequeue.add_argument('--pipeline', help='pipeline name',
                                 required=True)
        cmd_dequeue.add_argument('--project', help='project name',
                                 required=True)
        cmd_dequeue.add_argument('--change', help='change id',
                                 default=None)
        cmd_dequeue.add_argument('--ref', help='ref name',
                                 default=None)
        cmd_dequeue.set_defaults(func=self.dequeue)

        cmd_promote = subparsers.add_parser('promote',
                                            help='promote one or more changes')
        cmd_promote.add_argument('--tenant', help='tenant name',
                                 required=True)
        cmd_promote.add_argument('--pipeline', help='pipeline name',
                                 required=True)
        cmd_promote.add_argument('--changes', help='change ids',
                                 required=True, nargs='+')
        cmd_promote.set_defaults(func=self.promote)

        cmd_show = subparsers.add_parser('show',
                                         help='show current statuses')
        cmd_show.set_defaults(func=self.show_running_jobs)
        show_subparsers = cmd_show.add_subparsers(title='show')
        show_running_jobs = show_subparsers.add_parser(
            'running-jobs',
            help='show the running jobs'
        )
        running_jobs_columns = list(self._show_running_jobs_columns().keys())
        show_running_jobs.add_argument(
            '--columns',
            help="comma separated list of columns to display (or 'ALL')",
            choices=running_jobs_columns.append('ALL'),
            default='name, worker.name, start_time, result'
        )

        # TODO: add filters such as queue, project, changeid etc
        show_running_jobs.set_defaults(func=self.show_running_jobs)

        cmd_conf_check = subparsers.add_parser(
            'tenant-conf-check',
            help='validate the tenant configuration')
        cmd_conf_check.set_defaults(func=self.validate)

        cmd_create_auth_token = subparsers.add_parser(
            'create-auth-token',
            help='create an Authentication Token for the web API',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=textwrap.dedent('''\
            Create an Authentication Token for the administration web API

            Create a bearer token that can be used to access Zuul's
            administration web API. This is typically used to delegate
            privileged actions such as enqueueing and autoholding to
            third parties, scoped to a single tenant.
            At least one authenticator must be configured with a secret
            that can be used to sign the token.'''))
        cmd_create_auth_token.add_argument(
            '--auth-config',
            help=('The authenticator to use. '
                  'Must match an authenticator defined in zuul\'s '
                  'configuration file.'),
            default='zuul_operator',
            required=True)
        cmd_create_auth_token.add_argument(
            '--tenant',
            help='tenant name',
            required=True)
        cmd_create_auth_token.add_argument(
            '--user',
            help=("The user's name. Used for traceability in logs."),
            default=None,
            required=True)
        cmd_create_auth_token.add_argument(
            '--expires-in',
            help=('Token validity duration in seconds '
                  '(default: %i)' % 600),
            type=int,
            default=600,
            required=False)
        cmd_create_auth_token.set_defaults(func=self.create_auth_token)

        return parser

    def parseArguments(self, args=None):
        parser = super(Client, self).parseArguments()
        if not getattr(self.args, 'func', None):
            parser.print_help()
            sys.exit(1)
        if self.args.func == self.enqueue_ref:
            # if oldrev or newrev is set, ensure they're not the same
            if (self.args.oldrev is not None) or \
               (self.args.newrev is not None):
                if self.args.oldrev == self.args.newrev:
                    parser.error(
                        "The old and new revisions must not be the same.")
            # if they're not set, we pad them out to zero
            if self.args.oldrev is None:
                self.args.oldrev = '0000000000000000000000000000000000000000'
            if self.args.newrev is None:
                self.args.newrev = '0000000000000000000000000000000000000000'
        if self.args.func == self.dequeue:
            if self.args.change is None and self.args.ref is None:
                parser.error("Change or ref needed.")
            if self.args.change is not None and self.args.ref is not None:
                parser.error(
                    "The 'change' and 'ref' arguments are mutually exclusive.")

    def setup_logging(self):
        """Client logging does not rely on conf file"""
        if self.args.verbose:
            logging.basicConfig(level=logging.DEBUG)

    def main(self):
        self.parseArguments()
        if not self.args.zuul_url:
            self.readConfig()
        self.setup_logging()

        if self.args.func():
            sys.exit(0)
        else:
            sys.exit(1)

    def get_client(self):
        if self.args.zuul_url:
            self.log.debug('Zuul URL provided as argument, using REST client')
            client = ZuulRESTClient(self.args.zuul_url,
                                    self.args.insecure_ssl,
                                    self.args.auth_token)
            return client
        conf_sections = self.config.sections()
        if 'gearman' in conf_sections:
            self.log.debug('gearman section found in config, using RPC client')
            server = self.config.get('gearman', 'server')
            port = get_default(self.config, 'gearman', 'port', 4730)
            ssl_key = get_default(self.config, 'gearman', 'ssl_key')
            ssl_cert = get_default(self.config, 'gearman', 'ssl_cert')
            ssl_ca = get_default(self.config, 'gearman', 'ssl_ca')
            client = zuul.rpcclient.RPCClient(
                server, port, ssl_key,
                ssl_cert, ssl_ca)
        elif 'webclient' in conf_sections:
            self.log.debug('web section found in config, using REST client')
            server = get_default(self.config, 'webclient', 'url', None)
            verify = get_default(self.config, 'webclient', 'verify_ssl',
                                 self.args.insecure_ssl)
            client = ZuulRESTClient(server, verify,
                                    self.args.auth_token)
        else:
            print('Unable to find a way to connect to Zuul, add a "gearman" '
                  'or "web" section to your configuration file')
            sys.exit(1)
        if server is None:
            print('Missing "server" configuration value')
            sys.exit(1)
        return client

    def autohold(self):
        if self.args.change and self.args.ref:
            print("Change and ref can't be both used for the same request")
            return False
        if "," in self.args.change:
            print("Error: change argument can not contain any ','")
            return False

        node_hold_expiration = self.args.node_hold_expiration
        client = self.get_client()
        r = client.autohold(
            tenant=self.args.tenant,
            project=self.args.project,
            job=self.args.job,
            change=self.args.change,
            ref=self.args.ref,
            reason=self.args.reason,
            count=self.args.count,
            node_hold_expiration=node_hold_expiration)
        return r

    def autohold_delete(self):
        client = self.get_client()
        return client.autohold_delete(self.args.id)

    def autohold_info(self):
        client = self.get_client()
        request = client.autohold_info(self.args.id)

        if not request:
            print("Autohold request not found")
            return True

        print("ID: %s" % request['id'])
        print("Tenant: %s" % request['tenant'])
        print("Project: %s" % request['project'])
        print("Job: %s" % request['job'])
        print("Ref Filter: %s" % request['ref_filter'])
        print("Max Count: %s" % request['max_count'])
        print("Current Count: %s" % request['current_count'])
        print("Node Expiration: %s" % request['node_expiration'])
        print("Request Expiration: %s" % time.ctime(request['expired']))
        print("Reason: %s" % request['reason'])
        print("Held Nodes: %s" % request['nodes'])

        return True

    def autohold_list(self):
        client = self.get_client()
        autohold_requests = client.autohold_list(tenant=self.args.tenant)

        if not autohold_requests:
            print("No autohold requests found")
            return True

        table = prettytable.PrettyTable(
            field_names=[
                'ID', 'Tenant', 'Project', 'Job', 'Ref Filter',
                'Max Count', 'Reason'
            ])

        for request in autohold_requests:
            table.add_row([
                request['id'],
                request['tenant'],
                request['project'],
                request['job'],
                request['ref_filter'],
                request['max_count'],
                request['reason'],
            ])

        print(table)
        return True

    def enqueue(self):
        client = self.get_client()
        r = client.enqueue(
            tenant=self.args.tenant,
            pipeline=self.args.pipeline,
            project=self.args.project,
            trigger=self.args.trigger,
            change=self.args.change)
        return r

    def enqueue_ref(self):
        client = self.get_client()
        r = client.enqueue_ref(
            tenant=self.args.tenant,
            pipeline=self.args.pipeline,
            project=self.args.project,
            trigger=self.args.trigger,
            ref=self.args.ref,
            oldrev=self.args.oldrev,
            newrev=self.args.newrev)
        return r

    def dequeue(self):
        client = self.get_client()
        r = client.dequeue(
            tenant=self.args.tenant,
            pipeline=self.args.pipeline,
            project=self.args.project,
            change=self.args.change,
            ref=self.args.ref)
        return r

    def create_auth_token(self):
        auth_section = ''
        for section_name in self.config.sections():
            if re.match(r'^auth ([\'\"]?)%s(\1)$' % self.args.auth_config,
                        section_name, re.I):
                auth_section = section_name
                break
        if auth_section == '':
            print('"%s" authenticator configuration not found.'
                  % self.args.auth_config)
            sys.exit(1)
        now = time.time()
        token = {'iat': now,
                 'exp': now + self.args.expires_in,
                 'iss': get_default(self.config, auth_section, 'issuer_id'),
                 'aud': get_default(self.config, auth_section, 'client_id'),
                 'sub': self.args.user,
                 'zuul': {'admin': [self.args.tenant, ]},
                }
        driver = get_default(
            self.config, auth_section, 'driver')
        if driver == 'HS256':
            key = get_default(self.config, auth_section, 'secret')
        elif driver == 'RS256':
            private_key = get_default(self.config, auth_section, 'private_key')
            try:
                with open(private_key, 'r') as pk:
                    key = pk.read()
            except Exception as e:
                print('Could not read private key at "%s": %s' % (private_key,
                                                                  e))
                sys.exit(1)
        else:
            print('Unknown or unsupported authenticator driver "%s"' % driver)
            sys.exit(1)
        try:
            auth_token = jwt.encode(token,
                                    key=key,
                                    algorithm=driver).decode('utf-8')
            print("Bearer %s" % auth_token)
            err_code = 0
        except Exception as e:
            print("Error when generating Auth Token")
            print(e)
            err_code = 1
        finally:
            sys.exit(err_code)

    def promote(self):
        client = self.get_client()
        r = client.promote(
            tenant=self.args.tenant,
            pipeline=self.args.pipeline,
            change_ids=self.args.changes)
        return r

    def show_running_jobs(self):
        client = self.get_client()
        running_items = client.get_running_jobs()

        if len(running_items) == 0:
            print("No jobs currently running")
            return True

        all_fields = self._show_running_jobs_columns()
        fields = all_fields.keys()

        table = prettytable.PrettyTable(
            field_names=[all_fields[f]['title'] for f in fields])
        for item in running_items:
            for job in item['jobs']:
                values = []
                for f in fields:
                    v = job
                    for part in f.split('.'):
                        if hasattr(v, 'get'):
                            v = v.get(part, '')
                    if ('transform' in all_fields[f]
                        and callable(all_fields[f]['transform'])):
                        v = all_fields[f]['transform'](v)
                    if 'append' in all_fields[f]:
                        v += all_fields[f]['append']
                    values.append(v)
                table.add_row(values)
        print(table)
        return True

    def _epoch_to_relative_time(self, epoch):
        if epoch:
            delta = datetime.timedelta(seconds=(time.time() - int(epoch)))
            return babel.dates.format_timedelta(delta, locale='en_US')
        else:
            return "Unknown"

    def _boolean_to_yes_no(self, value):
        return 'Yes' if value else 'No'

    def _boolean_to_pass_fail(self, value):
        return 'Pass' if value else 'Fail'

    def _format_list(self, l):
        return ', '.join(l) if isinstance(l, list) else ''

    def _show_running_jobs_columns(self):
        """A helper function to get the list of available columns for
        `zuul show running-jobs`. Also describes how to convert particular
        values (for example epoch to time string)"""

        return {
            'name': {
                'title': 'Job Name',
            },
            'elapsed_time': {
                'title': 'Elapsed Time',
                'transform': self._epoch_to_relative_time
            },
            'remaining_time': {
                'title': 'Remaining Time',
                'transform': self._epoch_to_relative_time
            },
            'url': {
                'title': 'URL'
            },
            'result': {
                'title': 'Result'
            },
            'voting': {
                'title': 'Voting',
                'transform': self._boolean_to_yes_no
            },
            'uuid': {
                'title': 'UUID'
            },
            'execute_time': {
                'title': 'Execute Time',
                'transform': self._epoch_to_relative_time,
                'append': ' ago'
            },
            'start_time': {
                'title': 'Start Time',
                'transform': self._epoch_to_relative_time,
                'append': ' ago'
            },
            'end_time': {
                'title': 'End Time',
                'transform': self._epoch_to_relative_time,
                'append': ' ago'
            },
            'estimated_time': {
                'title': 'Estimated Time',
                'transform': self._epoch_to_relative_time,
                'append': ' to go'
            },
            'pipeline': {
                'title': 'Pipeline'
            },
            'canceled': {
                'title': 'Canceled',
                'transform': self._boolean_to_yes_no
            },
            'retry': {
                'title': 'Retry'
            },
            'number': {
                'title': 'Number'
            },
            'node_labels': {
                'title': 'Node Labels'
            },
            'node_name': {
                'title': 'Node Name'
            },
            'worker.name': {
                'title': 'Worker'
            },
            'worker.hostname': {
                'title': 'Worker Hostname'
            },
        }

    def validate(self):
        from zuul import scheduler
        from zuul import configloader
        sched = scheduler.Scheduler(self.config, testonly=True)
        self.configure_connections(source_only=True)
        sched.registerConnections(self.connections, load=False)
        loader = configloader.ConfigLoader(
            sched.connections, sched, None, None)
        tenant_config, script = sched._checkTenantSourceConf(self.config)
        unparsed_abide = loader.readConfig(tenant_config, from_script=script)
        try:
            for conf_tenant in unparsed_abide.tenants:
                loader.tenant_parser.getSchema()(conf_tenant)
            print("Tenants config validated with success")
            err_code = 0
        except Exception as e:
            print("Error when validating tenants config")
            print(e)
            err_code = 1
        finally:
            sys.exit(err_code)


def main():
    Client().main()


if __name__ == "__main__":
    main()
