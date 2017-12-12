"""
Helper functions and classe for the hyperparameter search prototype code
which Adro wrote.
"""

import copy
import json
import os
import psutil
import random
import re
import signal
import subprocess
import threading
import uuid
import yaml

from wandb import wandb_run
from wandb import api as wandb_api
from wandb import util

class Sampler:
    """
    Returns unique samples from a mixed discrete / coninuous distribution.
    """

    class NoMoreSamples(Exception):
        """
        Thrown by the sample() method when all samples have been exhausted.
        """
        pass

    def __init__(self, config):
        """
        Config is the wandb config yaml file but parameters are generalized
        as follows. Axes can be continuous:

            <axis name>: {
                min: <float>,
                max: <float>,
                type: 'linear' (defualt) | 'logarithmic'
            }

        or discrete:

            <axis name>: {min: <int>, max: <int>}

        or a discrete choice of values

            <axis name>: { values: ['value1', 'value2', ...]}

        The axis type is determined automatically.
        """
        # Parse the axes into our own, more explicit axes data structure.
        self._config = copy.copy(config)
        self._axes = {}
        self._n_elements = 1
        for label, data in config.items():
            # All axes must be defined as dictionaries.
            if type(data) != dict:
                continue

            # Either a integer or floating point scale.
            elif {'min', 'max'} < data.keys():
                min, max = data['min'], data['max']

                # floating point scale
                if type(min) == type(max) == float:

                    # logarithmic scale
                    if ('type', 'logarithmic') in data.items():
                        self._axes[label] = Sampler._log_range_axis(min, max)
                        self._n_elements = float('inf')

                    # linear scale
                    else:
                        self._axes[label] = Sampler._linear_range_axis(min, max)
                        self._n_elements = float('inf')

                # integer range
                elif type(min) == type(max) == int:
                    assert min <= max
                    self._axes[label] = Sampler._set_axis(range(min, max+1))
                    self._n_elements *= max - min + 1

            # a discrete set of values
            elif 'values' in data:
                self._axes[label] = Sampler._set_axis(data['values'])
                self._n_elements *= len(data['values'])

            # a single value
            elif 'value' in data:
                self._axes[label] = Sampler._set_axis([data['value']])

        # remember previous samples to make sure samples are unique
        self._drawn_samples = set()

    def sample(self):
        """Draw a unique sample from this sampler."""
        # Don't sample the same point twice.
        if len(self._drawn_samples) == self._n_elements:
            raise Sampler.NoMoreSamples

        # Draw a unique sample
        draw = lambda: tuple(sorted(
            [(label, f()) for (label, f) in self._axes.items()]))
        sample = draw()
        while sample in self._drawn_samples:
            sample = draw()
        self._drawn_samples.add(sample)

        # Apply it to the config file
        config = copy.copy(self._config)
        for key, value in sample:
            config[key]['value'] = value
        return config

    @staticmethod
    def _linear_range_axis(min, max):
        """Uniform distribution on [min, max]."""
        assert min <= max, "Range must be nonempty."
        return lambda: random.uniform(min,max)

    @staticmethod
    def _log_range_axis(min, max):
        """Logarithic distribution on [min,max]."""
        assert min <= max, "Range must be nonempty."
        raise NotImplementedError('Logarithmic ranges not implemented.')

    @staticmethod
    def _set_axis(values):
        """Samples from a finite set of values."""
        return lambda: random.choice(values)

def run_wandb_subprocess(program, config):
    """Runs wandb in a subprocess, returning the ???

    config - the yaml configuration to use
    """
    # create a uid and unique filenames to store data
    uid = wandb_run.generate_id()
    path = wandb_run.run_dir_path(uid, dry=False)
    util.mkdir_exists_ok(path)
    config_filename = os.path.join(path, 'cofig_search_template.yaml')
    proc_stdout = open(os.path.join(path, 'stdout'), 'wb')
    proc_stderr = open(os.path.join(path, 'stderr'), 'wb')

    # write the temporary config file, then run the command
    with open(config_filename, 'w') as config_stream:
        yaml.dump(config, config_stream, default_flow_style=False)
    cmd = ['wandb', 'run', '--configs', config_filename, '--id', uid, program]
    print('Running "%s".' % ' '.join(cmd))
    proc = subprocess.Popen(cmd, stdout=proc_stdout, stderr=proc_stderr)
    return (uid, proc)

def kill_wandb_subprocess(proc, program):
    """
    Searches the process tree to find the deepest subprocess and kills that.

    proc    - the wandb run process that's wrapping
    program - the python program that's being wrapped
    """
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            if child.cmdline() == ['python', program]:
                os.kill(child.pid, signal.SIGINT)
    except psutil.NoSuchProcess as exc:
        print('Process %i already killed:\n%s' % (proc.pid, exc))

class RunStatus:
    """
    Computes and stores the stastus for a bunch of runs, each of which is
    referenced by a uid.
    """

    VAL_LOSS_SENTINAL = None

    def __init__(self, uids):
        """
        Creates a RunStatus object containting information about the listed IDs.
        """
        # Load the results into a dictionary
        self._runs = { uid :
            {
                'state': 'finished',
                'val_loss_history': [],
            } for uid in uids }

        # Now load run status from the website.
        self._query_runs_filesystem()
        # self._query_runs_api()
        self._query_runs_process_tree()

        # display the run status of each uid
        for uid, data in self._runs.items():
            print('%s : state=%s' % (uid, data['state']))

    def should_be_killed(self, uid):
        """
        Returns True if the specified run should be killed, which also implies
        that should_be_replaced() is True too.
        """
        return False

    def should_be_replaced(self, uid):
        """
        Returns true if we should start another run to replace this one.
        """
        # This should only happen if we haven't spawned this process yet.
        if uid is None:
            return True

        # Replace all finished processes.
        state = self._runs[uid]['state']
        if state == 'running':
            return False
        elif state == 'finished':
            return True
        else:
            raise RuntimeError('Process state "%s" unrecognized.' % state)

    def min_val_loss(self, uid):
        """
        Returns the best (lowest) validation loss recorded for this run, or
        RunStatus.VAL_LOSS_SENTINAL if no such loss has yet been recorded.
        """
        if uid is None:
            return RunStatus.VAL_LOSS_SENTINAL
        history = self._runs[uid]['val_loss_history']
        if history:
            # nonempty list
            return min(history)
        else:
            # default return value for empty list
            return RunStatus.VAL_LOSS_SENTINAL

    def best_stats(self):
        """
        Returns the statistics for our best run as a dictionary.
        """
        sorted_runs = \
            sorted([(self.min_val_loss(uid), uid) for uid in self._runs
                if self.min_val_loss(uid) != RunStatus.VAL_LOSS_SENTINAL])

    def _query_runs_filesystem(self):
        """
        Queries the filesystem for run information and loads it into
        self._runs.
        """

        # Query by parsing the filesystem.
        # TODO: Do this by reading from the website.
        wandb_path = 'wandb'
        wandb_history = 'wandb-history.jsonl'
        run_paths = os.listdir(wandb_path)
        for uid in self._runs:
            # figure out the path for this run
            path_filter = re.compile('run-\d{8,8}_\d{6,6}-%s' % uid)
            run_path = max(filter(path_filter.match, run_paths))

            # parse the runfile
            history_path = os.path.join(wandb_path, run_path, wandb_history)
            try:
                with open(history_path) as history:
                    self._runs[uid]['val_loss_history'] = \
                        [json.loads(line)['val_loss']
                            for line in history.readlines()]
            except FileNotFoundError:
                pass

    def _query_runs_api(self):
        """
        Queries the api for run information and loads it into
        self._runs. This is currently broken
        """
        # this is broken and shoudn't be used right now
        raise NotImplementedError('Fix this method.')

        # api = wandb_api.Api()
        # project = api.settings()['project']
        # for run in api.list_runs(project):
        #     uid = run['name'].strip()
        #     if uid in self._runs:
        #         try:
        #             state = run['state']
        #         except KeyError:
        #             state = 'unknown'
        #         try:
        #             min_val_loss = \
        #                 json.loads(run['summaryMetrics'])['val_loss']
        #         except KeyError:
        #             min_val_loss = RunStatus.VAL_LOSS_SENTINAL
        #         self._runs[uid]['state'], self._runs[uid]['min_val_loss'] =\
        #             state, min_val_loss

    def _query_runs_process_tree(self):
        """
        Queries the process tree for run information and loads it into
        self._runs. This is currently broken
        """
        me = psutil.Process(os.getpid())
        for child in me.children(recursive=True):
            try:
                cmd_line = child.cmdline()
            except psutil.ZombieProcess:
                continue
            try:
                uid = cmd_line[cmd_line.index('--id') + 1]
            except ValueError:
                continue
            try:
                self._runs[uid]['state'] = 'running'
            except KeyError:
                raise RuntimeError('Subprocess %s is not '
                    'being tracked properly.' % uid)