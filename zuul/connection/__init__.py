# Copyright 2014 Rackspace Australia
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

import six


@six.add_metaclass(abc.ABCMeta)
class BaseConnection(object):
    """Base class for connections.

    A connection is a shared object that sources, triggers and reporters can
    use to speak with a remote API without needing to establish a new
    connection each time or without having to authenticate each time.

    Multiple instances of the same connection may exist with different
    credentials, for example, thus allowing for different pipelines to operate
    on different Gerrit installations or post back as a different user etc.

    Connections can implement their own public methods. Required connection
    methods are validated by the {trigger, source, reporter} they are loaded
    into. For example, a trigger will likely require some kind of query method
    while a reporter may need a review method."""

    def __init__(self, connection_name, connection_config):
        # connection_name is the name given to this connection in zuul.ini
        # connection_config is a dictionary of config_section from zuul.ini for
        # this connection.
        # __init__ shouldn't make the actual connection in case this connection
        # isn't used in the layout.
        self.connection_name = connection_name
        self.connection_config = connection_config

        # Keep track of the sources, triggers and reporters using this
        # connection
        self.attached_to = {
            'source': [],
            'trigger': [],
            'reporter': [],
        }

    def onLoad(self):
        pass

    def onStop(self):
        pass

    def registerScheduler(self, sched):
        self.sched = sched

    def registerUse(self, what, instance):
        self.attached_to[what].append(instance)

    def maintainCache(self, relevant):
        """Make cache contain relevant changes.

        This lets the user supply a list of change objects that are
        still in use.  Anything in our cache that isn't in the supplied
        list should be safe to remove from the cache."""
