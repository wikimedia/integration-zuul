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

import abc
from collections import OrderedDict
import copy
import logging
import os
import re2
import struct
import time
from uuid import uuid4
import urllib.parse
import textwrap
import types
import itertools

from zuul import change_matcher
from zuul.lib.config import get_default

MERGER_MERGE = 1          # "git merge"
MERGER_MERGE_RESOLVE = 2  # "git merge -s resolve"
MERGER_CHERRY_PICK = 3    # "git cherry-pick"

MERGER_MAP = {
    'merge': MERGER_MERGE,
    'merge-resolve': MERGER_MERGE_RESOLVE,
    'cherry-pick': MERGER_CHERRY_PICK,
}

PRECEDENCE_NORMAL = 0
PRECEDENCE_LOW = 1
PRECEDENCE_HIGH = 2

PRECEDENCE_MAP = {
    None: PRECEDENCE_NORMAL,
    'low': PRECEDENCE_LOW,
    'normal': PRECEDENCE_NORMAL,
    'high': PRECEDENCE_HIGH,
}

PRIORITY_MAP = {
    PRECEDENCE_NORMAL: 200,
    PRECEDENCE_LOW: 300,
    PRECEDENCE_HIGH: 100,
}

# Request states
STATE_REQUESTED = 'requested'
STATE_FULFILLED = 'fulfilled'
STATE_FAILED = 'failed'
REQUEST_STATES = set([STATE_REQUESTED,
                      STATE_FULFILLED,
                      STATE_FAILED])

# Node states
STATE_BUILDING = 'building'
STATE_TESTING = 'testing'
STATE_READY = 'ready'
STATE_IN_USE = 'in-use'
STATE_USED = 'used'
STATE_HOLD = 'hold'
STATE_DELETING = 'deleting'
NODE_STATES = set([STATE_BUILDING,
                   STATE_TESTING,
                   STATE_READY,
                   STATE_IN_USE,
                   STATE_USED,
                   STATE_HOLD,
                   STATE_DELETING])


class ConfigurationErrorKey(object):
    """A class which attempts to uniquely identify configuration errors
    based on their file location.  It's not perfect, but it's usually
    sufficient to determine whether we should show an error to a user.
    """

    def __init__(self, context, mark, error_text):
        self.context = context
        self.mark = mark
        self.error_text = error_text
        elements = []
        if context:
            elements.extend([
                context.project.canonical_name,
                context.branch,
                context.path,
            ])
        else:
            elements.extend([None, None, None])
        if mark:
            elements.extend([
                mark.line,
                mark.snippet,
            ])
        else:
            elements.extend([None, None])
        elements.append(error_text)
        self._hash = hash('|'.join([str(x) for x in elements]))

    def __hash__(self):
        return self._hash

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, ConfigurationErrorKey):
            return False
        return (self.context == other.context and
                self.mark.line == other.mark.line and
                self.mark.snippet == other.mark.snippet and
                self.error_text == other.error_text)


class ConfigurationError(object):

    """A configuration error"""
    def __init__(self, context, mark, error, short_error=None):
        self.error = str(error)
        self.short_error = short_error
        self.key = ConfigurationErrorKey(context, mark, self.error)


class LoadingErrors(object):
    """A configuration errors accumalator attached to a layout object
    """
    def __init__(self):
        self.errors = []
        self.error_keys = set()

    def addError(self, context, mark, error, short_error=None):
        e = ConfigurationError(context, mark, error, short_error)
        self.errors.append(e)
        self.error_keys.add(e.key)

    def __getitem__(self, index):
        return self.errors[index]

    def __len__(self):
        return len(self.errors)


class NoMatchingParentError(Exception):
    """A job referenced a parent, but that parent had no variants which
    matched the current change."""
    pass


class TemplateNotFoundError(Exception):
    """A project referenced a template that does not exist."""
    pass


class Attributes(object):
    """A class to hold attributes for string formatting."""

    def __init__(self, **kw):
        setattr(self, '__dict__', kw)


class Freezable(object):
    """A mix-in class so that an object can be made immutable"""

    def __init__(self):
        super(Freezable, self).__setattr__('_frozen', False)

    def freeze(self):
        """Make this object immutable"""
        def _freezelist(l):
            for i, v in enumerate(l):
                if isinstance(v, Freezable):
                    if not v._frozen:
                        v.freeze()
                elif isinstance(v, dict):
                    l[i] = _freezedict(v)
                elif isinstance(v, list):
                    l[i] = _freezelist(v)
            return tuple(l)

        def _freezedict(d):
            for k, v in list(d.items()):
                if isinstance(v, Freezable):
                    if not v._frozen:
                        v.freeze()
                elif isinstance(v, dict):
                    d[k] = _freezedict(v)
                elif isinstance(v, list):
                    d[k] = _freezelist(v)
            return types.MappingProxyType(d)

        _freezedict(self.__dict__)
        # Ignore return value from freezedict because __dict__ can't
        # be a mappingproxy.
        self._frozen = True

    def __setattr__(self, name, value):
        if self._frozen:
            raise Exception("Unable to modify frozen object %s" %
                            (repr(self),))
        super(Freezable, self).__setattr__(name, value)


class ConfigObject(Freezable):
    def __init__(self):
        super().__init__()
        self.source_context = None
        self.start_mark = None


class Pipeline(object):
    """A configuration that ties together triggers, reporters and managers

    Trigger
        A description of which events should be processed

    Manager
        Responsible for enqueing and dequeing Changes

    Reporter
        Communicates success and failure results somewhere
    """
    def __init__(self, name, tenant):
        self.name = name
        # Note that pipelines are not portable across tenants (new
        # pipeline objects must be made when a tenant is
        # reconfigured).  A pipeline requires a tenant in order to
        # reach the currently active layout for that tenant.
        self.tenant = tenant
        self.source_context = None
        self.description = None
        self.failure_message = None
        self.merge_failure_message = None
        self.success_message = None
        self.footer_message = None
        self.start_message = None
        self.post_review = False
        self.dequeue_on_new_patchset = True
        self.ignore_dependencies = False
        self.manager = None
        self.queues = []
        self.relative_priority_queues = {}
        self.precedence = PRECEDENCE_NORMAL
        self.triggers = []
        self.start_actions = []
        self.success_actions = []
        self.failure_actions = []
        self.merge_failure_actions = []
        self.disabled_actions = []
        self.disable_at = None
        self._consecutive_failures = 0
        self._disabled = False
        self.window = None
        self.window_floor = None
        self.window_increase_type = None
        self.window_increase_factor = None
        self.window_decrease_type = None
        self.window_decrease_factor = None

    @property
    def actions(self):
        return (
            self.start_actions +
            self.success_actions +
            self.failure_actions +
            self.merge_failure_actions +
            self.disabled_actions
        )

    def __repr__(self):
        return '<Pipeline %s>' % self.name

    def getSafeAttributes(self):
        return Attributes(name=self.name)

    def setManager(self, manager):
        self.manager = manager

    def addQueue(self, queue):
        self.queues.append(queue)

    def getQueue(self, project):
        for queue in self.queues:
            if project in queue.projects:
                return queue
        return None

    def getRelativePriorityQueue(self, project):
        for queue in self.relative_priority_queues.values():
            if project in queue:
                return queue
        return [project]

    def removeQueue(self, queue):
        if queue in self.queues:
            self.queues.remove(queue)

    def getChangesInQueue(self):
        changes = []
        for shared_queue in self.queues:
            changes.extend([x.change for x in shared_queue.queue])
        return changes

    def getAllItems(self):
        items = []
        for shared_queue in self.queues:
            items.extend(shared_queue.queue)
        return items

    def formatStatusJSON(self, websocket_url=None):
        j_pipeline = dict(name=self.name,
                          description=self.description)
        j_queues = []
        j_pipeline['change_queues'] = j_queues
        for queue in self.queues:
            j_queue = dict(name=queue.name)
            j_queues.append(j_queue)
            j_queue['heads'] = []
            j_queue['window'] = queue.window

            j_changes = []
            for e in queue.queue:
                if not e.item_ahead:
                    if j_changes:
                        j_queue['heads'].append(j_changes)
                    j_changes = []
                j_changes.append(e.formatJSON(websocket_url))
                if (len(j_changes) > 1 and
                        (j_changes[-2]['remaining_time'] is not None) and
                        (j_changes[-1]['remaining_time'] is not None)):
                    j_changes[-1]['remaining_time'] = max(
                        j_changes[-2]['remaining_time'],
                        j_changes[-1]['remaining_time'])
            if j_changes:
                j_queue['heads'].append(j_changes)
        return j_pipeline


class ChangeQueue(object):
    """A ChangeQueue contains Changes to be processed for related projects.

    A Pipeline with a DependentPipelineManager has multiple parallel
    ChangeQueues shared by different projects. For instance, there may a
    ChangeQueue shared by interrelated projects foo and bar, and a second queue
    for independent project baz.

    A Pipeline with an IndependentPipelineManager puts every Change into its
    own ChangeQueue.

    The ChangeQueue Window is inspired by TCP windows and controlls how many
    Changes in a given ChangeQueue will be considered active and ready to
    be processed. If a Change succeeds, the Window is increased by
    `window_increase_factor`. If a Change fails, the Window is decreased by
    `window_decrease_factor`.

    A ChangeQueue may be a dynamically created queue, which may be removed
    from a DependentPipelineManager once empty.
    """
    def __init__(self, pipeline, window=0, window_floor=1,
                 window_increase_type='linear', window_increase_factor=1,
                 window_decrease_type='exponential', window_decrease_factor=2,
                 name=None, dynamic=False):
        self.pipeline = pipeline
        if name:
            self.name = name
        else:
            self.name = ''
        self.projects = []
        self._jobs = set()
        self.queue = []
        self.window = window
        self.window_floor = window_floor
        self.window_increase_type = window_increase_type
        self.window_increase_factor = window_increase_factor
        self.window_decrease_type = window_decrease_type
        self.window_decrease_factor = window_decrease_factor
        self.dynamic = dynamic

    def __repr__(self):
        return '<ChangeQueue %s: %s>' % (self.pipeline.name, self.name)

    def getJobs(self):
        return self._jobs

    def addProject(self, project):
        if project not in self.projects:
            self.projects.append(project)

            if not self.name:
                self.name = project.name

    def enqueueChange(self, change):
        item = QueueItem(self, change)
        self.enqueueItem(item)
        item.enqueue_time = time.time()
        return item

    def enqueueItem(self, item):
        item.pipeline = self.pipeline
        item.queue = self
        if self.queue:
            item.item_ahead = self.queue[-1]
            item.item_ahead.items_behind.append(item)
        self.queue.append(item)

    def dequeueItem(self, item):
        if item in self.queue:
            self.queue.remove(item)
        if item.item_ahead:
            item.item_ahead.items_behind.remove(item)
        for item_behind in item.items_behind:
            if item.item_ahead:
                item.item_ahead.items_behind.append(item_behind)
            item_behind.item_ahead = item.item_ahead
        item.item_ahead = None
        item.items_behind = []
        item.dequeue_time = time.time()

    def moveItem(self, item, item_ahead):
        if item.item_ahead == item_ahead:
            return False
        # Remove from current location
        if item.item_ahead:
            item.item_ahead.items_behind.remove(item)
        for item_behind in item.items_behind:
            if item.item_ahead:
                item.item_ahead.items_behind.append(item_behind)
            item_behind.item_ahead = item.item_ahead
        # Add to new location
        item.item_ahead = item_ahead
        item.items_behind = []
        if item.item_ahead:
            item.item_ahead.items_behind.append(item)
        return True

    def isActionable(self, item):
        if self.window:
            return item in self.queue[:self.window]
        else:
            return True

    def increaseWindowSize(self):
        if self.window:
            if self.window_increase_type == 'linear':
                self.window += self.window_increase_factor
            elif self.window_increase_type == 'exponential':
                self.window *= self.window_increase_factor

    def decreaseWindowSize(self):
        if self.window:
            if self.window_decrease_type == 'linear':
                self.window = max(
                    self.window_floor,
                    self.window - self.window_decrease_factor)
            elif self.window_decrease_type == 'exponential':
                self.window = max(
                    self.window_floor,
                    int(self.window / self.window_decrease_factor))


class Project(object):
    """A Project represents a git repository such as openstack/nova."""

    # NOTE: Projects should only be instantiated via a Source object
    # so that they are associated with and cached by their Connection.
    # This makes a Project instance a unique identifier for a given
    # project from a given source.

    def __init__(self, name, source, foreign=False):
        self.name = name
        self.source = source
        self.connection_name = source.connection.connection_name
        self.canonical_hostname = source.canonical_hostname
        self.canonical_name = source.canonical_hostname + '/' + name
        # foreign projects are those referenced in dependencies
        # of layout projects, this should matter
        # when deciding whether to enqueue their changes
        # TODOv3 (jeblair): re-add support for foreign projects if needed
        self.foreign = foreign

    def __str__(self):
        return self.name

    def __repr__(self):
        return '<Project %s>' % (self.name)

    def getSafeAttributes(self):
        return Attributes(name=self.name)

    def toDict(self):
        d = {}
        d['name'] = self.name
        d['connection_name'] = self.connection_name
        d['canonical_name'] = self.canonical_name
        return d


class Node(ConfigObject):
    """A single node for use by a job.

    This may represent a request for a node, or an actual node
    provided by Nodepool.
    """

    def __init__(self, name, label):
        super(Node, self).__init__()
        self.name = name
        self.label = label
        self.id = None
        self.lock = None
        self.hold_job = None
        self.comment = None
        # Attributes from Nodepool
        self._state = 'unknown'
        self.state_time = time.time()
        self.host_id = None
        self.interface_ip = None
        self.public_ipv4 = None
        self.private_ipv4 = None
        self.public_ipv6 = None
        self.connection_port = 22
        self.connection_type = None
        self._keys = []
        self.az = None
        self.provider = None
        self.region = None
        self.username = None
        self.hold_expiration = None

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value not in NODE_STATES:
            raise TypeError("'%s' is not a valid state" % value)
        self._state = value
        self.state_time = time.time()

    def __repr__(self):
        return '<Node %s %s:%s>' % (self.id, self.name, self.label)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, Node):
            return False
        return (self.name == other.name and
                self.label == other.label and
                self.id == other.id)

    def toDict(self, internal_attributes=False):
        d = {}
        d['state'] = self.state
        d['hold_job'] = self.hold_job
        d['comment'] = self.comment
        for k in self._keys:
            d[k] = getattr(self, k)
        if internal_attributes:
            # These attributes are only useful for the rpc serialization
            d['name'] = self.name[0]
            d['aliases'] = self.name[1:]
            d['label'] = self.label
        return d

    def updateFromDict(self, data):
        self._state = data['state']
        keys = []
        for k, v in data.items():
            if k == 'state':
                continue
            keys.append(k)
            setattr(self, k, v)
        self._keys = keys


class Group(ConfigObject):
    """A logical group of nodes for use by a job.

    A Group is a named set of node names that will be provided to
    jobs in the inventory to describe logical units where some subset of tasks
    run.
    """

    def __init__(self, name, nodes):
        super(Group, self).__init__()
        self.name = name
        self.nodes = nodes

    def __repr__(self):
        return '<Group %s %s>' % (self.name, str(self.nodes))

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, Group):
            return False
        return (self.name == other.name and
                self.nodes == other.nodes)

    def toDict(self):
        return {
            'name': self.name,
            'nodes': self.nodes
        }


class NodeSet(ConfigObject):
    """A set of nodes.

    In configuration, NodeSets are attributes of Jobs indicating that
    a Job requires nodes matching this description.

    They may appear as top-level configuration objects and be named,
    or they may appears anonymously in in-line job definitions.
    """

    def __init__(self, name=None):
        super(NodeSet, self).__init__()
        self.name = name or ''
        self.nodes = OrderedDict()
        self.groups = OrderedDict()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, NodeSet):
            return False
        return (self.name == other.name and
                self.nodes == other.nodes)

    def toDict(self):
        d = {}
        d['name'] = self.name
        d['nodes'] = []
        for node in self.nodes.values():
            d['nodes'].append(node.toDict(internal_attributes=True))
        d['groups'] = []
        for group in self.groups.values():
            d['groups'].append(group.toDict())
        return d

    def copy(self):
        n = NodeSet(self.name)
        for name, node in self.nodes.items():
            n.addNode(Node(node.name, node.label))
        for name, group in self.groups.items():
            n.addGroup(Group(group.name, group.nodes[:]))
        return n

    def addNode(self, node):
        for name in node.name:
            if name in self.nodes:
                raise Exception("Duplicate node in %s" % (self,))
        self.nodes[tuple(node.name)] = node

    def getNodes(self):
        return list(self.nodes.values())

    def addGroup(self, group):
        if group.name in self.groups:
            raise Exception("Duplicate group in %s" % (self,))
        self.groups[group.name] = group

    def getGroups(self):
        return list(self.groups.values())

    def __repr__(self):
        if self.name:
            name = self.name + ' '
        else:
            name = ''
        return '<NodeSet %s%s>' % (name, list(self.nodes.values()))

    def __len__(self):
        return len(self.nodes)


class NodeRequest(object):
    """A request for a set of nodes."""

    def __init__(self, requestor, build_set, job, nodeset, relative_priority):
        self.requestor = requestor
        self.build_set = build_set
        self.job = job
        self.nodeset = nodeset
        self._state = STATE_REQUESTED
        self.requested_time = time.time()
        self.state_time = time.time()
        self.created_time = None
        self.stat = None
        self.uid = uuid4().hex
        self.relative_priority = relative_priority
        self.id = None
        self._zk_data = {}  # Data that we read back from ZK
        # Zuul internal flags (not stored in ZK so they are not
        # overwritten).
        self.failed = False
        self.canceled = False

    @property
    def priority(self):
        if self.build_set:
            precedence = self.build_set.item.pipeline.precedence
        else:
            precedence = PRECEDENCE_NORMAL
        return PRIORITY_MAP[precedence]

    @property
    def fulfilled(self):
        return (self._state == STATE_FULFILLED) and not self.failed

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value not in REQUEST_STATES:
            raise TypeError("'%s' is not a valid state" % value)
        self._state = value
        self.state_time = time.time()

    def __repr__(self):
        return '<NodeRequest %s %s>' % (self.id, self.nodeset)

    def toDict(self):
        # Start with any previously read data
        d = self._zk_data.copy()
        nodes = [n.label for n in self.nodeset.getNodes()]
        # These are immutable once set
        d.setdefault('node_types', nodes)
        d.setdefault('requestor', self.requestor)
        d.setdefault('created_time', self.created_time)
        # We might change these
        d['state'] = self.state
        d['state_time'] = self.state_time
        d['relative_priority'] = self.relative_priority
        return d

    def updateFromDict(self, data):
        self._zk_data = data
        self._state = data['state']
        self.state_time = data['state_time']
        self.relative_priority = data.get('relative_priority', 0)


class Secret(ConfigObject):
    """A collection of private data.

    In configuration, Secrets are collections of private data in
    key-value pair format.  They are defined as top-level
    configuration objects and then referenced by Jobs.

    """

    def __init__(self, name, source_context):
        super(Secret, self).__init__()
        self.name = name
        self.source_context = source_context
        # The secret data may or may not be encrypted.  This attribute
        # is named 'secret_data' to make it easy to search for and
        # spot where it is directly used.
        self.secret_data = {}

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, Secret):
            return False
        return (self.name == other.name and
                self.source_context == other.source_context and
                self.secret_data == other.secret_data)

    def areDataEqual(self, other):
        return (self.secret_data == other.secret_data)

    def __repr__(self):
        return '<Secret %s>' % (self.name,)

    def _decrypt(self, private_key, secret_data):
        # recursive function to decrypt data
        if hasattr(secret_data, 'decrypt'):
            return secret_data.decrypt(private_key)

        if isinstance(secret_data, (dict, types.MappingProxyType)):
            decrypted_secret_data = {}
            for k, v in secret_data.items():
                decrypted_secret_data[k] = self._decrypt(private_key, v)
            return decrypted_secret_data

        if isinstance(secret_data, (list, tuple)):
            decrypted_secret_data = []
            for v in secret_data:
                decrypted_secret_data.append(self._decrypt(private_key, v))
            return decrypted_secret_data

        return secret_data

    def decrypt(self, private_key):
        """Return a copy of this secret with any encrypted data decrypted.
        Note that the original remains encrypted."""

        r = Secret(self.name, self.source_context)
        r.secret_data = self._decrypt(private_key, self.secret_data)
        return r


class SecretUse(ConfigObject):
    """A use of a secret in a Job"""

    def __init__(self, name, alias):
        super(SecretUse, self).__init__()
        self.name = name
        self.alias = alias
        self.pass_to_parent = False


class SourceContext(ConfigObject):
    """A reference to the branch of a project in configuration.

    Jobs and playbooks reference this to keep track of where they
    originate."""

    def __init__(self, project, branch, path, trusted):
        super(SourceContext, self).__init__()
        self.project = project
        self.branch = branch
        self.path = path
        self.trusted = trusted
        self.implied_branch_matchers = None
        self.implied_branches = None

    def __str__(self):
        return '%s/%s@%s' % (self.project, self.path, self.branch)

    def __repr__(self):
        return '<SourceContext %s trusted:%s>' % (str(self),
                                                  self.trusted)

    def __deepcopy__(self, memo):
        return self.copy()

    def copy(self):
        return self.__class__(self.project, self.branch, self.path,
                              self.trusted)

    def isSameProject(self, other):
        if not isinstance(other, SourceContext):
            return False
        return (self.project == other.project and
                self.trusted == other.trusted)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, SourceContext):
            return False
        return (self.project == other.project and
                self.branch == other.branch and
                self.path == other.path and
                self.trusted == other.trusted)

    def toDict(self):
        return dict(
            project=self.project.name,
            branch=self.branch,
            path=self.path,
        )


class PlaybookContext(ConfigObject):
    """A reference to a playbook in the context of a project.

    Jobs refer to objects of this class for their main, pre, and post
    playbooks so that we can keep track of which repos and security
    contexts are needed in order to run them.

    We also keep a list of roles so that playbooks only run with the
    roles which were defined at the point the playbook was defined.

    """

    def __init__(self, source_context, path, roles, secrets):
        super(PlaybookContext, self).__init__()
        self.source_context = source_context
        self.path = path
        self.roles = roles
        self.secrets = secrets
        self.decrypted_secrets = ()

    def __repr__(self):
        return '<PlaybookContext %s %s>' % (self.source_context,
                                            self.path)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, PlaybookContext):
            return False
        return (self.source_context == other.source_context and
                self.path == other.path and
                self.roles == other.roles and
                self.secrets == other.secrets)

    def copy(self):
        r = PlaybookContext(self.source_context,
                            self.path,
                            self.roles,
                            self.secrets)
        return r

    def validateReferences(self, layout):
        # Verify that references to other objects in the layout are
        # valid.
        for secret_use in self.secrets:
            secret = layout.secrets.get(secret_use.name)
            if secret is None:
                raise Exception(
                    'The secret "{name}" was not found.'.format(
                        name=secret_use.name))
            if secret_use.alias == 'zuul' or secret_use.alias == 'nodepool':
                raise Exception('Secrets named "zuul" or "nodepool" '
                                'are not allowed.')
            if not secret.source_context.isSameProject(self.source_context):
                raise Exception(
                    "Unable to use secret {name}.  Secrets must be "
                    "defined in the same project in which they "
                    "are used".format(
                        name=secret_use.name))
            # Decrypt a copy of the secret to verify it can be done
            secret.decrypt(self.source_context.project.private_secrets_key)

    def freezeSecrets(self, layout):
        secrets = []
        for secret_use in self.secrets:
            secret = layout.secrets.get(secret_use.name)
            decrypted_secret = secret.decrypt(
                self.source_context.project.private_secrets_key)
            decrypted_secret.name = secret_use.alias
            secrets.append(decrypted_secret)
        self.decrypted_secrets = tuple(secrets)

    def addSecrets(self, decrypted_secrets):
        current_names = set([s.name for s in self.decrypted_secrets])
        new_secrets = [s for s in decrypted_secrets
                       if s.name not in current_names]
        self.decrypted_secrets = self.decrypted_secrets + tuple(new_secrets)

    def toDict(self):
        # Render to a dict to use in passing json to the executor
        secrets = {}
        for secret in self.decrypted_secrets:
            secrets[secret.name] = secret.secret_data
        return dict(
            connection=self.source_context.project.connection_name,
            project=self.source_context.project.name,
            branch=self.source_context.branch,
            trusted=self.source_context.trusted,
            roles=[r.toDict() for r in self.roles],
            secrets=secrets,
            path=self.path)


class Role(ConfigObject, metaclass=abc.ABCMeta):
    """A reference to an ansible role."""

    def __init__(self, target_name):
        super(Role, self).__init__()
        self.target_name = target_name

    @abc.abstractmethod
    def __repr__(self):
        pass

    def __ne__(self, other):
        return not self.__eq__(other)

    @abc.abstractmethod
    def __eq__(self, other):
        if not isinstance(other, Role):
            return False
        return (self.target_name == other.target_name)

    @abc.abstractmethod
    def toDict(self):
        # Render to a dict to use in passing json to the executor
        return dict(target_name=self.target_name)


class ZuulRole(Role):
    """A reference to an ansible role in a Zuul project."""

    def __init__(self, target_name, project_canonical_name, implicit=False):
        super(ZuulRole, self).__init__(target_name)
        self.project_canonical_name = project_canonical_name
        self.implicit = implicit

    def __repr__(self):
        return '<ZuulRole %s %s>' % (self.project_canonical_name,
                                     self.target_name)

    __hash__ = object.__hash__

    def __eq__(self, other):
        if not isinstance(other, ZuulRole):
            return False
        # Implicit is not consulted for equality so that we can handle
        # implicit to explicit conversions.
        return (super(ZuulRole, self).__eq__(other) and
                self.project_canonical_name == other.project_canonical_name)

    def toDict(self):
        # Render to a dict to use in passing json to the executor
        d = super(ZuulRole, self).toDict()
        d['type'] = 'zuul'
        d['project_canonical_name'] = self.project_canonical_name
        d['implicit'] = self.implicit
        return d


class Job(ConfigObject):

    """A Job represents the defintion of actions to perform.

    A Job is an abstract configuration concept.  It describes what,
    where, and under what circumstances something should be run
    (contrast this with Build which is a concrete single execution of
    a Job).

    NB: Do not modify attributes of this class, set them directly
    (e.g., "job.run = ..." rather than "job.run.append(...)").
    """

    BASE_JOB_MARKER = object()

    def __init__(self, name):
        super(Job, self).__init__()
        # These attributes may override even the final form of a job
        # in the context of a project-pipeline.  They can not affect
        # the execution of the job, but only whether the job is run
        # and how it is reported.
        self.context_attributes = dict(
            voting=True,
            hold_following_changes=False,
            failure_message=None,
            success_message=None,
            failure_url=None,
            success_url=None,
            branch_matcher=None,
            file_matcher=None,
            irrelevant_file_matcher=None,  # skip-if
            tags=frozenset(),
            dependencies=frozenset(),
        )

        # These attributes affect how the job is actually run and more
        # care must be taken when overriding them.  If a job is
        # declared "final", these may not be overridden in a
        # project-pipeline.
        self.execution_attributes = dict(
            parent=None,
            timeout=None,
            post_timeout=None,
            variables={},
            extra_variables={},
            host_variables={},
            group_variables={},
            nodeset=NodeSet(),
            workspace=None,
            pre_run=(),
            post_run=(),
            run=(),
            semaphore=None,
            attempts=3,
            final=False,
            abstract=False,
            protected=None,
            roles=(),
            required_projects={},
            allowed_projects=None,
            override_branch=None,
            override_checkout=None,
            post_review=None,
        )

        # These are generally internal attributes which are not
        # accessible via configuration.
        self.other_attributes = dict(
            name=None,
            source_context=None,
            start_mark=None,
            inheritance_path=(),
            parent_data=None,
            description=None,
            variant_description=None,
            protected_origin=None,
            _branches=(),
            _implied_branch=None,
            _files=(),
            _irrelevant_files=(),
            secrets=(),  # secrets aren't inheritable
        )

        self.inheritable_attributes = {}
        self.inheritable_attributes.update(self.context_attributes)
        self.inheritable_attributes.update(self.execution_attributes)
        self.attributes = {}
        self.attributes.update(self.inheritable_attributes)
        self.attributes.update(self.other_attributes)

        self.name = name

    def toDict(self, tenant):
        '''
        Convert a Job object's attributes to a dictionary.
        '''
        d = {}
        d['name'] = self.name
        d['branches'] = self._branches
        d['files'] = self._files
        d['irrelevant_files'] = self._irrelevant_files
        d['variant_description'] = self.variant_description
        d['implied_branch'] = self._implied_branch
        d['source_context'] = self.source_context.toDict()
        d['description'] = self.description
        d['required_projects'] = []
        for project in self.required_projects.values():
            d['required_projects'].append(project.toDict())
        if self.semaphore:
            # For now just leave the semaphore name here until we really need
            # more information in zuul-web about this
            d['semaphore'] = self.semaphore.name
        else:
            d['semaphore'] = None
        d['variables'] = self.variables
        d['final'] = self.final
        d['abstract'] = self.abstract
        d['protected'] = self.protected
        d['voting'] = self.voting
        d['timeout'] = self.timeout
        d['attempts'] = self.attempts
        d['roles'] = list(map(lambda x: x.toDict(), self.roles))
        d['post_review'] = self.post_review
        if self.isBase():
            d['parent'] = None
        elif self.parent:
            d['parent'] = self.parent
        else:
            d['parent'] = tenant.default_base_job
        d['dependencies'] = []
        for dependency in self.dependencies:
            d['dependencies'].append(dependency)
        if isinstance(self.nodeset, str):
            ns = tenant.layout.nodesets.get(self.nodeset)
        else:
            ns = self.nodeset
        if ns:
            d['nodeset'] = ns.toDict()
        return d

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        # Compare the name and all inheritable attributes to determine
        # whether two jobs with the same name are identically
        # configured.  Useful upon reconfiguration.
        if not isinstance(other, Job):
            return False
        if self.name != other.name:
            return False
        for k, v in self.attributes.items():
            if getattr(self, k) != getattr(other, k):
                return False
        return True

    __hash__ = object.__hash__

    def __str__(self):
        return self.name

    def __repr__(self):
        ln = 0
        if self.start_mark:
            ln = self.start_mark.line + 1
        return '<Job %s branches: %s source: %s#%s>' % (
            self.name,
            self.branch_matcher,
            self.source_context,
            ln)

    def __getattr__(self, name):
        v = self.__dict__.get(name)
        if v is None:
            return self.attributes[name]
        return v

    def _get(self, name):
        return self.__dict__.get(name)

    def getSafeAttributes(self):
        return Attributes(name=self.name)

    def isBase(self):
        return self.parent is self.BASE_JOB_MARKER

    def setBase(self, layout):
        self.inheritance_path = self.inheritance_path + (repr(self),)
        if self._get('run') is not None:
            self.run = self.freezePlaybooks(self.run, layout)
        if self._get('pre_run') is not None:
            self.pre_run = self.freezePlaybooks(self.pre_run, layout)
        if self._get('post_run') is not None:
            self.post_run = self.freezePlaybooks(self.post_run, layout)

    def getNodeSet(self, layout):
        if isinstance(self.nodeset, str):
            # This references an existing named nodeset in the layout.
            ns = layout.nodesets.get(self.nodeset)
            if ns is None:
                raise Exception(
                    'The nodeset "{nodeset}" was not found.'.format(
                        nodeset=self.nodeset))
            return ns
        return self.nodeset

    def validateReferences(self, layout):
        # Verify that references to other objects in the layout are
        # valid.
        if not self.isBase() and self.parent:
            layout.getJob(self.parent)

        ns = self.getNodeSet(layout)
        if layout.tenant.max_nodes_per_job != -1 and \
           len(ns) > layout.tenant.max_nodes_per_job:
            raise Exception(
                'The job "{job}" exceeds tenant '
                'max-nodes-per-job {maxnodes}.'.format(
                    job=self.name,
                    maxnodes=layout.tenant.max_nodes_per_job))

        for pb in self.pre_run + self.run + self.post_run:
            pb.validateReferences(layout)

    def addRoles(self, roles):
        newroles = []
        # Start with a copy of the existing roles, but if any of them
        # are implicit roles which are identified as explicit in the
        # new roles list, replace them with the explicit version.
        changed = False
        for existing_role in self.roles:
            if existing_role in roles:
                new_role = roles[roles.index(existing_role)]
            else:
                new_role = None
            if (new_role and
                isinstance(new_role, ZuulRole) and
                isinstance(existing_role, ZuulRole) and
                existing_role.implicit and not new_role.implicit):
                newroles.append(new_role)
                changed = True
            else:
                newroles.append(existing_role)
        # Now add the new roles.
        for role in reversed(roles):
            if role not in newroles:
                newroles.insert(0, role)
                changed = True
        if changed:
            self.roles = tuple(newroles)

    def setBranchMatcher(self, branches):
        # Set the branch matcher to match any of the supplied branches
        self._branches = branches
        matchers = []
        for branch in branches:
            matchers.append(change_matcher.BranchMatcher(branch))
        self.branch_matcher = change_matcher.MatchAny(matchers)

    def setFileMatcher(self, files):
        # Set the file matcher to match any of the change files
        self._files = files
        matchers = []
        for fn in files:
            matchers.append(change_matcher.FileMatcher(fn))
        self.file_matcher = change_matcher.MatchAny(matchers)

    def setIrrelevantFileMatcher(self, irrelevant_files):
        # Set the irrelevant file matcher to match any of the change files
        self._irrelevant_files = irrelevant_files
        matchers = []
        for fn in irrelevant_files:
            matchers.append(change_matcher.FileMatcher(fn))
        self.irrelevant_file_matcher = change_matcher.MatchAllFiles(matchers)

    def getSimpleBranchMatcher(self):
        # If the job has a simple branch matcher, return it; otherwise None.
        if not self.branch_matcher:
            return None
        m = self.branch_matcher
        if not isinstance(m, change_matcher.AbstractMatcherCollection):
            return None
        if len(m.matchers) != 1:
            return None
        m = m.matchers[0]
        if not isinstance(m, change_matcher.BranchMatcher):
            return None
        return m

    def addImpliedBranchMatcher(self, branch):
        # Add a branch matcher that combines as a boolean *and* with
        # existing branch matchers, if any.
        self._implied_branch = branch
        matchers = [change_matcher.ImpliedBranchMatcher(branch)]
        if self.branch_matcher:
            matchers.append(self.branch_matcher)
        self.branch_matcher = change_matcher.MatchAll(matchers)

    def updateVariables(self, other_vars, other_extra_vars, other_host_vars,
                        other_group_vars):
        if other_vars is not None:
            self.variables = Job._deepUpdate(self.variables, other_vars)
        if other_extra_vars is not None:
            self.extra_variables = Job._deepUpdate(
                self.extra_variables, other_extra_vars)
        if other_host_vars is not None:
            self.host_variables = Job._deepUpdate(
                self.host_variables, other_host_vars)
        if other_group_vars is not None:
            self.group_variables = Job._deepUpdate(
                self.group_variables, other_group_vars)

    def updateParentData(self, other_vars):
        # Update variables, but give the current values priority (used
        # for job return data which is lower precedence than defined
        # job vars).
        v = self.parent_data or {}
        v = Job._deepUpdate(v, other_vars)
        # To avoid running afoul of checks that jobs don't set zuul
        # variables, remove them from parent data here.
        if 'zuul' in v:
            del v['zuul']
        self.parent_data = v
        self.variables = Job._deepUpdate(self.parent_data, self.variables)

    def updateProjectVariables(self, project_vars):
        # Merge project/template variables directly into the job
        # variables.  Job variables override project variables.
        self.variables = Job._deepUpdate(project_vars, self.variables)

    def updateProjects(self, other_projects):
        required_projects = self.required_projects.copy()
        required_projects.update(other_projects)
        self.required_projects = required_projects

    @staticmethod
    def _deepUpdate(a, b):
        # Merge nested dictionaries if possible, otherwise, overwrite
        # the value in 'a' with the value in 'b'.

        ret = {}
        for k, av in a.items():
            if k not in b:
                ret[k] = av
        for k, bv in b.items():
            av = a.get(k)
            if (isinstance(av, (dict, types.MappingProxyType)) and
                isinstance(bv, (dict, types.MappingProxyType))):
                ret[k] = Job._deepUpdate(av, bv)
            else:
                ret[k] = bv
        return ret

    def copy(self):
        job = Job(self.name)
        for k in self.attributes:
            v = self._get(k)
            if v is not None:
                # If this is a config object, it's frozen, so it's
                # safe to shallow copy.
                setattr(job, k, v)
        return job

    def freezePlaybooks(self, pblist, layout):
        """Take a list of playbooks, and return a copy of it updated with this
        job's roles.

        """

        ret = []
        for old_pb in pblist:
            pb = old_pb.copy()
            pb.roles = self.roles
            pb.freezeSecrets(layout)
            ret.append(pb)
        return tuple(ret)

    def applyVariant(self, other, layout):
        """Copy the attributes which have been set on the other job to this
        job."""
        if not isinstance(other, Job):
            raise Exception("Job unable to inherit from %s" % (other,))

        for k in self.execution_attributes:
            if (other._get(k) is not None and
                k not in set(['final', 'abstract', 'protected'])):
                if self.final:
                    raise Exception("Unable to modify final job %s attribute "
                                    "%s=%s with variant %s" % (
                                        repr(self), k, other._get(k),
                                        repr(other)))
                if self.protected_origin:
                    # this is a protected job, check origin of job definition
                    this_origin = self.protected_origin
                    other_origin = other.source_context.project.canonical_name
                    if this_origin != other_origin:
                        raise Exception("Job %s which is defined in %s is "
                                        "protected and cannot be inherited "
                                        "from other projects."
                                        % (repr(self), this_origin))
                if k not in set(['pre_run', 'run', 'post_run', 'roles',
                                 'variables', 'extra_variables',
                                 'host_variables', 'group_variables',
                                 'required_projects', 'allowed_projects']):
                    setattr(self, k, other._get(k))

        # Don't set final above so that we don't trip an error halfway
        # through assignment.
        if other.final != self.attributes['final']:
            self.final = other.final

        # Abstract may not be reset by a variant, it may only be
        # cleared by inheriting.
        if other.name != self.name:
            self.abstract = other.abstract
        elif other.abstract:
            self.abstract = True

        # Protected may only be set to true
        if other.protected is not None:
            # don't allow to reset protected flag
            if not other.protected and self.protected_origin:
                raise Exception("Unable to reset protected attribute of job"
                                " %s by job %s" % (
                                    repr(self), repr(other)))
            if not self.protected_origin:
                self.protected_origin = \
                    other.source_context.project.canonical_name

        # We must update roles before any playbook contexts
        if other._get('roles') is not None:
            self.addRoles(other.roles)

        # Freeze the nodeset
        self.nodeset = self.getNodeSet(layout)

        # Pass secrets to parents
        secrets_for_parents = [s for s in other.secrets if s.pass_to_parent]
        if secrets_for_parents:
            decrypted_secrets = []
            for secret_use in secrets_for_parents:
                secret = layout.secrets.get(secret_use.name)
                decrypted_secret = secret.decrypt(
                    other.source_context.project.private_secrets_key)
            decrypted_secret.name = secret_use.alias
            decrypted_secrets.append(decrypted_secret)
            # Add the secrets to any existing playbooks.  If any of
            # them are in an untrusted project, then we've just given
            # a secret to a playbook which can run in dynamic config,
            # therefore it's no longer safe to run this job
            # pre-review.  The only way pass-to-parent can work with
            # pre-review pipeline is if all playbooks are in the
            # trusted context.
            for pb in itertools.chain(self.pre_run, self.run, self.post_run):
                pb.addSecrets(decrypted_secrets)
                if not pb.source_context.trusted:
                    self.post_review = True

        if other._get('run') is not None:
            other_run = self.freezePlaybooks(other.run, layout)
            self.run = other_run
        if other._get('pre_run') is not None:
            other_pre_run = self.freezePlaybooks(other.pre_run, layout)
            self.pre_run = self.pre_run + other_pre_run
        if other._get('post_run') is not None:
            other_post_run = self.freezePlaybooks(other.post_run, layout)
            self.post_run = other_post_run + self.post_run
        self.updateVariables(other.variables, other.extra_variables,
                             other.host_variables, other.group_variables)
        if other._get('required_projects') is not None:
            self.updateProjects(other.required_projects)
        if (other._get('allowed_projects') is not None and
            self._get('allowed_projects') is not None):
            self.allowed_projects = frozenset(
                self.allowed_projects.intersection(
                    other.allowed_projects))
        elif other._get('allowed_projects') is not None:
            self.allowed_projects = other.allowed_projects

        for k in self.context_attributes:
            if (other._get(k) is not None and
                k not in set(['tags'])):
                setattr(self, k, other._get(k))

        if other._get('tags') is not None:
            self.tags = frozenset(self.tags.union(other.tags))

        self.inheritance_path = self.inheritance_path + (repr(other),)

    def changeMatchesBranch(self, change, override_branch=None):
        if override_branch is None:
            branch_change = change
        else:
            # If an override branch is supplied, create a very basic
            # change (a Ref) and set its branch to the override
            # branch.
            branch_change = Ref(change.project)
            branch_change.ref = override_branch

        if self.branch_matcher and not self.branch_matcher.matches(
                branch_change):
            return False
        return True

    def changeMatchesFiles(self, change):
        if self.file_matcher and not self.file_matcher.matches(change):
            return False

        # NB: This is a negative match.
        if (self.irrelevant_file_matcher and
            self.irrelevant_file_matcher.matches(change)):
            return False

        return True


class JobProject(ConfigObject):
    """ A reference to a project from a job. """

    def __init__(self, project_name, override_branch=None,
                 override_checkout=None):
        super(JobProject, self).__init__()
        self.project_name = project_name
        self.override_branch = override_branch
        self.override_checkout = override_checkout

    def toDict(self):
        d = dict()
        d['project_name'] = self.project_name
        d['override_branch'] = self.override_branch
        d['override_checkout'] = self.override_checkout
        return d


class JobSemaphore(ConfigObject):
    """ A reference to a semaphore from a job. """

    def __init__(self, semaphore_name, resources_first=False):
        super().__init__()
        self.name = semaphore_name
        self.resources_first = resources_first

    def toDict(self):
        d = dict()
        d['name'] = self.name
        d['resources_first'] = self.resources_first
        return d


class JobList(ConfigObject):
    """ A list of jobs in a project's pipeline. """

    def __init__(self):
        super(JobList, self).__init__()
        self.jobs = OrderedDict()  # job.name -> [job, ...]

    def addJob(self, job):
        if job.name in self.jobs:
            self.jobs[job.name].append(job)
        else:
            self.jobs[job.name] = [job]

    def inheritFrom(self, other):
        for jobname, jobs in other.jobs.items():
            joblist = self.jobs.setdefault(jobname, [])
            for job in jobs:
                if job not in joblist:
                    joblist.append(job)


class JobGraph(object):
    """ A JobGraph represents the dependency graph between Job."""

    def __init__(self):
        self.jobs = OrderedDict()  # job_name -> Job
        self._dependencies = {}  # dependent_job_name -> set(parent_job_names)

    def __repr__(self):
        return '<JobGraph %s>' % (self.jobs)

    def addJob(self, job):
        # A graph must be created after the job list is frozen,
        # therefore we should only get one job with the same name.
        if job.name in self.jobs:
            raise Exception("Job %s already added" % (job.name,))
        self.jobs[job.name] = job
        # Append the dependency information
        self._dependencies.setdefault(job.name, set())
        try:
            for dependency in job.dependencies:
                # Make sure a circular dependency is never created
                ancestor_jobs = self._getParentJobNamesRecursively(
                    dependency, soft=True)
                ancestor_jobs.add(dependency)
                if any((job.name == anc_job) for anc_job in ancestor_jobs):
                    raise Exception("Dependency cycle detected in job %s" %
                                    (job.name,))
                self._dependencies[job.name].add(dependency)
        except Exception:
            del self.jobs[job.name]
            del self._dependencies[job.name]
            raise

    def getJobs(self):
        return list(self.jobs.values())  # Report in the order of layout cfg

    def getDirectDependentJobs(self, parent_job):
        ret = set()
        for dependent_name, parent_names in self._dependencies.items():
            if parent_job in parent_names:
                ret.add(dependent_name)
        return ret

    def getDependentJobsRecursively(self, parent_job):
        all_dependent_jobs = set()
        jobs_to_iterate = set([parent_job])
        while len(jobs_to_iterate) > 0:
            current_job = jobs_to_iterate.pop()
            current_dependent_jobs = self.getDirectDependentJobs(current_job)
            new_dependent_jobs = current_dependent_jobs - all_dependent_jobs
            jobs_to_iterate |= new_dependent_jobs
            all_dependent_jobs |= new_dependent_jobs
        return [self.jobs[name] for name in all_dependent_jobs]

    def getParentJobsRecursively(self, dependent_job, soft=False):
        return [self.jobs[name] for name in
                self._getParentJobNamesRecursively(dependent_job, soft)]

    def _getParentJobNamesRecursively(self, dependent_job, soft=False):
        all_parent_jobs = set()
        jobs_to_iterate = set([dependent_job])
        while len(jobs_to_iterate) > 0:
            current_job = jobs_to_iterate.pop()
            current_parent_jobs = self._dependencies.get(current_job)
            if current_parent_jobs is None:
                if soft:
                    current_parent_jobs = set()
                else:
                    raise Exception("Job %s depends on %s which was not run." %
                                    (dependent_job, current_job))
            new_parent_jobs = current_parent_jobs - all_parent_jobs
            jobs_to_iterate |= new_parent_jobs
            all_parent_jobs |= new_parent_jobs
        return all_parent_jobs


class Build(object):
    """A Build is an instance of a single execution of a Job.

    While a Job describes what to run, a Build describes an actual
    execution of that Job.  Each build is associated with exactly one
    Job (related builds are grouped together in a BuildSet).
    """

    def __init__(self, job, uuid):
        self.job = job
        self.uuid = uuid
        self.url = None
        self.result = None
        self.result_data = {}
        self.error_detail = None
        self.build_set = None
        self.execute_time = time.time()
        self.start_time = None
        self.end_time = None
        self.estimated_time = None
        self.canceled = False
        self.paused = False
        self.retry = False
        self.parameters = {}
        self.worker = Worker()
        self.node_labels = []
        self.node_name = None
        self.nodeset = None

    def __repr__(self):
        return ('<Build %s of %s voting:%s on %s>' %
                (self.uuid, self.job.name, self.job.voting, self.worker))

    @property
    def pipeline(self):
        return self.build_set.item.pipeline

    def getSafeAttributes(self):
        return Attributes(uuid=self.uuid,
                          result=self.result,
                          error_detail=self.error_detail,
                          result_data=self.result_data)


class Worker(object):
    """Information about the specific worker executing a Build."""
    def __init__(self):
        self.name = "Unknown"
        self.hostname = None
        self.log_port = None

    def updateFromData(self, data):
        """Update worker information if contained in the WORK_DATA response."""
        self.name = data.get('worker_name', self.name)
        self.hostname = data.get('worker_hostname', self.hostname)
        self.log_port = data.get('worker_log_port', self.log_port)

    def __repr__(self):
        return '<Worker %s>' % self.name


class RepoFiles(object):
    """RepoFiles holds config-file content for per-project job config.

    When Zuul asks a merger to prepare a future multiple-repo state
    and collect Zuul configuration files so that we can dynamically
    load our configuration, this class provides cached access to that
    data for use by the Change which updated the config files and any
    changes that follow it in a ChangeQueue.

    It is attached to a BuildSet since the content of Zuul
    configuration files can change with each new BuildSet.
    """

    def __init__(self):
        self.connections = {}

    def __repr__(self):
        return '<RepoFiles %s>' % self.connections

    def setFiles(self, items):
        self.hostnames = {}
        for item in items:
            connection = self.connections.setdefault(
                item['connection'], {})
            project = connection.setdefault(item['project'], {})
            branch = project.setdefault(item['branch'], {})
            branch.update(item['files'])

    def getFile(self, connection_name, project_name, branch, fn):
        host = self.connections.get(connection_name, {})
        return host.get(project_name, {}).get(branch, {}).get(fn)


class BuildSet(object):
    """A collection of Builds for one specific potential future repository
    state.

    When Zuul executes Builds for a change, it creates a Build to
    represent each execution of each job and a BuildSet to keep track
    of all the Builds running for that Change.  When Zuul re-executes
    Builds for a Change with a different configuration, all of the
    running Builds in the BuildSet for that change are aborted, and a
    new BuildSet is created to hold the Builds for the Jobs being
    run with the new configuration.

    A BuildSet also holds the UUID used to produce the Zuul Ref that
    builders check out.

    """
    # Merge states:
    NEW = 1
    PENDING = 2
    COMPLETE = 3

    states_map = {
        1: 'NEW',
        2: 'PENDING',
        3: 'COMPLETE',
    }

    def __init__(self, item):
        self.item = item
        self.builds = {}
        self.result = None
        self.uuid = None
        self.commit = None
        self.dependent_changes = None
        self.merger_items = None
        self.unable_to_merge = False
        self.config_errors = []  # list of ConfigurationErrors
        self.failing_reasons = []
        self.debug_messages = []
        self.warning_messages = []
        self.merge_state = self.NEW
        self.nodesets = {}  # job -> nodeset
        self.node_requests = {}  # job -> reqs
        self.files = RepoFiles()
        self.repo_state = {}
        self.tries = {}
        if item.change.files is not None:
            self.files_state = self.COMPLETE
        else:
            self.files_state = self.NEW

    @property
    def ref(self):
        # NOTE(jamielennox): The concept of buildset ref is to be removed and a
        # buildset UUID identifier available instead. Currently the ref is
        # checked to see if the BuildSet has been configured.
        return 'Z' + self.uuid if self.uuid else None

    def __repr__(self):
        return '<BuildSet item: %s #builds: %s merge state: %s>' % (
            self.item,
            len(self.builds),
            self.getStateName(self.merge_state))

    def setConfiguration(self):
        # The change isn't enqueued until after it's created
        # so we don't know what the other changes ahead will be
        # until jobs start.
        if not self.uuid:
            self.uuid = uuid4().hex
        if self.dependent_changes is None:
            items = [self.item]
            next_item = self.item.item_ahead
            while next_item:
                items.append(next_item)
                next_item = next_item.item_ahead
            items.reverse()
            self.dependent_changes = [i.change.toDict() for i in items]
            self.merger_items = [i.makeMergerItem() for i in items]

    def getStateName(self, state_num):
        return self.states_map.get(
            state_num, 'UNKNOWN (%s)' % state_num)

    def addBuild(self, build):
        self.builds[build.job.name] = build
        if build.job.name not in self.tries:
            self.tries[build.job.name] = 1
        build.build_set = self

    def removeBuild(self, build):
        if build.job.name not in self.builds:
            return
        self.tries[build.job.name] += 1
        del self.builds[build.job.name]

    def getBuild(self, job_name):
        return self.builds.get(job_name)

    def getBuilds(self):
        keys = list(self.builds.keys())
        keys.sort()
        return [self.builds.get(x) for x in keys]

    def getJobNodeSet(self, job_name):
        # Return None if not provisioned; empty NodeSet if no nodes
        # required
        return self.nodesets.get(job_name)

    def removeJobNodeSet(self, job_name):
        if job_name not in self.nodesets:
            raise Exception("No job nodeset for %s" % (job_name))
        del self.nodesets[job_name]

    def setJobNodeRequest(self, job_name, req):
        if job_name in self.node_requests:
            raise Exception("Prior node request for %s" % (job_name))
        self.node_requests[job_name] = req

    def getJobNodeRequest(self, job_name):
        return self.node_requests.get(job_name)

    def removeJobNodeRequest(self, job_name):
        if job_name in self.node_requests:
            del self.node_requests[job_name]

    def jobNodeRequestComplete(self, job_name, req, nodeset):
        if job_name in self.nodesets:
            raise Exception("Prior node request for %s" % (job_name))
        self.nodesets[job_name] = nodeset
        del self.node_requests[job_name]

    def getTries(self, job_name):
        return self.tries.get(job_name, 0)

    def getMergeMode(self):
        # We may be called before this build set has a shadow layout
        # (ie, we are called to perform the merge to create that
        # layout).  It's possible that the change we are merging will
        # update the merge-mode for the project, but there's not much
        # we can do about that here.  Instead, do the best we can by
        # using the nearest shadow layout to determine the merge mode,
        # or if that fails, the current live layout, or if that fails,
        # use the default: merge-resolve.
        item = self.item
        layout = None
        while item:
            layout = item.layout
            if layout:
                break
            item = item.item_ahead
        if not layout:
            layout = self.item.pipeline.tenant.layout
        if layout:
            project = self.item.change.project
            project_metadata = layout.getProjectMetadata(
                project.canonical_name)
            if project_metadata:
                return project_metadata.merge_mode
        return MERGER_MERGE_RESOLVE

    def getSafeAttributes(self):
        return Attributes(uuid=self.uuid)


class QueueItem(object):
    """Represents the position of a Change in a ChangeQueue.

    All Changes are enqueued into ChangeQueue in a QueueItem. The QueueItem
    holds the current `BuildSet` as well as all previous `BuildSets` that were
    produced for this `QueueItem`.
    """
    log = logging.getLogger("zuul.QueueItem")

    def __init__(self, queue, change):
        self.pipeline = queue.pipeline
        self.queue = queue
        self.change = change  # a ref
        self.dequeued_needing_change = False
        self.current_build_set = BuildSet(self)
        self.item_ahead = None
        self.items_behind = []
        self.enqueue_time = None
        self.dequeue_time = None
        self.reported = False
        self.reported_start = False
        self.quiet = False
        self.active = False  # Whether an item is within an active window
        self.live = True  # Whether an item is intended to be processed at all
        self.layout = None
        self.project_pipeline_config = None
        self.job_graph = None

    def __repr__(self):
        if self.pipeline:
            pipeline = self.pipeline.name
        else:
            pipeline = None
        return '<QueueItem 0x%x for %s in %s>' % (
            id(self), self.change, pipeline)

    def resetAllBuilds(self):
        self.current_build_set = BuildSet(self)
        self.layout = None
        self.project_pipeline_config = None
        self.job_graph = None

    def addBuild(self, build):
        self.current_build_set.addBuild(build)

    def removeBuild(self, build):
        self.current_build_set.removeBuild(build)

    def setReportedResult(self, result):
        self.current_build_set.result = result

    def debug(self, msg, indent=0):
        if (not self.project_pipeline_config or
            not self.project_pipeline_config.debug):
            return
        if indent:
            indent = '  ' * indent
        else:
            indent = ''
        self.current_build_set.debug_messages.append(indent + msg)

    def warning(self, msg):
        self.current_build_set.warning_messages.append(msg)

    def freezeJobGraph(self):
        """Find or create actual matching jobs for this item's change and
        store the resulting job tree."""

        ppc = self.layout.getProjectPipelineConfig(self)
        try:
            # Conditionally set self.ppc so that the debug method can
            # consult it as we resolve the jobs.
            self.project_pipeline_config = ppc
            if ppc:
                for msg in ppc.debug_messages:
                    self.debug(msg)
            job_graph = self.layout.createJobGraph(self, ppc)
            for job in job_graph.getJobs():
                # Ensure that each jobs's dependencies are fully
                # accessible.  This will raise an exception if not.
                job_graph.getParentJobsRecursively(job.name)
            self.job_graph = job_graph
        except Exception:
            self.project_pipeline_config = None
            self.job_graph = None
            raise

    def hasJobGraph(self):
        """Returns True if the item has a job graph."""
        return self.job_graph is not None

    def getJobs(self):
        if not self.live or not self.job_graph:
            return []
        return self.job_graph.getJobs()

    def getJob(self, name):
        if not self.job_graph:
            return None
        return self.job_graph.jobs.get(name)

    def haveAllJobsStarted(self):
        if not self.hasJobGraph():
            return False
        for job in self.getJobs():
            build = self.current_build_set.getBuild(job.name)
            if not build or not build.start_time:
                return False
        return True

    def areAllJobsComplete(self):
        if (self.current_build_set.config_errors or
            self.current_build_set.unable_to_merge):
            return True
        if not self.hasJobGraph():
            return False
        for job in self.getJobs():
            build = self.current_build_set.getBuild(job.name)
            if not build or not build.result:
                return False
        return True

    def didAllJobsSucceed(self):
        """Check if all jobs have completed with status SUCCESS.

        Return True if all voting jobs have completed with status
        SUCCESS.  Non-voting jobs are ignored.  Skipped jobs are
        ignored, but skipping all jobs returns a failure.  Incomplete
        builds are considered a failure, hence this is unlikely to be
        useful unless all builds are complete.

        """
        if not self.hasJobGraph():
            return False

        all_jobs_skipped = True
        for job in self.getJobs():
            build = self.current_build_set.getBuild(job.name)
            if build:
                # If the build ran, record whether or not it was skipped
                # and return False if the build was voting and has an
                # unsuccessful return value
                if build.result != 'SKIPPED':
                    all_jobs_skipped = False
                if job.voting and build.result not in ['SUCCESS', 'SKIPPED']:
                    return False
            elif job.voting:
                # If the build failed to run and was voting that is an
                # unsuccessful build. But we don't count against it if not
                # voting.
                return False

        # NOTE(pabelanger): We shouldn't be able to skip all jobs.
        if all_jobs_skipped:
            return False

        return True

    def hasAnyJobFailed(self):
        """Check if any jobs have finished with a non-success result.

        Return True if any job in the job graph has returned with a
        status not equal to SUCCESS or SKIPPED, else return False.
        Non-voting and in-flight jobs are ignored.

        """
        if not self.hasJobGraph():
            return False
        for job in self.getJobs():
            if not job.voting:
                continue
            build = self.current_build_set.getBuild(job.name)
            if (build and build.result and
                    build.result not in ['SUCCESS', 'SKIPPED']):
                return True
        return False

    def didMergerFail(self):
        return self.current_build_set.unable_to_merge

    def getConfigErrors(self):
        return self.current_build_set.config_errors

    def wasDequeuedNeedingChange(self):
        return self.dequeued_needing_change

    def includesConfigUpdates(self):
        includes_trusted = False
        includes_untrusted = False
        tenant = self.pipeline.tenant
        item = self
        while item:
            if item.change.updatesConfig():
                (trusted, project) = tenant.getProject(
                    item.change.project.canonical_name)
                if trusted:
                    includes_trusted = True
                else:
                    includes_untrusted = True
            if includes_trusted and includes_untrusted:
                # We're done early
                return (includes_trusted, includes_untrusted)
            item = item.item_ahead
        return (includes_trusted, includes_untrusted)

    def isHoldingFollowingChanges(self):
        if not self.live:
            return False
        if not self.hasJobGraph():
            return False
        for job in self.getJobs():
            if not job.hold_following_changes:
                continue
            build = self.current_build_set.getBuild(job.name)
            if not build:
                return True
            if build.result != 'SUCCESS':
                return True

        if not self.item_ahead:
            return False
        return self.item_ahead.isHoldingFollowingChanges()

    def findJobsToRun(self, semaphore_handler):
        torun = []
        if not self.live:
            return []
        if not self.job_graph:
            return []
        if self.item_ahead:
            # Only run jobs if any 'hold' jobs on the change ahead
            # have completed successfully.
            if self.item_ahead.isHoldingFollowingChanges():
                return []

        successful_job_names = set()
        jobs_not_started = set()
        for job in self.job_graph.getJobs():
            build = self.current_build_set.getBuild(job.name)
            if build:
                if build.result == 'SUCCESS' or build.paused:
                    successful_job_names.add(job.name)
            else:
                jobs_not_started.add(job)

        # Attempt to run jobs in the order they appear in
        # configuration.
        for job in self.job_graph.getJobs():
            if job not in jobs_not_started:
                continue
            all_parent_jobs_successful = True
            parent_builds_with_data = {}
            for parent_job in self.job_graph.getParentJobsRecursively(
                    job.name):
                if parent_job.name not in successful_job_names:
                    all_parent_jobs_successful = False
                    break
                parent_build = self.current_build_set.getBuild(parent_job.name)
                if parent_build.result_data:
                    parent_builds_with_data[parent_job.name] = parent_build

            if all_parent_jobs_successful:
                # Iterate in reverse order over all jobs of the graph (which is
                # in sorted config order) and apply parent data of the jobs we
                # already found.
                if len(parent_builds_with_data) > 0:
                    for parent_job in reversed(self.job_graph.getJobs()):
                        parent_build = parent_builds_with_data.get(
                            parent_job.name)
                        if parent_build:
                            job.updateParentData(parent_build.result_data)

                nodeset = self.current_build_set.getJobNodeSet(job.name)
                if nodeset is None:
                    # The nodes for this job are not ready, skip
                    # it for now.
                    continue
                if semaphore_handler.acquire(self, job, False):
                    # If this job needs a semaphore, either acquire it or
                    # make sure that we have it before running the job.
                    torun.append(job)
        return torun

    def findJobsToRequest(self, semaphore_handler):
        build_set = self.current_build_set
        toreq = []
        if not self.live:
            return []
        if not self.job_graph:
            return []
        if self.item_ahead:
            if self.item_ahead.isHoldingFollowingChanges():
                return []

        successful_job_names = set()
        jobs_not_requested = set()
        for job in self.job_graph.getJobs():
            build = build_set.getBuild(job.name)
            if build and (build.result == 'SUCCESS' or build.paused):
                successful_job_names.add(job.name)
            elif build and build.result == 'SKIPPED':
                pass
            else:
                nodeset = build_set.getJobNodeSet(job.name)
                if nodeset is None:
                    req = build_set.getJobNodeRequest(job.name)
                    if req is None:
                        jobs_not_requested.add(job)

        # Attempt to request nodes for jobs in the order jobs appear
        # in configuration.
        for job in self.job_graph.getJobs():
            if job not in jobs_not_requested:
                continue
            all_parent_jobs_successful = True
            for parent_job in self.job_graph.getParentJobsRecursively(
                    job.name):
                if parent_job.name not in successful_job_names:
                    all_parent_jobs_successful = False
                    break
            if all_parent_jobs_successful:
                if semaphore_handler.acquire(self, job, True):
                    # If this job needs a semaphore, either acquire it or
                    # make sure that we have it before requesting the nodes.
                    toreq.append(job)
        return toreq

    def setResult(self, build):
        if build.retry:
            self.removeBuild(build)
            return

        skipped = []
        # NOTE(pabelanger): Check successful jobs to see if zuul_return
        # includes zuul.child_jobs.
        build_result = build.result_data.get('zuul', {})
        if 'child_jobs' in build_result:
            zuul_return = build_result.get('child_jobs', [])
            dependent_jobs = self.job_graph.getDirectDependentJobs(
                build.job.name)

            if not zuul_return:
                # If zuul.child_jobs exists and is empty, user want to skip all
                # child jobs.
                skipped += self.job_graph.getDependentJobsRecursively(
                    build.job.name)
            else:
                # We have list of jobs to run.
                intersect_jobs = dependent_jobs.intersection(zuul_return)

                for skip in (dependent_jobs - intersect_jobs):
                    skipped.append(self.job_graph.jobs.get(skip))
                    skipped += self.job_graph.getDependentJobsRecursively(
                        skip)

        elif build.result != 'SUCCESS' and not build.paused:
            skipped += self.job_graph.getDependentJobsRecursively(
                build.job.name)

        for job in skipped:
            fakebuild = Build(job, None)
            fakebuild.result = 'SKIPPED'
            self.addBuild(fakebuild)

    def setNodeRequestFailure(self, job):
        fakebuild = Build(job, None)
        fakebuild.start_time = time.time()
        fakebuild.end_time = time.time()
        self.addBuild(fakebuild)
        fakebuild.result = 'NODE_FAILURE'
        self.setResult(fakebuild)

    def setDequeuedNeedingChange(self):
        self.dequeued_needing_change = True
        self._setAllJobsSkipped()

    def setUnableToMerge(self):
        self.current_build_set.unable_to_merge = True
        self._setAllJobsSkipped()

    def setConfigError(self, error):
        err = ConfigurationError(None, None, error)
        self.setConfigErrors([err])

    def setConfigErrors(self, errors):
        self.current_build_set.config_errors = errors
        self._setAllJobsSkipped()

    def _setAllJobsSkipped(self):
        for job in self.getJobs():
            fakebuild = Build(job, None)
            fakebuild.result = 'SKIPPED'
            self.addBuild(fakebuild)

    def getNodePriority(self):
        return self.pipeline.manager.getNodePriority(self)

    def formatUrlPattern(self, url_pattern, job=None, build=None):
        url = None
        # Produce safe versions of objects which may be useful in
        # result formatting, but don't allow users to crawl through
        # the entire data structure where they might be able to access
        # secrets, etc.
        safe_change = self.change.getSafeAttributes()
        safe_pipeline = self.pipeline.getSafeAttributes()
        safe_tenant = self.pipeline.tenant.getSafeAttributes()
        safe_buildset = self.current_build_set.getSafeAttributes()
        safe_job = job.getSafeAttributes() if job else {}
        safe_build = build.getSafeAttributes() if build else {}
        try:
            url = url_pattern.format(change=safe_change,
                                     pipeline=safe_pipeline,
                                     tenant=safe_tenant,
                                     buildset=safe_buildset,
                                     job=safe_job,
                                     build=safe_build)
        except KeyError as e:
            self.log.error("Error while formatting url for job %s: unknown "
                           "key %s in pattern %s"
                           % (job, e.args[0], url_pattern))
        except AttributeError as e:
            self.log.error("Error while formatting url for job %s: unknown "
                           "attribute %s in pattern %s"
                           % (job, e.args[0], url_pattern))
        except Exception:
            self.log.exception("Error while formatting url for job %s with "
                               "pattern %s:" % (job, url_pattern))

        return url

    def formatJobResult(self, job):
        build = self.current_build_set.getBuild(job.name)
        result = build.result
        pattern = None
        if result == 'SUCCESS':
            if job.success_message:
                result = job.success_message
            if job.success_url:
                pattern = job.success_url
        else:
            if job.failure_message:
                result = job.failure_message
            if job.failure_url:
                pattern = job.failure_url
        url = None  # The final URL
        default_url = build.result_data.get('zuul', {}).get('log_url')
        if pattern:
            job_url = self.formatUrlPattern(pattern, job, build)
        else:
            job_url = None
        try:
            if job_url:
                u = urllib.parse.urlparse(job_url)
                if u.scheme:
                    # The job success or failure url is absolute, so it's
                    # our final url.
                    url = job_url
                else:
                    # We have a relative job url.  Combine it with our
                    # default url.
                    if default_url:
                        url = urllib.parse.urljoin(default_url, job_url)
        except Exception:
            self.log.exception("Error while parsing url for job %s:"
                               % (job,))
        if not url:
            url = default_url or build.url or job.name
        return (result, url)

    def formatJSON(self, websocket_url=None):
        ret = {}
        ret['active'] = self.active
        ret['live'] = self.live
        if hasattr(self.change, 'url') and self.change.url is not None:
            ret['url'] = self.change.url
        else:
            ret['url'] = None
        if hasattr(self.change, 'ref') and self.change.ref is not None:
            ret['ref'] = self.change.ref
        else:
            ret['ref'] = None
        ret['id'] = self.change._id()
        if self.item_ahead:
            ret['item_ahead'] = self.item_ahead.change._id()
        else:
            ret['item_ahead'] = None
        ret['items_behind'] = [i.change._id() for i in self.items_behind]
        ret['failing_reasons'] = self.current_build_set.failing_reasons
        ret['zuul_ref'] = self.current_build_set.ref
        if self.change.project:
            ret['project'] = self.change.project.name
            ret['project_canonical'] = self.change.project.canonical_name
        else:
            # For cross-project dependencies with the depends-on
            # project not known to zuul, the project is None
            # Set it to a static value
            ret['project'] = "Unknown Project"
            ret['project_canonical'] = "Unknown Project"
        ret['enqueue_time'] = int(self.enqueue_time * 1000)
        ret['jobs'] = []
        if hasattr(self.change, 'owner'):
            ret['owner'] = self.change.owner
        else:
            ret['owner'] = None
        max_remaining = 0
        for job in self.getJobs():
            now = time.time()
            build = self.current_build_set.getBuild(job.name)
            elapsed = None
            remaining = None
            result = None
            build_url = None
            finger_url = None
            report_url = None
            worker = None
            if build:
                result = build.result
                finger_url = build.url
                # TODO(tobiash): add support for custom web root
                urlformat = 'stream/{build.uuid}?' \
                            'logfile=console.log'
                if websocket_url:
                    urlformat += '&websocket_url={websocket_url}'
                build_url = urlformat.format(
                    build=build, websocket_url=websocket_url)
                (unused, report_url) = self.formatJobResult(job)
                if build.start_time:
                    if build.end_time:
                        elapsed = int((build.end_time -
                                       build.start_time) * 1000)
                        remaining = 0
                    else:
                        elapsed = int((now - build.start_time) * 1000)
                        if build.estimated_time:
                            remaining = max(
                                int(build.estimated_time * 1000) - elapsed,
                                0)
                worker = {
                    'name': build.worker.name,
                    'hostname': build.worker.hostname,
                }
            if remaining and remaining > max_remaining:
                max_remaining = remaining

            ret['jobs'].append({
                'name': job.name,
                'dependencies': list(job.dependencies),
                'elapsed_time': elapsed,
                'remaining_time': remaining,
                'url': build_url,
                'finger_url': finger_url,
                'report_url': report_url,
                'result': result,
                'voting': job.voting,
                'uuid': build.uuid if build else None,
                'execute_time': build.execute_time if build else None,
                'start_time': build.start_time if build else None,
                'end_time': build.end_time if build else None,
                'estimated_time': build.estimated_time if build else None,
                'pipeline': build.pipeline.name if build else None,
                'canceled': build.canceled if build else None,
                'paused': build.paused if build else None,
                'retry': build.retry if build else None,
                'node_labels': build.node_labels if build else [],
                'node_name': build.node_name if build else None,
                'worker': worker,
            })

        if self.haveAllJobsStarted():
            ret['remaining_time'] = max_remaining
        else:
            ret['remaining_time'] = None
        return ret

    def formatStatus(self, indent=0, html=False):
        indent_str = ' ' * indent
        ret = ''
        if html and getattr(self.change, 'url', None) is not None:
            ret += '%sProject %s change <a href="%s">%s</a>\n' % (
                indent_str,
                self.change.project.name,
                self.change.url,
                self.change._id())
        else:
            ret += '%sProject %s change %s based on %s\n' % (
                indent_str,
                self.change.project.name,
                self.change._id(),
                self.item_ahead)
        for job in self.getJobs():
            build = self.current_build_set.getBuild(job.name)
            if build:
                result = build.result
            else:
                result = None
            job_name = job.name
            if not job.voting:
                voting = ' (non-voting)'
            else:
                voting = ''
            if html:
                if build:
                    url = build.url
                else:
                    url = None
                if url is not None:
                    job_name = '<a href="%s">%s</a>' % (url, job_name)
            ret += '%s  %s: %s%s' % (indent_str, job_name, result, voting)
            ret += '\n'
        return ret

    def makeMergerItem(self):
        # Create a dictionary with all info about the item needed by
        # the merger.
        number = None
        patchset = None
        oldrev = None
        newrev = None
        branch = None
        if hasattr(self.change, 'number'):
            number = self.change.number
            patchset = self.change.patchset
        if hasattr(self.change, 'newrev'):
            oldrev = self.change.oldrev
            newrev = self.change.newrev
        if hasattr(self.change, 'branch'):
            branch = self.change.branch

        source = self.change.project.source
        connection_name = source.connection.connection_name
        project = self.change.project

        return dict(project=project.name,
                    connection=connection_name,
                    merge_mode=self.current_build_set.getMergeMode(),
                    ref=self.change.ref,
                    branch=branch,
                    buildset_uuid=self.current_build_set.uuid,
                    number=number,
                    patchset=patchset,
                    oldrev=oldrev,
                    newrev=newrev,
                    )


class Ref(object):
    """An existing state of a Project."""

    def __init__(self, project):
        self.project = project
        self.ref = None
        self.oldrev = None
        self.newrev = None
        self.files = []

    def _id(self):
        return self.newrev

    def __repr__(self):
        rep = None
        pname = None
        if self.project and self.project.name:
            pname = self.project.name
        if self.newrev == '0000000000000000000000000000000000000000':
            rep = '<%s 0x%x %s deletes %s from %s' % (
                type(self).__name__, id(self), pname,
                self.ref, self.oldrev)
        elif self.oldrev == '0000000000000000000000000000000000000000':
            rep = '<%s 0x%x %s creates %s on %s>' % (
                type(self).__name__, id(self), pname,
                self.ref, self.newrev)
        else:
            # Catch all
            rep = '<%s 0x%x %s %s updated %s..%s>' % (
                type(self).__name__, id(self), pname,
                self.ref, self.oldrev, self.newrev)
        return rep

    def equals(self, other):
        if (self.project == other.project
            and self.ref == other.ref
            and self.newrev == other.newrev):
            return True
        return False

    def isUpdateOf(self, other):
        return False

    def getRelatedChanges(self):
        return set()

    def updatesConfig(self):
        if self.files is None:
            # If self.files is None we don't know if this change updates the
            # config so assume it does as this is a safe default if we don't
            # know.
            return True
        if 'zuul.yaml' in self.files or '.zuul.yaml' in self.files or \
           [True for fn in self.files if fn.startswith("zuul.d/") or
            fn.startswith(".zuul.d/")]:
            return True
        return False

    def getSafeAttributes(self):
        return Attributes(project=self.project,
                          ref=self.ref,
                          oldrev=self.oldrev,
                          newrev=self.newrev)

    def toDict(self):
        # Render to a dict to use in passing json to the executor
        d = dict()
        d['project'] = dict(
            name=self.project.name,
            short_name=self.project.name.split('/')[-1],
            canonical_hostname=self.project.canonical_hostname,
            canonical_name=self.project.canonical_name,
            src_dir=os.path.join('src', self.project.canonical_name),
        )
        return d


class Branch(Ref):
    """An existing branch state for a Project."""
    def __init__(self, project):
        super(Branch, self).__init__(project)
        self.branch = None

    def toDict(self):
        # Render to a dict to use in passing json to the executor
        d = super(Branch, self).toDict()
        d['branch'] = self.branch
        return d


class Tag(Ref):
    """An existing tag state for a Project."""
    def __init__(self, project):
        super(Tag, self).__init__(project)
        self.tag = None


class Change(Branch):
    """A proposed new state for a Project."""
    def __init__(self, project):
        super(Change, self).__init__(project)
        self.number = None
        # The gitweb url for browsing the change
        self.url = None
        # URIs for this change which may appear in depends-on headers.
        # Note this omits the scheme; i.e., is hostname/path.
        self.uris = []
        self.patchset = None

        # Changes that the source determined are needed due to the
        # git DAG:
        self.git_needs_changes = []
        self.git_needed_by_changes = []

        # Changes that the source determined are needed by backwards
        # compatible processing of Depends-On headers (Gerrit only):
        self.compat_needs_changes = []
        self.compat_needed_by_changes = []

        # Changes that the pipeline manager determined are needed due
        # to Depends-On headers (all drivers):
        self.commit_needs_changes = None
        self.refresh_deps = False

        self.is_current_patchset = True
        self.can_merge = False
        self.is_merged = False
        self.failed_to_merge = False
        self.open = None
        self.status = None
        self.owner = None

        # This may be the commit message, or it may be a cover message
        # in the case of a PR.  Either way, it's the place where we
        # look for depends-on headers.
        self.message = None

        self.source_event = None

    def _id(self):
        return '%s,%s' % (self.number, self.patchset)

    def __repr__(self):
        pname = None
        if self.project and self.project.name:
            pname = self.project.name
        return '<Change 0x%x %s %s>' % (id(self), pname, self._id())

    def equals(self, other):
        if self.number == other.number and self.patchset == other.patchset:
            return True
        return False

    @property
    def needs_changes(self):
        return (self.git_needs_changes + self.compat_needs_changes +
                self.commit_needs_changes)

    @property
    def needed_by_changes(self):
        return (self.git_needed_by_changes + self.compat_needed_by_changes)

    def isUpdateOf(self, other):
        if (self.project == other.project and
            (hasattr(other, 'number') and self.number == other.number) and
            (hasattr(other, 'patchset') and
             self.patchset is not None and
             other.patchset is not None and
             int(self.patchset) > int(other.patchset))):
            return True
        return False

    def getRelatedChanges(self):
        related = set()
        for c in self.needs_changes:
            related.add(c)
        for c in self.needed_by_changes:
            related.add(c)
            related.update(c.getRelatedChanges())
        return related

    def getSafeAttributes(self):
        return Attributes(project=self.project,
                          number=self.number,
                          patchset=self.patchset)

    def toDict(self):
        # Render to a dict to use in passing json to the executor
        d = super(Change, self).toDict()
        d['change'] = str(self.number)
        d['change_url'] = self.url
        d['patchset'] = str(self.patchset)
        return d


class TriggerEvent(object):
    """Incoming event from an external system."""
    def __init__(self):
        # TODO(jeblair): further reduce this list
        self.data = None
        # common
        self.type = None
        self.branch_updated = False
        self.branch_created = False
        self.branch_deleted = False
        self.branch_protected = True
        self.ref = None
        # For management events (eg: enqueue / promote)
        self.tenant_name = None
        self.project_hostname = None
        self.project_name = None
        self.trigger_name = None
        # Representation of the user account that performed the event.
        self.account = None
        # patchset-created, comment-added, etc.
        self.change_number = None
        self.change_url = None
        self.patch_number = None
        self.branch = None
        self.comment = None
        self.state = None
        # ref-updated
        self.oldrev = None
        self.newrev = None
        # For events that arrive with a destination pipeline (eg, from
        # an admin command, etc):
        self.forced_pipeline = None

    @property
    def canonical_project_name(self):
        return self.project_hostname + '/' + self.project_name

    def isPatchsetCreated(self):
        return False

    def isChangeAbandoned(self):
        return False

    def _repr(self):
        flags = [str(self.type)]
        if self.project_name:
            flags.append(self.project_name)
        if self.ref:
            flags.append(self.ref)
        if self.branch_updated:
            flags.append('branch_updated')
        if self.branch_created:
            flags.append('branch_created')
        if self.branch_deleted:
            flags.append('branch_deleted')
        return ' '.join(flags)

    def __repr__(self):
        return '<%s 0x%x %s>' % (self.__class__.__name__,
                                 id(self), self._repr())


class BaseFilter(ConfigObject):
    """Base Class for filtering which Changes and Events to process."""
    pass


class EventFilter(BaseFilter):
    """Allows a Pipeline to only respond to certain events."""
    def __init__(self, trigger):
        super(EventFilter, self).__init__()
        self.trigger = trigger

    def matches(self, event, ref):
        # TODO(jeblair): consider removing ref argument
        return True


class RefFilter(BaseFilter):
    """Allows a Manager to only enqueue Changes that meet certain criteria."""
    def __init__(self, connection_name):
        super(RefFilter, self).__init__()
        self.connection_name = connection_name

    def matches(self, change):
        return True


class TenantProjectConfig(object):
    """A project in the context of a tenant.

    A Project is globally unique in the system, however, when used in
    a tenant, some metadata about the project local to the tenant is
    stored in a TenantProjectConfig.
    """

    def __init__(self, project):
        self.project = project
        self.load_classes = set()
        self.shadow_projects = set()
        self.branches = []
        # The tenant's default setting of exclude_unprotected_branches will
        # be overridden by this one if not None.
        self.exclude_unprotected_branches = None
        self.parsed_branch_config = {}  # branch -> ParsedConfig


class ProjectPipelineConfig(ConfigObject):
    # Represents a project cofiguration in the context of a pipeline
    def __init__(self):
        super(ProjectPipelineConfig, self).__init__()
        self.job_list = JobList()
        self.queue_name = None
        self.debug = False
        self.debug_messages = []
        self.variables = {}

    def addDebug(self, msg):
        self.debug_messages.append(msg)

    def update(self, other):
        if not isinstance(other, ProjectPipelineConfig):
            raise Exception("Unable to update from %s" % (other,))
        if self.queue_name is None:
            self.queue_name = other.queue_name
        if other.debug:
            self.debug = other.debug
        self.job_list.inheritFrom(other.job_list)

    def updateVariables(self, other):
        # We need to keep this separate to update() because we wish to
        # apply the project variables all the time, even if its jobs
        # only come from templates.
        self.variables = Job._deepUpdate(self.variables, other)

    def toDict(self):
        d = {}
        d['queue_name'] = self.queue_name
        return d


class ProjectConfig(ConfigObject):
    # Represents a project configuration
    def __init__(self, name):
        super(ProjectConfig, self).__init__()
        self.name = name
        self.templates = []
        # Pipeline name -> ProjectPipelineConfig
        self.pipelines = {}
        self.branch_matcher = None
        self.variables = {}
        # These represent the values from the config file, but should
        # not be used directly; instead, use the ProjectMetadata to
        # find the computed value from across all project config
        # stanzas.
        self.merge_mode = None
        self.default_branch = None

    def __repr__(self):
        return '<ProjectConfig %s source: %s %s>' % (
            self.name, self.source_context, self.branch_matcher)

    def copy(self):
        r = self.__class__(self.name)
        r.source_context = self.source_context
        r.start_mark = self.start_mark
        r.templates = self.templates
        r.pipelines = self.pipelines
        r.branch_matcher = self.branch_matcher
        r.variables = self.variables
        r.merge_mode = self.merge_mode
        r.default_branch = self.default_branch
        return r

    def setImpliedBranchMatchers(self, branches):
        if len(branches) == 0:
            self.branch_matcher = None
        elif len(branches) > 1:
            matchers = [change_matcher.ImpliedBranchMatcher(branch)
                        for branch in branches]
            self.branch_matcher = change_matcher.MatchAny(matchers)
        else:
            self.branch_matcher = change_matcher.ImpliedBranchMatcher(
                branches[0])

    def changeMatches(self, change):
        if self.branch_matcher and not self.branch_matcher.matches(change):
            return False
        return True

    def toDict(self):
        d = {}
        d['default_branch'] = self.default_branch
        if self.merge_mode:
            d['merge_mode'] = list(filter(lambda x: x[1] == self.merge_mode,
                                          MERGER_MAP.items()))[0][0]
        else:
            d['merge_mode'] = None
        d['templates'] = self.templates
        return d


class ProjectMetadata(object):
    """Information about a Project

    A Layout holds one of these for each project it knows about.
    Information about the project which is synthesized from multiple
    ProjectConfig objects is stored here.
    """

    def __init__(self):
        self.merge_mode = None
        self.default_branch = None


class ConfigItemNotListError(Exception):
    def __init__(self):
        message = textwrap.dedent("""\
        Configuration file is not a list.  Each zuul.yaml configuration
        file must be a list of items, for example:

        - job:
            name: foo

        - project:
            name: bar

        Ensure that every item starts with "- " so that it is parsed as a
        YAML list.
        """)
        super(ConfigItemNotListError, self).__init__(message)


class ConfigItemNotDictError(Exception):
    def __init__(self):
        message = textwrap.dedent("""\
        Configuration item is not a dictionary.  Each zuul.yaml
        configuration file must be a list of dictionaries, for
        example:

        - job:
            name: foo

        - project:
            name: bar

        Ensure that every item in the list is a dictionary with one
        key (in this example, 'job' and 'project').
        """)
        super(ConfigItemNotDictError, self).__init__(message)


class ConfigItemMultipleKeysError(Exception):
    def __init__(self):
        message = textwrap.dedent("""\
        Configuration item has more than one key.  Each zuul.yaml
        configuration file must be a list of dictionaries with a
        single key, for example:

        - job:
            name: foo

        - project:
            name: bar

        Ensure that every item in the list is a dictionary with only
        one key (in this example, 'job' and 'project').  This error
        may be caused by insufficient indentation of the keys under
        the configuration item ('name' in this example).
        """)
        super(ConfigItemMultipleKeysError, self).__init__(message)


class ConfigItemUnknownError(Exception):
    def __init__(self):
        message = textwrap.dedent("""\
        Configuration item not recognized.  Each zuul.yaml
        configuration file must be a list of dictionaries, for
        example:

        - job:
            name: foo

        - project:
            name: bar

        The dictionary keys must match one of the configuration item
        types recognized by zuul (for example, 'job' or 'project').
        """)
        super(ConfigItemUnknownError, self).__init__(message)


class UnparsedAbideConfig(object):

    """A collection of yaml lists that has not yet been parsed into objects.

    An Abide is a collection of tenants.
    """

    def __init__(self):
        self.tenants = []
        self.known_tenants = set()

    def extend(self, conf):
        if isinstance(conf, UnparsedAbideConfig):
            self.tenants.extend(conf.tenants)
            return

        if not isinstance(conf, list):
            raise ConfigItemNotListError()

        for item in conf:
            if not isinstance(item, dict):
                raise ConfigItemNotDictError()
            if len(item.keys()) > 1:
                raise ConfigItemMultipleKeysError()
            key, value = list(item.items())[0]
            if key == 'tenant':
                self.tenants.append(value)
                if 'name' in value:
                    self.known_tenants.add(value['name'])
            else:
                raise ConfigItemUnknownError()


class UnparsedConfig(object):
    """A collection of yaml lists that has not yet been parsed into objects."""

    def __init__(self):
        self.pragmas = []
        self.pipelines = []
        self.jobs = []
        self.project_templates = []
        self.projects = []
        self.nodesets = []
        self.secrets = []
        self.semaphores = []

    def copy(self, trusted=None):
        # If trusted is not None, update the source context of each
        # object in the copy.
        r = UnparsedConfig()
        # Keep a cache of all the source contexts indexed by
        # project-branch-path so that we can share them across objects
        source_contexts = {}
        for attr in ['pragmas', 'pipelines', 'jobs', 'project_templates',
                     'projects', 'nodesets', 'secrets', 'semaphores']:
            # Make a deep copy of each of our attributes
            old_objlist = getattr(self, attr)
            new_objlist = copy.deepcopy(old_objlist)
            setattr(r, attr, new_objlist)
            for i, new_obj in enumerate(new_objlist):
                old_obj = old_objlist[i]
                key = (old_obj['_source_context'].project,
                       old_obj['_source_context'].branch,
                       old_obj['_source_context'].path)
                new_sc = source_contexts.get(key)
                if not new_sc:
                    new_sc = new_obj['_source_context']
                    if trusted is not None:
                        new_sc.trusted = trusted
                    source_contexts[key] = new_sc
                else:
                    new_obj['_source_context'] = new_sc
        return r

    def extend(self, conf):
        if isinstance(conf, UnparsedConfig):
            self.pragmas.extend(conf.pragmas)
            self.pipelines.extend(conf.pipelines)
            self.jobs.extend(conf.jobs)
            self.project_templates.extend(conf.project_templates)
            self.projects.extend(conf.projects)
            self.nodesets.extend(conf.nodesets)
            self.secrets.extend(conf.secrets)
            self.semaphores.extend(conf.semaphores)
            return

        if not isinstance(conf, list):
            raise ConfigItemNotListError()

        for item in conf:
            if not isinstance(item, dict):
                raise ConfigItemNotDictError()
            if len(item.keys()) > 1:
                raise ConfigItemMultipleKeysError()
            key, value = list(item.items())[0]
            if key == 'project':
                self.projects.append(value)
            elif key == 'job':
                self.jobs.append(value)
            elif key == 'project-template':
                self.project_templates.append(value)
            elif key == 'pipeline':
                self.pipelines.append(value)
            elif key == 'nodeset':
                self.nodesets.append(value)
            elif key == 'secret':
                self.secrets.append(value)
            elif key == 'semaphore':
                self.semaphores.append(value)
            elif key == 'pragma':
                self.pragmas.append(value)
            else:
                raise ConfigItemUnknownError()


class ParsedConfig(object):
    """A collection of parsed config objects."""

    def __init__(self):
        self.pragmas = []
        self.pipelines = []
        self.jobs = []
        self.project_templates = []
        self.projects = []
        self.projects_by_regex = {}
        self.nodesets = []
        self.secrets = []
        self.semaphores = []

    def copy(self):
        r = ParsedConfig()
        r.pragmas = self.pragmas[:]
        r.pipelines = self.pipelines[:]
        r.jobs = self.jobs[:]
        r.project_templates = self.project_templates[:]
        r.projects = self.projects[:]
        r.projects_by_regex = copy.copy(self.projects_by_regex)
        r.nodesets = self.nodesets[:]
        r.secrets = self.secrets[:]
        r.semaphores = self.semaphores[:]
        return r

    def extend(self, conf):
        if isinstance(conf, ParsedConfig):
            self.pragmas.extend(conf.pragmas)
            self.pipelines.extend(conf.pipelines)
            self.jobs.extend(conf.jobs)
            self.project_templates.extend(conf.project_templates)
            self.projects.extend(conf.projects)
            self.nodesets.extend(conf.nodesets)
            self.secrets.extend(conf.secrets)
            self.semaphores.extend(conf.semaphores)
            for regex, projects in conf.projects_by_regex.items():
                self.projects_by_regex.setdefault(regex, []).extend(projects)
            return
        else:
            raise ConfigItemUnknownError()


class Layout(object):
    """Holds all of the Pipelines."""

    log = logging.getLogger("zuul.layout")

    def __init__(self, tenant):
        self.uuid = uuid4().hex
        self.tenant = tenant
        self.project_configs = {}
        self.project_templates = {}
        self.project_metadata = {}
        self.pipelines = OrderedDict()
        # This is a dictionary of name -> [jobs].  The first element
        # of the list is the first job added with that name.  It is
        # the reference definition for a given job.  Subsequent
        # elements are aspects of that job with different matchers
        # that override some attribute of the job.  These aspects all
        # inherit from the reference definition.
        noop = Job('noop')
        noop.description = 'A job that will always succeed, no operation.'
        noop.parent = noop.BASE_JOB_MARKER
        noop.run = (PlaybookContext(None, 'noop.yaml', [], []),)
        self.jobs = {'noop': [noop]}
        self.nodesets = {}
        self.secrets = {}
        self.semaphores = {}
        self.loading_errors = LoadingErrors()

    def getJob(self, name):
        if name in self.jobs:
            return self.jobs[name][0]
        raise Exception("Job %s not defined" % (name,))

    def hasJob(self, name):
        return name in self.jobs

    def getJobs(self, name):
        return self.jobs.get(name, [])

    def addJob(self, job):
        # We can have multiple variants of a job all with the same
        # name, but these variants must all be defined in the same repo.
        prior_jobs = [j for j in self.getJobs(job.name) if
                      j.source_context.project !=
                      job.source_context.project]
        # Unless the repo is permitted to shadow another.  If so, and
        # the job we are adding is from a repo that is permitted to
        # shadow the one with the older jobs, skip adding this job.
        job_project = job.source_context.project
        job_tpc = self.tenant.project_configs[job_project.canonical_name]
        skip_add = False
        for prior_job in prior_jobs[:]:
            prior_project = prior_job.source_context.project
            if prior_project in job_tpc.shadow_projects:
                prior_jobs.remove(prior_job)
                skip_add = True

        if prior_jobs:
            raise Exception("Job %s in %s is not permitted to shadow "
                            "job %s in %s" % (
                                job,
                                job.source_context.project,
                                prior_jobs[0],
                                prior_jobs[0].source_context.project))
        if skip_add:
            return False
        if job.name in self.jobs:
            self.jobs[job.name].append(job)
        else:
            self.jobs[job.name] = [job]
        return True

    def addNodeSet(self, nodeset):
        # It's ok to have a duplicate nodeset definition, but only if
        # they are in different branches of the same repo, and have
        # the same values.
        other = self.nodesets.get(nodeset.name)
        if other is not None:
            if not nodeset.source_context.isSameProject(other.source_context):
                raise Exception("Nodeset %s already defined in project %s" %
                                (nodeset.name, other.source_context.project))
            if nodeset.source_context.branch == other.source_context.branch:
                raise Exception("Nodeset %s already defined" % (nodeset.name,))
            if nodeset != other:
                raise Exception("Nodeset %s does not match existing definition"
                                " in branch %s" %
                                (nodeset.name, other.source_context.branch))
            # Identical data in a different branch of the same project;
            # ignore the duplicate definition
            return
        self.nodesets[nodeset.name] = nodeset

    def addSecret(self, secret):
        # It's ok to have a duplicate secret definition, but only if
        # they are in different branches of the same repo, and have
        # the same values.
        other = self.secrets.get(secret.name)
        if other is not None:
            if not secret.source_context.isSameProject(other.source_context):
                raise Exception("Secret %s already defined in project %s" %
                                (secret.name, other.source_context.project))
            if secret.source_context.branch == other.source_context.branch:
                raise Exception("Secret %s already defined" % (secret.name,))
            if not secret.areDataEqual(other):
                raise Exception("Secret %s does not match existing definition"
                                " in branch %s" %
                                (secret.name, other.source_context.branch))
            # Identical data in a different branch of the same project;
            # ignore the duplicate definition
            return
        self.secrets[secret.name] = secret

    def addSemaphore(self, semaphore):
        # It's ok to have a duplicate semaphore definition, but only if
        # they are in different branches of the same repo, and have
        # the same values.
        other = self.semaphores.get(semaphore.name)
        if other is not None:
            if not semaphore.source_context.isSameProject(
                    other.source_context):
                raise Exception("Semaphore %s already defined in project %s" %
                                (semaphore.name, other.source_context.project))
            if semaphore.source_context.branch == other.source_context.branch:
                raise Exception("Semaphore %s already defined" %
                                (semaphore.name,))
            if semaphore != other:
                raise Exception("Semaphore %s does not match existing"
                                " definition in branch %s" %
                                (semaphore.name, other.source_context.branch))
            # Identical data in a different branch of the same project;
            # ignore the duplicate definition
            return
        self.semaphores[semaphore.name] = semaphore

    def addPipeline(self, pipeline):
        if pipeline.tenant is not self.tenant:
            raise Exception("Pipeline created for tenant %s "
                            "may not be added to %s" % (
                                pipeline.tenant,
                                self.tenant))
        self.pipelines[pipeline.name] = pipeline

    def addProjectTemplate(self, project_template):
        template_list = self.project_templates.get(project_template.name)
        if template_list is not None:
            reference = template_list[0]
            if (reference.source_context.project !=
                project_template.source_context.project):
                raise Exception("Project template %s is already defined" %
                                (project_template.name,))
        else:
            template_list = self.project_templates.setdefault(
                project_template.name, [])
        template_list.append(project_template)

    def getProjectTemplates(self, name):
        pt = self.project_templates.get(name, None)
        if pt is None:
            raise TemplateNotFoundError("Project template %s not found" % name)
        return pt

    def addProjectConfig(self, project_config):
        if project_config.name in self.project_configs:
            self.project_configs[project_config.name].append(project_config)
        else:
            self.project_configs[project_config.name] = [project_config]
            self.project_metadata[project_config.name] = ProjectMetadata()
        md = self.project_metadata[project_config.name]
        if md.merge_mode is None and project_config.merge_mode is not None:
            md.merge_mode = project_config.merge_mode
        if (md.default_branch is None and
            project_config.default_branch is not None):
            md.default_branch = project_config.default_branch

    def getProjectConfigs(self, name):
        return self.project_configs.get(name, [])

    def getAllProjectConfigs(self, name):
        # Get all the project configs (project and project-template
        # stanzas) for a project.
        try:
            ret = []
            for pc in self.getProjectConfigs(name):
                ret.append(pc)
                for template_name in pc.templates:
                    templates = self.getProjectTemplates(template_name)
                    ret.extend(templates)
            return ret
        except TemplateNotFoundError as e:
            self.log.warning("%s for project %s" % (e, name))
            return []

    def getProjectMetadata(self, name):
        if name in self.project_metadata:
            return self.project_metadata[name]
        return None

    def getProjectPipelineConfig(self, item):
        # Create a project-pipeline config for the given item, taking
        # its branch (if any) into consideration.  If the project does
        # not participate in the pipeline at all (in this branch),
        # return None.

        # A pc for a project can appear only in a config-project
        # (unbranched, always applies), or in the project itself (it
        # should have an implied branch matcher and it must match the
        # item).
        ppc = ProjectPipelineConfig()
        project_in_pipeline = False
        for pc in self.getProjectConfigs(item.change.project.canonical_name):
            if not pc.changeMatches(item.change):
                msg = "Project %s did not match" % (pc,)
                ppc.addDebug(msg)
                self.log.debug("%s item %s" % (msg, item))
                continue
            msg = "Project %s matched" % (pc,)
            ppc.addDebug(msg)
            self.log.debug("%s item %s" % (msg, item))
            for template_name in pc.templates:
                templates = self.getProjectTemplates(template_name)
                for template in templates:
                    template_ppc = template.pipelines.get(item.pipeline.name)
                    if template_ppc:
                        if not template.changeMatches(item.change):
                            msg = "Project template %s did not match" % (
                                template,)
                            ppc.addDebug(msg)
                            self.log.debug("%s item %s" % (msg, item))
                            continue
                        msg = "Project template %s matched" % (
                            template,)
                        ppc.addDebug(msg)
                        self.log.debug("%s item %s" % (msg, item))
                        project_in_pipeline = True
                        ppc.update(template_ppc)
                        ppc.updateVariables(template.variables)

            # Now merge in project variables (they will override
            # template variables; later job variables may override
            # these again)
            ppc.updateVariables(pc.variables)

            project_ppc = pc.pipelines.get(item.pipeline.name)
            if project_ppc:
                project_in_pipeline = True
                ppc.update(project_ppc)
        if project_in_pipeline:
            return ppc
        return None

    def _updateOverrideCheckouts(self, override_checkouts, job):
        # Update the values in an override_checkouts dict with those
        # in a job.  Used in collectJobVariants.
        if job.override_checkout:
            override_checkouts[None] = job.override_checkout
        for req in job.required_projects.values():
            if req.override_checkout:
                override_checkouts[req.project_name] = req.override_checkout

    def _collectJobVariants(self, item, jobname, change, path, jobs, stack,
                            override_checkouts, indent):
        matched = False
        local_override_checkouts = override_checkouts.copy()
        override_branch = None
        project = None
        for variant in self.getJobs(jobname):
            if project is None and variant.source_context:
                project = variant.source_context.project
                if override_checkouts.get(None) is not None:
                    override_branch = override_checkouts.get(None)
                override_branch = override_checkouts.get(
                    project.canonical_name, override_branch)
                branches = self.tenant.getProjectBranches(project)
                if override_branch not in branches:
                    override_branch = None
            if not variant.changeMatchesBranch(
                    change,
                    override_branch=override_branch):
                self.log.debug("Variant %s did not match %s", repr(variant),
                               change)
                item.debug("Variant {variant} did not match".format(
                    variant=repr(variant)), indent=indent)
                continue
            else:
                self.log.debug("Variant %s matched %s", repr(variant), change)
                item.debug("Variant {variant} matched".format(
                    variant=repr(variant)), indent=indent)
            if not variant.isBase():
                parent = variant.parent
                if not jobs and parent is None:
                    parent = self.tenant.default_base_job
            else:
                parent = None
            self._updateOverrideCheckouts(local_override_checkouts, variant)
            if parent and parent not in path:
                if parent in stack:
                    raise Exception("Dependency cycle in jobs: %s" % stack)
                self.collectJobs(item, parent, change, path, jobs,
                                 stack + [jobname], local_override_checkouts)
            matched = True
            if variant not in jobs:
                jobs.append(variant)
        return matched

    def collectJobs(self, item, jobname, change, path=None, jobs=None,
                    stack=None, override_checkouts=None):
        # Stack is the recursion stack of job parent names.  Each time
        # we go up a level, we add to stack, and it's popped as we
        # descend.
        if stack is None:
            stack = []
        # Jobs is the list of jobs we've accumulated.
        if jobs is None:
            jobs = []
        # Path is the list of job names we've examined.  It
        # accumulates and never reduces.  If more than one job has the
        # same parent, this will prevent us from adding it a second
        # time.
        if path is None:
            path = []
        # Override_checkouts is a dictionary of canonical project
        # names -> branch names.  It is not mutated, but instead new
        # copies are made and updated as we ascend the hierarchy, so
        # higher levels don't affect lower levels after we descend.
        # It's used to override the branch matchers for jobs.
        if override_checkouts is None:
            override_checkouts = {}
        path.append(jobname)
        matched = False
        indent = len(path) + 1
        msg = "Collecting job variants for {jobname}".format(jobname=jobname)
        self.log.debug(msg)
        item.debug(msg, indent=indent)
        matched = self._collectJobVariants(
            item, jobname, change, path, jobs, stack, override_checkouts,
            indent)
        if not matched:
            self.log.debug("No matching parents for job %s and change %s",
                           jobname, change)
            item.debug("No matching parents for {jobname}".format(
                jobname=repr(jobname)), indent=indent)
            raise NoMatchingParentError()
        return jobs

    def _createJobGraph(self, item, ppc, job_graph):
        job_list = ppc.job_list
        change = item.change
        pipeline = item.pipeline
        item.debug("Freezing job graph")
        for jobname in job_list.jobs:
            # This is the final job we are constructing
            frozen_job = None
            self.log.debug("Collecting jobs %s for %s", jobname, change)
            item.debug("Freezing job {jobname}".format(
                jobname=jobname), indent=1)
            # Create the initial list of override_checkouts, which are
            # used as we walk up the hierarchy to expand the set of
            # jobs which match.
            override_checkouts = {}
            for variant in job_list.jobs[jobname]:
                if variant.changeMatchesBranch(change):
                    self._updateOverrideCheckouts(override_checkouts, variant)
            try:
                variants = self.collectJobs(
                    item, jobname, change,
                    override_checkouts=override_checkouts)
            except NoMatchingParentError:
                variants = None
            self.log.debug("Collected jobs %s for %s", jobname, change)
            if not variants:
                # A change must match at least one defined job variant
                # (that is to say that it must match more than just
                # the job that is defined in the tree).
                item.debug("No matching variants for {jobname}".format(
                    jobname=jobname), indent=2)
                continue
            for variant in variants:
                if frozen_job is None:
                    frozen_job = variant.copy()
                    frozen_job.setBase(item.layout)
                else:
                    frozen_job.applyVariant(variant, item.layout)
                    frozen_job.name = variant.name
            frozen_job.name = jobname
            self.log.debug("Froze job %s for %s", jobname, change)
            # Whether the change matches any of the project pipeline
            # variants
            matched = False
            for variant in job_list.jobs[jobname]:
                if variant.changeMatchesBranch(change):
                    frozen_job.applyVariant(variant, item.layout)
                    matched = True
                    self.log.debug("Pipeline variant %s matched %s",
                                   repr(variant), change)
                    item.debug("Pipeline variant {variant} matched".format(
                        variant=repr(variant)), indent=2)
                else:
                    self.log.debug("Pipeline variant %s did not match %s",
                                   repr(variant), change)
                    item.debug("Pipeline variant {variant} did not match".
                               format(variant=repr(variant)), indent=2)
            if not matched:
                # A change must match at least one project pipeline
                # job variant.
                item.debug("No matching pipeline variants for {jobname}".
                           format(jobname=jobname), indent=2)
                continue
            if not frozen_job.changeMatchesFiles(change):
                self.log.debug("Job %s did not match files in %s",
                               repr(frozen_job), change)
                item.debug("Job {jobname} did not match files".
                           format(jobname=jobname), indent=2)
                continue
            if frozen_job.abstract:
                raise Exception("Job %s is abstract and may not be "
                                "directly run" %
                                (frozen_job.name,))
            if (frozen_job.allowed_projects is not None and
                change.project.name not in frozen_job.allowed_projects):
                raise Exception("Project %s is not allowed to run job %s" %
                                (change.project.name, frozen_job.name))
            if ((not pipeline.post_review) and frozen_job.post_review):
                raise Exception("Pre-review pipeline %s does not allow "
                                "post-review job %s" % (
                                    pipeline.name, frozen_job.name))
            if not frozen_job.run:
                raise Exception("Job %s does not specify a run playbook" % (
                    frozen_job.name,))

            # Now merge variables set from this parent ppc
            # (i.e. project+templates) directly into the job vars
            frozen_job.updateProjectVariables(ppc.variables)

            job_graph.addJob(frozen_job)

    def createJobGraph(self, item, ppc):
        # NOTE(pabelanger): It is possible for a foreign project not to have a
        # configured pipeline, if so return an empty JobGraph.
        ret = JobGraph()
        if ppc:
            self._createJobGraph(item, ppc, ret)
        return ret


class Semaphore(ConfigObject):
    def __init__(self, name, max=1):
        super(Semaphore, self).__init__()
        self.name = name
        self.max = int(max)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __eq__(self, other):
        if not isinstance(other, Semaphore):
            return False
        return (self.name == other.name and
                self.max == other.max)


class SemaphoreHandler(object):
    log = logging.getLogger("zuul.SemaphoreHandler")

    def __init__(self):
        self.semaphores = {}

    def acquire(self, item, job, request_resources):
        """
        Aquires a semaphore for an item job combination. This gets called twice
        during the lifecycle of a job. The first call is before requesting
        build resources. The second call is before running the job. In which
        call we really acquire the semaphore is defined by the job.

        :param item: The item
        :param job: The job
        :param request_resources: True if we want to acquire for the request
                                  resources phase, False if we want to acquire
                                  for the run phase.
        """
        if not job.semaphore:
            return True

        if job.semaphore.resources_first and request_resources:
            # We're currently in the resource request phase and want to get the
            # resources before locking. So we don't need to do anything here.
            return True
        else:
            # As a safety net we want to acuire the semaphore at least in the
            # run phase so don't filter this here as re-acuiring the semaphore
            # is not a problem here if it has been already acquired before in
            # the resources phase.
            pass

        semaphore_key = job.semaphore.name

        m = self.semaphores.get(semaphore_key)
        if not m:
            # The semaphore is not held, acquire it
            self._acquire(semaphore_key, item, job.name)
            return True
        if (item, job.name) in m:
            # This item already holds the semaphore
            return True

        # semaphore is there, check max
        if len(m) < self._max_count(item, job.semaphore.name):
            self._acquire(semaphore_key, item, job.name)
            return True

        return False

    def release(self, item, job):
        if not job.semaphore:
            return

        semaphore_key = job.semaphore.name

        m = self.semaphores.get(semaphore_key)
        if not m:
            # The semaphore is not held, nothing to do
            self.log.error("Semaphore can not be released for %s "
                           "because the semaphore is not held" %
                           item)
            return
        if (item, job.name) in m:
            # This item is a holder of the semaphore
            self._release(semaphore_key, item, job.name)
            return
        self.log.error("Semaphore can not be released for %s "
                       "which does not hold it" % item)

    def _acquire(self, semaphore_key, item, job_name):
        self.log.debug("Semaphore acquire {semaphore}: job {job}, item {item}"
                       .format(semaphore=semaphore_key,
                               job=job_name,
                               item=item))
        if semaphore_key not in self.semaphores:
            self.semaphores[semaphore_key] = []
        self.semaphores[semaphore_key].append((item, job_name))

    def _release(self, semaphore_key, item, job_name):
        self.log.debug("Semaphore release {semaphore}: job {job}, item {item}"
                       .format(semaphore=semaphore_key,
                               job=job_name,
                               item=item))
        sem_item = (item, job_name)
        if sem_item in self.semaphores[semaphore_key]:
            self.semaphores[semaphore_key].remove(sem_item)

        # cleanup if there is no user of the semaphore anymore
        if len(self.semaphores[semaphore_key]) == 0:
            del self.semaphores[semaphore_key]

    @staticmethod
    def _max_count(item, semaphore_name):
        if not item.layout:
            # This should not occur as the layout of the item must already be
            # built when acquiring or releasing a semaphore for a job.
            raise Exception("Item {} has no layout".format(item))

        # find the right semaphore
        default_semaphore = Semaphore(semaphore_name, 1)
        semaphores = item.layout.semaphores
        return semaphores.get(semaphore_name, default_semaphore).max


class Tenant(object):
    def __init__(self, name):
        self.name = name
        self.max_nodes_per_job = 5
        self.max_job_timeout = 10800
        self.exclude_unprotected_branches = False
        self.default_base_job = None
        self.layout = None
        # The unparsed configuration from the main zuul config for
        # this tenant.
        self.unparsed_config = None
        # The list of projects from which we will read full
        # configuration.
        self.config_projects = []
        # The parsed config from those projects.
        self.config_projects_config = None
        # The list of projects from which we will read untrusted
        # in-repo configuration.
        self.untrusted_projects = []
        # The parsed config from those projects.
        self.untrusted_projects_config = None
        self.semaphore_handler = SemaphoreHandler()
        # Metadata about projects for this tenant
        # canonical project name -> TenantProjectConfig
        self.project_configs = {}

        # A mapping of project names to projects.  project_name ->
        # VALUE where VALUE is a further dictionary of
        # canonical_hostname -> Project.
        self.projects = {}
        self.canonical_hostnames = set()

    def _addProject(self, tpc):
        """Add a project to the project index

        :arg TenantProjectConfig tpc: The TenantProjectConfig (with
        associated project) to add.

        """
        project = tpc.project
        self.canonical_hostnames.add(project.canonical_hostname)
        hostname_dict = self.projects.setdefault(project.name, {})
        if project.canonical_hostname in hostname_dict:
            raise Exception("Project %s is already in project index" %
                            (project,))
        hostname_dict[project.canonical_hostname] = project
        self.project_configs[project.canonical_name] = tpc

    def getProject(self, name):
        """Return a project given its name.

        :arg str name: The name of the project.  It may be fully
            qualified (E.g., "git.example.com/subpath/project") or may
            contain only the project name name may be supplied (E.g.,
            "subpath/project").

        :returns: A tuple (trusted, project) or (None, None) if the
            project is not found or ambiguous.  The "trusted" boolean
            indicates whether or not the project is trusted by this
            tenant.
        :rtype: (bool, Project)

        """
        path = name.split('/', 1)
        if path[0] in self.canonical_hostnames:
            hostname = path[0]
            project_name = path[1]
        else:
            hostname = None
            project_name = name
        hostname_dict = self.projects.get(project_name)
        project = None
        if hostname_dict:
            if hostname:
                project = hostname_dict.get(hostname)
            else:
                values = list(hostname_dict.values())
                if len(values) == 1:
                    project = values[0]
                else:
                    raise Exception("Project name '%s' is ambiguous, "
                                    "please fully qualify the project "
                                    "with a hostname" % (name,))
        if project is None:
            return (None, None)
        if project in self.config_projects:
            return (True, project)
        if project in self.untrusted_projects:
            return (False, project)
        # This should never happen:
        raise Exception("Project %s is neither trusted nor untrusted" %
                        (project,))

    def getProjectsByRegex(self, regex):
        """Return all projects with a full match to either project name or
        canonical project name.

        :arg str regex: The regex to match
        :returns: A list of tuples (trusted, project) describing the found
            projects. Raises an exception if the same project name is found
            several times across multiple hostnames.
        """

        matcher = re2.compile(regex)
        projects = []
        result = []

        for name, hostname_dict in self.projects.items():

            if matcher.fullmatch(name):
                # validate that this match is unambiguous
                values = list(hostname_dict.values())
                if len(values) > 1:
                    raise Exception("Project name '%s' is ambiguous, "
                                    "please fully qualify the project "
                                    "with a hostname. Valid hostnames "
                                    "are %s." % (name, hostname_dict.keys()))
                projects.append(values[0])
            else:
                # try to match canonical project names
                for project in hostname_dict.values():
                    if matcher.fullmatch(project.canonical_name):
                        projects.append(project)

        for project in projects:
            if project in self.config_projects:
                result.append((True, project))
            elif project in self.untrusted_projects:
                result.append((False, project))
            else:
                raise Exception("Project %s is neither trusted nor untrusted" %
                                (project,))
        return result

    def getProjectBranches(self, project):
        """Return a project's branches (filtered by this tenant config)

        :arg Project project: The project object.

        :returns: A list of branch names.
        :rtype: [str]

        """
        tpc = self.project_configs[project.canonical_name]
        return tpc.branches

    def getExcludeUnprotectedBranches(self, project):
        # Evaluate if unprotected branches should be excluded or not. The first
        # match wins. The order is project -> tenant (default is false).
        project_config = self.project_configs.get(project.canonical_name)
        if project_config.exclude_unprotected_branches is not None:
            exclude_unprotected = project_config.exclude_unprotected_branches
        else:
            exclude_unprotected = self.exclude_unprotected_branches
        return exclude_unprotected

    def addConfigProject(self, tpc):
        self.config_projects.append(tpc.project)
        self._addProject(tpc)

    def addUntrustedProject(self, tpc):
        self.untrusted_projects.append(tpc.project)
        self._addProject(tpc)

    def getSafeAttributes(self):
        return Attributes(name=self.name)


class Abide(object):
    def __init__(self):
        self.tenants = OrderedDict()
        # project -> branch -> UnparsedConfig
        self.unparsed_project_branch_config = {}

    def cacheUnparsedConfig(self, canonical_project_name, branch, conf):
        self.unparsed_project_branch_config.setdefault(
            canonical_project_name, {})[branch] = conf

    def getUnparsedConfig(self, canonical_project_name, branch):
        return self.unparsed_project_branch_config.get(
            canonical_project_name, {}).get(branch)

    def clearUnparsedConfigCache(self, canonical_project_name, branch=None):
        if canonical_project_name in self.unparsed_project_branch_config:
            project_branch_config = \
                self.unparsed_project_branch_config[canonical_project_name]

            if branch in project_branch_config:
                del project_branch_config[branch]

            if len(project_branch_config) == 0 or branch is None:
                del self.unparsed_project_branch_config[canonical_project_name]


class JobTimeData(object):
    format = 'B10H10H10B'
    version = 0

    def __init__(self, path):
        self.path = path
        self.success_times = [0 for x in range(10)]
        self.failure_times = [0 for x in range(10)]
        self.results = [0 for x in range(10)]

    def load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, 'rb') as f:
            data = struct.unpack(self.format, f.read())
        version = data[0]
        if version != self.version:
            raise Exception("Unkown data version")
        self.success_times = list(data[1:11])
        self.failure_times = list(data[11:21])
        self.results = list(data[21:32])

    def save(self):
        tmpfile = self.path + '.tmp'
        data = [self.version]
        data.extend(self.success_times)
        data.extend(self.failure_times)
        data.extend(self.results)
        data = struct.pack(self.format, *data)
        with open(tmpfile, 'wb') as f:
            f.write(data)
        os.rename(tmpfile, self.path)

    def add(self, elapsed, result):
        elapsed = int(elapsed)
        if result == 'SUCCESS':
            self.success_times.append(elapsed)
            self.success_times.pop(0)
            result = 0
        else:
            self.failure_times.append(elapsed)
            self.failure_times.pop(0)
            result = 1
        self.results.append(result)
        self.results.pop(0)

    def getEstimatedTime(self):
        times = [x for x in self.success_times if x]
        if times:
            return float(sum(times)) / len(times)
        return 0.0


class TimeDataBase(object):
    def __init__(self, root):
        self.root = root

    def _getTD(self, build):
        if hasattr(build.build_set.item.change, 'branch'):
            branch = build.build_set.item.change.branch
        else:
            branch = ''

        dir_path = os.path.join(
            self.root,
            build.build_set.item.pipeline.tenant.name,
            build.build_set.item.change.project.canonical_name,
            branch)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
        path = os.path.join(dir_path, build.job.name)

        td = JobTimeData(path)
        td.load()
        return td

    def getEstimatedTime(self, name):
        return self._getTD(name).getEstimatedTime()

    def update(self, build, elapsed, result):
        td = self._getTD(build)
        td.add(elapsed, result)
        td.save()


class Capabilities(object):
    """The set of capabilities this Zuul installation has.

    Some plugins add elements to the external API. In order to
    facilitate consumers knowing if functionality is available
    or not, keep track of distinct capability flags.
    """
    def __init__(self, job_history=False):
        self.job_history = job_history

    def __repr__(self):
        return '<Capabilities 0x%x %s>' % (id(self), self._renderFlags())

    def _renderFlags(self):
        d = self.toDict()
        return " ".join(['{k}={v}'.format(k=k, v=v) for (k, v) in d.items()])

    def copy(self):
        return Capabilities(**self.toDict())

    def toDict(self):
        d = dict()
        d['job_history'] = self.job_history
        return d


class WebInfo(object):
    """Information about the system needed by zuul-web /info."""

    def __init__(self, websocket_url=None,
                 capabilities=None, stats_url=None,
                 stats_prefix=None, stats_type=None):
        self.capabilities = capabilities or Capabilities()
        self.stats_prefix = stats_prefix
        self.stats_type = stats_type
        self.stats_url = stats_url
        self.tenant = None
        self.websocket_url = websocket_url

    def __repr__(self):
        return '<WebInfo 0x%x capabilities=%s>' % (
            id(self), str(self.capabilities))

    def copy(self):
        return WebInfo(
            capabilities=self.capabilities.copy(),
            stats_prefix=self.stats_prefix,
            stats_type=self.stats_type,
            stats_url=self.stats_url,
            websocket_url=self.websocket_url)

    @staticmethod
    def fromConfig(config):
        return WebInfo(
            stats_prefix=get_default(config, 'statsd', 'prefix'),
            stats_type=get_default(config, 'web', 'stats_type', 'graphite'),
            stats_url=get_default(config, 'web', 'stats_url', None),
            websocket_url=get_default(config, 'web', 'websocket_url', None),
        )

    def toDict(self):
        d = dict()
        d['capabilities'] = self.capabilities.toDict()
        d['websocket_url'] = self.websocket_url
        stats = dict()
        stats['prefix'] = self.stats_prefix
        stats['type'] = self.stats_type
        stats['url'] = self.stats_url
        d['stats'] = stats
        if self.tenant:
            d['tenant'] = self.tenant
        return d
