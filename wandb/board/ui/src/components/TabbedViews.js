import React from 'react';
import _ from 'lodash';
import PropTypes from 'prop-types';
import {Button, Icon, Menu, Tab} from 'semantic-ui-react';

class TabbedViews extends React.Component {
  state = {editMode: false};

  static propTypes = {
    renderView: PropTypes.func.isRequired,
  };

  render() {
    let panes = this.props.tabs.map(viewId => ({
      menuItem: {
        key: viewId,
        content: <span>{this.props.views[viewId].name + ' '}</span>,
      },
      render: () => (
        <Tab.Pane>
          {this.props.renderView(viewId, this.state.editMode)}
        </Tab.Pane>
      ),
    }));
    if (this.state.editMode) {
      panes.push({
        menuItem: (
          <Menu.Item
            key="add"
            onClick={() =>
              this.props.addView(this.props.viewType, 'New View', [])
            }>
            <Icon name="plus" />
          </Menu.Item>
        ),
      });
    }
    let activeIndex = 0;
    if (!_.isNil(this.props.activeView)) {
      activeIndex = _.indexOf(this.props.tabs, this.props.activeView);
      if (activeIndex === -1) {
        activeIndex = 0;
      }
    }
    return (
      <div>
        {this.state.editMode && (
          <Button
            color="green"
            floated="right"
            content="Save Changes"
            disabled={!this.props.isModified}
            onClick={() => {
              this.setState({editMode: false});
              this.props.updateViews(JSON.stringify(this.props.viewState));
            }}
          />
        )}
        <Button
          content={this.state.editMode ? 'View Charts' : 'Edit Charts'}
          floated="right"
          icon={this.state.editMode ? 'unhide' : 'configure'}
          onClick={() => this.setState({editMode: !this.state.editMode})}
        />
        {(!this.props.blank || this.state.editMode) && (
          <Tab
            panes={panes}
            activeIndex={activeIndex}
            onTabChange={(event, {activeIndex}) => {
              this.props.setActiveView(
                this.props.viewType,
                this.props.tabs[activeIndex] || activeIndex,
              );
            }}
          />
        )}
      </div>
    );
  }
}

export default TabbedViews;