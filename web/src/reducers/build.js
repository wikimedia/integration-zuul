// Copyright 2018 Red Hat, Inc
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may
// not use this file except in compliance with the License. You may obtain
// a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
// License for the specific language governing permissions and limitations
// under the License.

import update from 'immutability-helper'

import {
  BUILD_FETCH_FAIL,
  BUILD_FETCH_REQUEST,
  BUILD_FETCH_SUCCESS,

  BUILDSET_FETCH_FAIL,
  BUILDSET_FETCH_REQUEST,
  BUILDSET_FETCH_SUCCESS,

  BUILD_OUTPUT_FAIL,
  BUILD_OUTPUT_REQUEST,
  BUILD_OUTPUT_SUCCESS,

  BUILD_MANIFEST_FAIL,
  BUILD_MANIFEST_REQUEST,
  BUILD_MANIFEST_SUCCESS,
} from '../actions/build'


export default (state = {
  isFetching: false,
  isFetchingOutput: false,
  isFetchingManifest: false,
  builds: {},
  buildsets: {},
}, action) => {
  switch (action.type) {
  case BUILD_FETCH_REQUEST:
  case BUILDSET_FETCH_REQUEST:
    return update(state, {$merge: {isFetching: true}})
  case BUILD_FETCH_SUCCESS:
    state.builds = update(
      state.builds, {$merge: {[action.buildId]: action.build}})
    return update(state, {$merge: {isFetching: false}})
  case BUILDSET_FETCH_SUCCESS:
    return update(state, {$merge: {
      isFetching: false,
      buildsets: update(state.buildsets, {$merge: {
        [action.buildsetId]: action.buildset}})
    }})
  case BUILD_FETCH_FAIL:
  case BUILDSET_FETCH_FAIL:
    return update(state, {$merge: {isFetching: false}})

  case BUILD_OUTPUT_REQUEST:
    return update(state, {$merge: {isFetchingOutput: true}})
  case BUILD_OUTPUT_SUCCESS:
    state.builds = update(
      state.builds, {[action.buildId]: {$merge: {errorIds: action.errorIds,
                                                 hosts: action.hosts,
                                                 output: action.output}}})
    return update(state, {$merge: {isFetchingOutput: false}})
  case BUILD_OUTPUT_FAIL:
    return update(state, {$merge: {isFetchingOutput: false}})

  case BUILD_MANIFEST_REQUEST:
    return update(state, {$merge: {isFetchingManifest: true}})
  case BUILD_MANIFEST_SUCCESS:
    state.builds = update(
      state.builds, {[action.buildId]: {$merge: {manifest: action.manifest}}})
    return update(state, {$merge: {isFetchingManifest: false}})
  case BUILD_MANIFEST_FAIL:
    return update(state, {$merge: {isFetchingManifest: false}})

  default:
    return state
  }
}
