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

import {
  JOBS_FETCH_FAIL,
  JOBS_FETCH_REQUEST,
  JOBS_FETCH_SUCCESS
} from '../actions/jobs'

import update from 'immutability-helper'

export default (state = {
  isFetching: false,
  jobs: {},
}, action) => {
  switch (action.type) {
    case JOBS_FETCH_REQUEST:
      return {
        isFetching: true,
        jobs: state.jobs,
      }
    case JOBS_FETCH_SUCCESS:
      return {
        isFetching: false,
        jobs: update(state.jobs, {$merge: {[action.tenant]: action.jobs}}),
      }
    case JOBS_FETCH_FAIL:
      return {
        isFetching: false,
        jobs: state.jobs,
      }
    default:
      return state
  }
}
