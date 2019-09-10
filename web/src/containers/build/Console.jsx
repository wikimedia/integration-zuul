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

import * as React from 'react'
import PropTypes from 'prop-types'
import ReactJson from 'react-json-view'
import {
  Icon,
  ListView,
  Row,
  Col,
  Modal,
} from 'patternfly-react'

import {
  hasInterestingKeys,
  findLoopLabel,
  shouldIncludeKey,
  makeTaskPath,
  taskPathMatches,
} from '../../actions/build'

const INTERESTING_KEYS = ['msg', 'stdout', 'stderr']


class TaskOutput extends React.Component {
  static propTypes = {
    data: PropTypes.object,
    include: PropTypes.array,
  }

  renderResults(value) {
    const interesting_results = []
    value.forEach((result, idx) => {
      const keys = Object.entries(result).filter(
        ([key, value]) => shouldIncludeKey(
          key, value, true, this.props.include))
      if (keys.length) {
        interesting_results.push(idx)
      }
    })

    return (
      <div key='results'>
        {interesting_results.length>0 &&
         <React.Fragment>
           <h5 key='results-header'>results</h5>
           {interesting_results.map((idx) => (
             <div className='zuul-console-task-result' key={idx}>
               <h4 key={idx}>{idx}: {findLoopLabel(value[idx])}</h4>
               {Object.entries(value[idx]).map(([key, value]) => (
                 this.renderData(key, value, true)
               ))}
             </div>
           ))}
         </React.Fragment>
        }
      </div>
    )
  }

  renderData(key, value, ignore_underscore) {
    let ret
    if (!shouldIncludeKey(key, value, ignore_underscore, this.props.include)) {
      return (<React.Fragment key={key}/>)
    }
    if (value === null) {
      ret = (
        <pre>
          null
        </pre>
      )
    } else if (typeof(value) === 'string') {
      ret = (
        <pre>
          {value}
        </pre>
      )
    } else if (typeof(value) === 'object') {
      ret = (
        <pre>
          <ReactJson
            src={value}
            name={null}
            sortKeys={true}
            enableClipboard={false}
            displayDataTypes={false}/>
        </pre>
      )
    } else {
      ret = (
        <pre>
          {value.toString()}
        </pre>
      )
    }

    return (
      <div key={key}>
        {ret && <h5>{key}</h5>}
        {ret && ret}
      </div>
    )
  }

  render () {
    const { data } = this.props

    return (
      <React.Fragment>
        {Object.entries(data).map(([key, value]) => (
          key==='results'?this.renderResults(value):this.renderData(key, value)
        ))}
      </React.Fragment>
    )
  }
}

class HostTask extends React.Component {
  static propTypes = {
    hostname: PropTypes.string,
    task: PropTypes.object,
    host: PropTypes.object,
    errorIds: PropTypes.object,
    taskPath: PropTypes.array,
    displayPath: PropTypes.array,
  }

  state = {
    showModal: false,
    failed: false,
    changed: false,
    skipped: false,
    ok: false
  }

  open = () => {
    this.setState({ showModal: true})
  }

  close = () => {
    this.setState({ showModal: false})
  }

  constructor (props) {
    super(props)

    const { host, taskPath, displayPath } = this.props

    if (host.failed) {
      this.state.failed = true
    } else if (host.changed) {
      this.state.changed = true
    } else if (host.skipped) {
      this.state.skipped = true
    } else {
      this.state.ok = true
    }

    if (taskPathMatches(taskPath, displayPath))
      this.state.showModal = true
  }

  render () {
    const { hostname, task, host, taskPath, errorIds } = this.props

    const ai = []
    if (this.state.failed) {
      ai.push(
        <ListView.InfoItem key="failed" title="Click for details">
          <span className="task-failed" onClick={this.open}>FAILED</span>
        </ListView.InfoItem>)
    } else if (this.state.changed) {
      ai.push(
        <ListView.InfoItem key="changed" title="Click for details">
          <span className="task-changed" onClick={this.open}>CHANGED</span>
        </ListView.InfoItem>)
    } else if (this.state.skipped) {
      ai.push(
        <ListView.InfoItem key="skipped" title="Click for details">
          <span className="task-skipped" onClick={this.open}>SKIPPED</span>
        </ListView.InfoItem>)
    } else if (this.state.ok) {
      ai.push(
        <ListView.InfoItem key="ok" title="Click for details">
          <span className="task-ok" onClick={this.open}>OK</span>
        </ListView.InfoItem>)
    }
    ai.push(
      <ListView.InfoItem key="hostname">
        <span className="additionalinfo-icon">
          <Icon type='pf' name='container-node' />
          {hostname}
        </span>
      </ListView.InfoItem>
    )

    const expand = errorIds.has(task.task.id)

    let name = task.task.name
    if (!name) {
      name = host.action
    }
    if (task.role) {
      name = task.role.name + ': ' + name
    }
    const has_interesting_keys = hasInterestingKeys(this.props.host, INTERESTING_KEYS)
    let lc = undefined
    if (!has_interesting_keys) {
      lc = []
    }
    return (
      <React.Fragment>
        <ListView.Item
          key='header'
          heading={name}
          initExpanded={expand}
          additionalInfo={ai}
          leftContent={lc}
        >
          {has_interesting_keys &&
           <Row>
             <Col sm={11}>
               <pre>
                 <TaskOutput data={this.props.host} include={INTERESTING_KEYS}/>
               </pre>
             </Col>
           </Row>
          }
        </ListView.Item>
        <Modal key='modal' show={this.state.showModal} onHide={this.close}
               dialogClassName="zuul-console-task-detail">
          <Modal.Header>
            <button
              className="close"
              onClick={this.close}
              aria-hidden="true"
              aria-label="Close"
            >
              <Icon type="pf" name="close" />
            </button>
            <Modal.Title>{hostname}
              <span className="zuul-console-modal-header-link">
                <a href={'#'+makeTaskPath(taskPath)}>
                  <Icon type="fa" name="link" title="Permalink" />
                </a>
              </span>
            </Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <TaskOutput data={host}/>
          </Modal.Body>
        </Modal>
      </React.Fragment>
    )
  }
}

class PlayBook extends React.Component {
  static propTypes = {
    playbook: PropTypes.object,
    errorIds: PropTypes.object,
    taskPath: PropTypes.array,
    displayPath: PropTypes.array,
  }

  render () {
    const { playbook, errorIds, taskPath, displayPath } = this.props

    const expandAll = (playbook.phase === 'run')
    const expand = (expandAll ||
                    errorIds.has(playbook.phase + playbook.index) ||
                    taskPathMatches(taskPath, displayPath))

    const ai = []
    if (playbook.trusted) {
      ai.push(
        <ListView.InfoItem key="trusted" title="This playbook runs in a trusted execution context, which permits executing code on the Zuul executor and allows access to all Ansible features.">
          <span className="additionalinfo-icon">
            <Icon type='pf' name='info' /> Trusted
          </span>
        </ListView.InfoItem>
      )
    }

    return (
      <ListView.Item
        stacked={true}
        additionalInfo={ai}
        initExpanded={expand}
        heading={playbook.phase[0].toUpperCase() + playbook.phase.slice(1) + ' playbook'}
        description={playbook.playbook}
      >
          {playbook.plays.map((play, idx) => (
            <React.Fragment key={idx}>
              <Row key='play'>
                <Col sm={12}>
                  <strong>Play: {play.play.name}</strong>
                </Col>
              </Row>
              {play.tasks.map((task, idx2) => (
                Object.entries(task.hosts).map(([hostname, host]) => (
                  <Row key={idx2+hostname}>
                    <Col sm={12}>
                      <HostTask hostname={hostname}
                                taskPath={taskPath.concat([
                                  idx.toString(), idx2.toString(), hostname])}
                                displayPath={displayPath} task={task} host={host}
                                errorIds={errorIds}/>
                    </Col>
                  </Row>
                ))))}
            </React.Fragment>
          ))}
      </ListView.Item>
    )
  }
}

class Console extends React.Component {
  static propTypes = {
    errorIds: PropTypes.object,
    output: PropTypes.array,
    displayPath: PropTypes.array,
  }

  render () {
    const { errorIds, output, displayPath } = this.props

    return (
      <React.Fragment>
        <ListView key="playbooks" className="zuul-console">
          {output.map((playbook, idx) => (
            <PlayBook key={idx} playbook={playbook} taskPath={[idx.toString()]}
                      displayPath={displayPath} errorIds={errorIds}/>))}
        </ListView>
      </React.Fragment>
    )
  }
}


export default Console
