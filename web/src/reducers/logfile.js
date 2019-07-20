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
  LOGFILE_FETCH_FAIL,
  LOGFILE_FETCH_REQUEST,
  LOGFILE_FETCH_SUCCESS,
} from '../actions/logfile'


export default (state = {
  isFetching: false,
  url: null,
  data: null
}, action) => {
  switch (action.type) {
    case LOGFILE_FETCH_REQUEST:
      return update(state, {$merge: {isFetching: true,
                                     url: action.url,
                                     data: null}})
    case LOGFILE_FETCH_SUCCESS:
      return update(state, {$merge: {isFetching: false,
                                     data: action.data}})
    case LOGFILE_FETCH_FAIL:
      return update(state, {$merge: {isFetching: false,
                                     url: null,
                                     data: null}})
    default:
      return state
  }
}
