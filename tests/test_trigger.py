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

import logging
import testtools

import zuul.connection.gerrit
import zuul.connection.smtp

import zuul.trigger
import zuul.trigger.gerrit
import zuul.trigger.timer
import zuul.trigger.zuultrigger


def gerrit_conn():
    return zuul.connection.gerrit.GerritConnection(
        'review.example.org',
        {'server': 'review.example.org', 'user': 'zuul'})


class TestGerritTrigger(testtools.TestCase):
    log = logging.getLogger("zuul.test_trigger")

    def test_trigger_abc(self):
        # We only need to instantiate a class for this
        zuul.trigger.gerrit.GerritTrigger({})

    def test_trigger_name(self):
        self.assertEqual('gerrit', zuul.trigger.gerrit.GerritTrigger.name)

    def test_repr(self):
        self.assertEqual(
            '<GerritTrigger connection: gerrit://review.example.org>',
            repr(zuul.trigger.gerrit.GerritTrigger(connection=gerrit_conn())))


class TestTimerTrigger(testtools.TestCase):
    log = logging.getLogger("zuul.test_trigger")

    def test_trigger_abc(self):
        # We only need to instantiate a class for this
        zuul.trigger.timer.TimerTrigger({})

    def test_trigger_name(self):
        self.assertEqual('timer', zuul.trigger.timer.TimerTrigger.name)

    def test_repr(self):
        self.assertEqual(
            '<TimerTrigger connection: gerrit://review.example.org>',
            repr(zuul.trigger.timer.TimerTrigger(connection=gerrit_conn())))


class TestZuulTrigger(testtools.TestCase):
    log = logging.getLogger("zuul.test_trigger")

    def test_trigger_abc(self):
        # We only need to instantiate a class for this
        zuul.trigger.zuultrigger.ZuulTrigger({})

    def test_trigger_name(self):
        self.assertEqual('zuul', zuul.trigger.zuultrigger.ZuulTrigger.name)

    def test_repr(self):
        self.assertEqual(
            '<ZuulTrigger connection: None>',
            repr(zuul.trigger.zuultrigger.ZuulTrigger({})))
