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
import time

from kazoo.client import KazooClient, KazooState
from kazoo import exceptions as kze
from kazoo.handlers.threading import KazooTimeoutError
from kazoo.recipe.cache import TreeCache, TreeEvent
from kazoo.recipe.lock import Lock

import zuul.model


class LockException(Exception):
    pass


class ZooKeeper(object):
    '''
    Class implementing the ZooKeeper interface.

    This class uses the facade design pattern to keep common interaction
    with the ZooKeeper API simple and consistent for the caller, and
    limits coupling between objects. It allows for more complex interactions
    by providing direct access to the client connection when needed (though
    that is discouraged). It also provides for a convenient entry point for
    testing only ZooKeeper interactions.
    '''

    log = logging.getLogger("zuul.zk.ZooKeeper")

    REQUEST_ROOT = '/nodepool/requests'
    REQUEST_LOCK_ROOT = "/nodepool/requests-lock"
    NODE_ROOT = '/nodepool/nodes'
    HOLD_REQUEST_ROOT = '/zuul/hold-requests'

    # Log zookeeper retry every 10 seconds
    retry_log_rate = 10

    def __init__(self, enable_cache=True):
        '''
        Initialize the ZooKeeper object.

        :param bool enable_cache: When True, enables caching of ZooKeeper
            objects (e.g., HoldRequests).
        '''
        self.client = None
        self._became_lost = False
        self._last_retry_log = 0
        self.enable_cache = enable_cache

        # The caching model we use is designed around handing out model
        # data as objects. To do this, we use two caches: one is a TreeCache
        # which contains raw znode data (among other details), and one for
        # storing that data serialized as objects. This allows us to return
        # objects from the APIs, and avoids calling the methods to serialize
        # the data into objects more than once.
        self._hold_request_tree = None
        self._cached_hold_requests = {}

    def _dictToStr(self, data):
        return json.dumps(data).encode('utf8')

    def _strToDict(self, data):
        return json.loads(data.decode('utf8'))

    def _connection_listener(self, state):
        '''
        Listener method for Kazoo connection state changes.

        .. warning:: This method must not block.
        '''
        if state == KazooState.LOST:
            self.log.debug("ZooKeeper connection: LOST")
            self._became_lost = True
        elif state == KazooState.SUSPENDED:
            self.log.debug("ZooKeeper connection: SUSPENDED")
        else:
            self.log.debug("ZooKeeper connection: CONNECTED")

    @property
    def connected(self):
        return self.client.state == KazooState.CONNECTED

    @property
    def suspended(self):
        return self.client.state == KazooState.SUSPENDED

    @property
    def lost(self):
        return self.client.state == KazooState.LOST

    @property
    def didLoseConnection(self):
        return self._became_lost

    def resetLostFlag(self):
        self._became_lost = False

    def logConnectionRetryEvent(self):
        now = time.monotonic()
        if now - self._last_retry_log >= self.retry_log_rate:
            self.log.warning("Retrying zookeeper connection")
            self._last_retry_log = now

    def connect(self, hosts, read_only=False, timeout=10.0):
        '''
        Establish a connection with ZooKeeper cluster.

        Convenience method if a pre-existing ZooKeeper connection is not
        supplied to the ZooKeeper object at instantiation time.

        :param str hosts: Comma-separated list of hosts to connect to (e.g.
            127.0.0.1:2181,127.0.0.1:2182,[::1]:2183).
        :param bool read_only: If True, establishes a read-only connection.
        :param float timeout: The ZooKeeper session timeout, in
            seconds (default: 10.0).
        '''
        if self.client is None:
            self.client = KazooClient(hosts=hosts, read_only=read_only,
                                      timeout=timeout)
            self.client.add_listener(self._connection_listener)
            # Manually retry initial connection attempt
            while True:
                try:
                    self.client.start(1)
                    break
                except KazooTimeoutError:
                    self.logConnectionRetryEvent()

        if self.enable_cache:
            self._hold_request_tree = TreeCache(self.client,
                                                self.HOLD_REQUEST_ROOT)
            self._hold_request_tree.listen_fault(self.cacheFaultListener)
            self._hold_request_tree.listen(self.holdRequestCacheListener)
            self._hold_request_tree.start()

    def cacheFaultListener(self, e):
        self.log.exception(e)

    def holdRequestCacheListener(self, event):
        '''
        Keep the hold request object cache in sync with the TreeCache.
        '''
        try:
            self._holdRequestCacheListener(event)
        except Exception:
            self.log.exception(
                "Exception in hold request cache update for event: %s", event)

    def _holdRequestCacheListener(self, event):
        if hasattr(event.event_data, 'path'):
            # Ignore root node
            path = event.event_data.path
            if path == self.HOLD_REQUEST_ROOT:
                return

        if event.event_type not in (TreeEvent.NODE_ADDED,
                                    TreeEvent.NODE_UPDATED,
                                    TreeEvent.NODE_REMOVED):
            return

        path = event.event_data.path
        request_id = path.rsplit('/', 1)[1]

        if event.event_type in (TreeEvent.NODE_ADDED, TreeEvent.NODE_UPDATED):
            # Requests with no data are invalid
            if not event.event_data.data:
                return

            # Perform an in-place update of the already cached request
            d = self._bytesToDict(event.event_data.data)
            old_request = self._cached_hold_requests.get(request_id)
            if old_request:
                if event.event_data.stat.version <= old_request.stat.version:
                    # Don't update to older data
                    return
                old_request.updateFromDict(d)
                old_request.stat = event.event_data.stat
            else:
                request = zuul.model.HoldRequest.fromDict(d)
                request.id = request_id
                request.stat = event.event_data.stat
                self._cached_hold_requests[request_id] = request

        elif event.event_type == TreeEvent.NODE_REMOVED:
            try:
                del self._cached_hold_requests[request_id]
            except KeyError:
                pass

    def disconnect(self):
        '''
        Close the ZooKeeper cluster connection.

        You should call this method if you used connect() to establish a
        cluster connection.
        '''
        if self._hold_request_tree is not None:
            self._hold_request_tree.close()
            self._hold_request_tree = None

        if self.client is not None and self.client.connected:
            self.client.stop()
            self.client.close()
            self.client = None

    def resetHosts(self, hosts):
        '''
        Reset the ZooKeeper cluster connection host list.

        :param str hosts: Comma-separated list of hosts to connect to (e.g.
            127.0.0.1:2181,127.0.0.1:2182,[::1]:2183).
        '''
        if self.client is not None:
            self.client.set_hosts(hosts=hosts)

    def submitNodeRequest(self, node_request, watcher):
        '''
        Submit a request for nodes to Nodepool.

        :param NodeRequest node_request: A NodeRequest with the
            contents of the request.

        :param callable watcher: A callable object that will be
            invoked each time the request is updated.  It is called
            with two arguments: (node_request, deleted) where
            node_request is the same argument passed to this method,
            and deleted is a boolean which is True if the node no
            longer exists (notably, this will happen on disconnection
            from ZooKeeper).  The watcher should return False when
            further updates are no longer necessary.
        '''
        node_request.created_time = time.time()
        data = node_request.toDict()

        path = '{}/{:0>3}-'.format(self.REQUEST_ROOT, node_request.priority)
        path = self.client.create(path, self._dictToStr(data),
                                  makepath=True,
                                  sequence=True, ephemeral=True)
        reqid = path.split("/")[-1]
        node_request.id = reqid

        def callback(data, stat):
            if data:
                self.updateNodeRequest(node_request, data)
            deleted = (data is None)  # data *are* none
            return watcher(node_request, deleted)

        self.client.DataWatch(path, callback)

    def deleteNodeRequest(self, node_request):
        '''
        Delete a request for nodes.

        :param NodeRequest node_request: A NodeRequest with the
            contents of the request.
        '''

        path = '%s/%s' % (self.REQUEST_ROOT, node_request.id)
        try:
            self.client.delete(path)
        except kze.NoNodeError:
            pass

    def nodeRequestExists(self, node_request):
        '''
        See if a NodeRequest exists in ZooKeeper.

        :param NodeRequest node_request: A NodeRequest to verify.

        :returns: True if the request exists, False otherwise.
        '''
        path = '%s/%s' % (self.REQUEST_ROOT, node_request.id)
        if self.client.exists(path):
            return True
        return False

    def storeNodeRequest(self, node_request):
        '''Store the node request.

        The request is expected to already exist and is updated in its
        entirety.

        :param NodeRequest node_request: The request to update.
        '''

        path = '%s/%s' % (self.REQUEST_ROOT, node_request.id)
        self.client.set(path, self._dictToStr(node_request.toDict()))

    def updateNodeRequest(self, node_request, data=None):
        '''Refresh an existing node request.

        :param NodeRequest node_request: The request to update.
        :param dict data: The data to use; query ZK if absent.
        '''
        if data is None:
            path = '%s/%s' % (self.REQUEST_ROOT, node_request.id)
            data, stat = self.client.get(path)
        data = self._strToDict(data)
        request_nodes = list(node_request.nodeset.getNodes())
        for i, nodeid in enumerate(data.get('nodes', [])):
            request_nodes[i].id = nodeid
            self.updateNode(request_nodes[i])
        node_request.updateFromDict(data)

    def storeNode(self, node):
        '''Store the node.

        The node is expected to already exist and is updated in its
        entirety.

        :param Node node: The node to update.
        '''

        path = '%s/%s' % (self.NODE_ROOT, node.id)
        self.client.set(path, self._dictToStr(node.toDict()))

    def updateNode(self, node):
        '''Refresh an existing node.

        :param Node node: The node to update.
        '''

        node_path = '%s/%s' % (self.NODE_ROOT, node.id)
        node_data, node_stat = self.client.get(node_path)
        node_data = self._strToDict(node_data)
        node.updateFromDict(node_data)

    def lockNode(self, node, blocking=True, timeout=None):
        '''
        Lock a node.

        This should be called as soon as a request is fulfilled and
        the lock held for as long as the node is in-use.  It can be
        used by nodepool to detect if Zuul has gone offline and the
        node should be reclaimed.

        :param Node node: The node which should be locked.
        '''

        lock_path = '%s/%s/lock' % (self.NODE_ROOT, node.id)
        try:
            lock = Lock(self.client, lock_path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise LockException(
                "Timeout trying to acquire lock %s" % lock_path)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise LockException("Did not get lock on %s" % lock_path)

        node.lock = lock

    def unlockNode(self, node):
        '''
        Unlock a node.

        The node must already have been locked.

        :param Node node: The node which should be unlocked.
        '''

        if node.lock is None:
            raise LockException("Node %s does not hold a lock" % (node,))
        node.lock.release()
        node.lock = None

    def lockNodeRequest(self, request, blocking=True, timeout=None):
        '''
        Lock a node request.

        This will set the `lock` attribute of the request object when the
        lock is successfully acquired.

        :param NodeRequest request: The request to lock.
        :param bool blocking: Whether or not to block on trying to
            acquire the lock
        :param int timeout: When blocking, how long to wait for the lock
            to get acquired. None, the default, waits forever.

        :raises: TimeoutException if we failed to acquire the lock when
            blocking with a timeout. ZKLockException if we are not blocking
            and could not get the lock, or a lock is already held.
        '''

        path = "%s/%s" % (self.REQUEST_LOCK_ROOT, request.id)
        try:
            lock = Lock(self.client, path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise LockException(
                "Timeout trying to acquire lock %s" % path)
        except kze.NoNodeError:
            have_lock = False
            self.log.error("Request not found for locking: %s", request)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise LockException("Did not get lock on %s" % path)

        request.lock = lock
        self.updateNodeRequest(request)

    def unlockNodeRequest(self, request):
        '''
        Unlock a node request.

        The request must already have been locked.

        :param NodeRequest request: The request to unlock.

        :raises: ZKLockException if the request is not currently locked.
        '''
        if request.lock is None:
            raise LockException(
                "Request %s does not hold a lock" % request)
        request.lock.release()
        request.lock = None

    def heldNodeCount(self, autohold_key):
        '''
        Count the number of nodes being held for the given tenant/project/job.

        :param set autohold_key: A set with the tenant/project/job names.
        '''
        identifier = " ".join(autohold_key)
        try:
            nodes = self.client.get_children(self.NODE_ROOT)
        except kze.NoNodeError:
            return 0

        count = 0
        for nodeid in nodes:
            node_path = '%s/%s' % (self.NODE_ROOT, nodeid)
            try:
                node_data, node_stat = self.client.get(node_path)
            except kze.NoNodeError:
                # Node got removed on us. Just ignore.
                continue

            if not node_data:
                self.log.warning("Node ID %s has no data", nodeid)
                continue
            node_data = self._strToDict(node_data)
            if (node_data['state'] == zuul.model.STATE_HOLD and
                    node_data.get('hold_job') == identifier):
                count += 1
        return count

    # Copy of nodepool/zk.py begins here
    NODE_ROOT = "/nodepool/nodes"
    LAUNCHER_ROOT = "/nodepool/launchers"

    def _bytesToDict(self, data):
        return json.loads(data.decode('utf8'))

    def _launcherPath(self, launcher):
        return "%s/%s" % (self.LAUNCHER_ROOT, launcher)

    def _nodePath(self, node):
        return "%s/%s" % (self.NODE_ROOT, node)

    def getRegisteredLaunchers(self):
        '''
        Get a list of all launchers that have registered with ZooKeeper.

        :returns: A list of Launcher objects, or empty list if none are found.
        '''
        try:
            launcher_ids = self.client.get_children(self.LAUNCHER_ROOT)
        except kze.NoNodeError:
            return []

        objs = []
        for launcher in launcher_ids:
            path = self._launcherPath(launcher)
            try:
                data, _ = self.client.get(path)
            except kze.NoNodeError:
                # launcher disappeared
                continue

            objs.append(Launcher.fromDict(self._bytesToDict(data)))
        return objs

    def getNodes(self):
        '''
        Get the current list of all nodes.

        :returns: A list of nodes.
        '''
        try:
            return self.client.get_children(self.NODE_ROOT)
        except kze.NoNodeError:
            return []

    def getNode(self, node):
        '''
        Get the data for a specific node.

        :param str node: The node ID.

        :returns: The node data, or None if the node was not found.
        '''
        path = self._nodePath(node)
        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None
        if not data:
            return None

        d = self._bytesToDict(data)
        d['id'] = node
        return d

    def nodeIterator(self):
        '''
        Utility generator method for iterating through all nodes.
        '''
        for node_id in self.getNodes():
            node = self.getNode(node_id)
            if node:
                yield node

    def getHoldRequests(self):
        '''
        Get the current list of all hold requests.
        '''
        try:
            return sorted(self.client.get_children(self.HOLD_REQUEST_ROOT))
        except kze.NoNodeError:
            return []

    def getHoldRequest(self, hold_request_id):
        path = self.HOLD_REQUEST_ROOT + "/" + hold_request_id
        try:
            data, stat = self.client.get(path)
        except kze.NoNodeError:
            return None
        if not data:
            return None

        obj = zuul.model.HoldRequest.fromDict(self._strToDict(data))
        obj.id = hold_request_id
        obj.stat = stat
        return obj

    def storeHoldRequest(self, hold_request):
        '''
        Create or update a hold request.

        If this is a new request with no value for the `id` attribute of the
        passed in request, then `id` will be set with the unique request
        identifier after successful creation.

        :param HoldRequest hold_request: Object representing the hold request.
        '''
        if hold_request.id is None:
            path = self.client.create(
                self.HOLD_REQUEST_ROOT + "/",
                value=hold_request.serialize(),
                sequence=True,
                makepath=True)
            hold_request.id = path.split('/')[-1]
        else:
            path = self.HOLD_REQUEST_ROOT + "/" + hold_request.id
            self.client.set(path, hold_request.serialize())

    def _markHeldNodesAsUsed(self, hold_request):
        '''
        Changes the state for each held node for the hold request to 'used'.

        :returns: True if all nodes marked USED, False otherwise.
        '''
        def getHeldNodeIDs(request):
            node_ids = []
            for data in request.nodes:
                # TODO(Shrews): Remove type check at some point.
                # When autoholds were initially changed to be stored in ZK,
                # the node IDs were originally stored as a list of strings.
                # A later change embedded them within a dict. Handle both
                # cases here to deal with the upgrade.
                if isinstance(data, dict):
                    node_ids += data['nodes']
                else:
                    node_ids.append(data)
            return node_ids

        failure = False
        for node_id in getHeldNodeIDs(hold_request):
            node = self.getNode(node_id)
            if not node or node['state'] == zuul.model.STATE_USED:
                continue

            node['state'] = zuul.model.STATE_USED

            name = None
            label = None
            if 'name' in node:
                name = node['name']
            if 'label' in node:
                label = node['label']

            node_obj = zuul.model.Node(name, label)
            node_obj.updateFromDict(node)

            try:
                self.lockNode(node_obj, blocking=False)
                self.storeNode(node_obj)
            except Exception:
                self.log.exception("Cannot change HELD node state to USED "
                                   "for node %s in request %s",
                                   node_obj.id, hold_request.id)
                failure = True
            finally:
                try:
                    if node_obj.lock:
                        self.unlockNode(node_obj)
                except Exception:
                    self.log.exception(
                        "Failed to unlock HELD node %s for request %s",
                        node_obj.id, hold_request.id)

        return not failure

    def deleteHoldRequest(self, hold_request):
        '''
        Delete a hold request.

        :param HoldRequest hold_request: Object representing the hold request.
        '''
        if not self._markHeldNodesAsUsed(hold_request):
            self.log.info("Unable to delete hold request %s because "
                          "not all nodes marked as USED.", hold_request.id)
            return

        path = self.HOLD_REQUEST_ROOT + "/" + hold_request.id
        try:
            self.client.delete(path, recursive=True)
        except kze.NoNodeError:
            pass

    def lockHoldRequest(self, request, blocking=True, timeout=None):
        '''
        Lock a node request.

        This will set the `lock` attribute of the request object when the
        lock is successfully acquired.

        :param HoldRequest request: The hold request to lock.
        '''
        if not request.id:
            raise LockException(
                "Hold request without an ID cannot be locked: %s" % request)

        path = "%s/%s/lock" % (self.HOLD_REQUEST_ROOT, request.id)
        try:
            lock = Lock(self.client, path)
            have_lock = lock.acquire(blocking, timeout)
        except kze.LockTimeout:
            raise LockException(
                "Timeout trying to acquire lock %s" % path)

        # If we aren't blocking, it's possible we didn't get the lock
        # because someone else has it.
        if not have_lock:
            raise LockException("Did not get lock on %s" % path)

        request.lock = lock

    def unlockHoldRequest(self, request):
        '''
        Unlock a hold request.

        The request must already have been locked.

        :param HoldRequest request: The request to unlock.

        :raises: ZKLockException if the request is not currently locked.
        '''
        if request.lock is None:
            raise LockException(
                "Request %s does not hold a lock" % request)
        request.lock.release()
        request.lock = None


class Launcher():
    '''
    Class to describe a nodepool launcher.
    '''

    def __init__(self):
        self.id = None
        self._supported_labels = set()

    def __eq__(self, other):
        if isinstance(other, Launcher):
            return (self.id == other.id and
                    self.supported_labels == other.supported_labels)
        else:
            return False

    @property
    def supported_labels(self):
        return self._supported_labels

    @supported_labels.setter
    def supported_labels(self, value):
        if not isinstance(value, set):
            raise TypeError("'supported_labels' attribute must be a set")
        self._supported_labels = value

    @staticmethod
    def fromDict(d):
        obj = Launcher()
        obj.id = d.get('id')
        obj.supported_labels = set(d.get('supported_labels', []))
        return obj
