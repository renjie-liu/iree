#!/usr/bin/env python3

# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Runs all E2E TensorFlow tests and extracts their benchmarking artifacts.

Example usages:
  # Run all test suites and collect their artifacts:
  python3 ./scripts/get_e2e_artifacts.py

  # Run the e2e_tests test suite and collect its artifacts:
  python3 ./scripts/get_e2e_artifacts.py --test_suites=e2e_tests
"""

import fileinput
import os
import re
import subprocess
import tempfile
from typing import Dict, Set
import zipfile

import utils

from absl import app
from absl import flags

SUITE_NAME_TO_TARGET = {
    'e2e_tests':
        '//integrations/tensorflow/e2e:e2e_tests',
    'mobile_bert_squad_tests':
        '//integrations/tensorflow/e2e:mobile_bert_squad_tests',
    'keras_tests':
        '//integrations/tensorflow/e2e/keras:keras_tests',
    'layers_tests':
        '//integrations/tensorflow/e2e/keras/layers:layers_tests',
    'layers_dynamic_batch_tests':
        '//integrations/tensorflow/e2e/keras/layers:layers_dynamic_batch_tests',
    'layers_training_tests':
        '//integrations/tensorflow/e2e/keras/layers:layers_training_tests',
    'keyword_spotting_tests':
        '//integrations/tensorflow/e2e/keras:keyword_spotting_tests',
    'keyword_spotting_internal_streaming_tests':
        '//integrations/tensorflow/e2e/keras:keyword_spotting_internal_streaming_tests',
    'imagenet_non_hermetic_tests':
        '//integrations/tensorflow/e2e/keras:imagenet_non_hermetic_tests',
    'slim_vision_tests':
        '//integrations/tensorflow/e2e/slim_vision_models:slim_vision_tests',
}
SUITES_HELP = [f'`{name}`' for name in SUITE_NAME_TO_TARGET]
SUITES_HELP = f'{", ".join(SUITES_HELP[:-1])} and {SUITES_HELP[-1]}'

FLAGS = flags.FLAGS

flags.DEFINE_bool(
    'dry_run', False,
    'Run without extracting files. Useful for quickly checking for artifact '
    'collisions.')
flags.DEFINE_string(
    'artifacts_dir', os.path.join(tempfile.gettempdir(), 'iree', 'modules'),
    'Directory to transfer the benchmarking artifacts to. Defaults to '
    '/tmp/iree/modules/')
flags.DEFINE_bool('run_test_suites', True, 'Run any specified test suites.')
flags.DEFINE_list('test_suites', list(SUITE_NAME_TO_TARGET.keys()),
                  f'Any combination of {SUITES_HELP}.')

EXPECTED_COLLISIONS = [
    '/tf_ref/', 'tf_input.mlir', 'iree_input.mlir', '/saved_model/'
]


def _target_to_testlogs_path(target: str) -> str:
  """Convert target into the path where Bazel stores the artifacts we want."""
  return os.path.join('bazel-testlogs',
                      target.replace('//', '').replace(':', os.sep))


def _target_to_test_name(target: str, test_suite_path: str) -> str:
  """Get test_name from `suite_name_test_name__tf__backend_name`."""
  return target.split('__')[0].replace(f'{test_suite_path}_', '')


def get_test_paths_and_names(test_suite_path: str):
  """Get the paths Bazel stores test outputs in and the matching test names."""
  targets = utils.get_test_targets(test_suite_path)
  test_paths = [_target_to_testlogs_path(target) for target in targets]
  test_names = [
      _target_to_test_name(target, test_suite_path) for target in targets
  ]
  return test_paths, test_names


def check_collision(filename: str, test_name: str, written_paths: Set[str],
                    paths_to_tests: Dict[str, str]):
  """Check that we aren't overwriting files unless we expect to."""
  # Note: We can't use a check that the files have identical contents because
  # tf_input.mlir can have random numbers appended to its function names.
  # See https://github.com/google/iree/issues/3375

  expected_collision = any([name in filename for name in EXPECTED_COLLISIONS])
  if filename in written_paths and not expected_collision:
    raise ValueError(f'Collision found on {filename} between {test_name}.py '
                     f'and {paths_to_tests[filename]}.py')
  else:
    written_paths.add(filename)
    paths_to_tests[filename] = test_name


def update_path(archive_path: str):
  """Update the --module_file flag with the new location of the compiled.vmfb"""
  backend_path = archive_path.split('traces')[0]  # 'ModuleName/backend_name'.
  compiled_path = os.path.join(FLAGS.artifacts_dir, backend_path,
                               'compiled.vmfb')
  flagfile_path = os.path.join(FLAGS.artifacts_dir, archive_path)
  for line in fileinput.input(files=[flagfile_path], inplace=True):
    if line.strip().startswith('--module_file'):
      print(f'--module_file={compiled_path}\n', end='')
    else:
      print(line, end='')


def extract_artifacts(test_path: str, test_name: str, written_paths: Set[str],
                      paths_to_tests: Dict[str, str]):
  """Unzips all of the benchmarking artifacts for a given test and backend."""
  outputs = os.path.join(test_path, 'test.outputs', 'outputs.zip')
  if FLAGS.dry_run and not os.path.exists(outputs):
    # The artifacts may or may not be present on disk during a dry run. If they
    # are then we want to collision check them, but if they aren't that's fine.
    return

  archive = zipfile.ZipFile(outputs)
  # Filter out directory names.
  filenames = [name for name in archive.namelist() if name[-1] != os.sep]

  for filename in filenames:
    # Check for collisions.
    check_collision(filename, test_name, written_paths, paths_to_tests)

    # Extract and update flagfile path.
    if not FLAGS.dry_run:
      archive.extract(filename, FLAGS.artifacts_dir)
      if filename.endswith('flagfile'):
        update_path(filename)


def main(argv):
  del argv  # Unused.

  # Convert test suite shorthands to full test suite targets.
  test_suites = [SUITE_NAME_TO_TARGET[suite] for suite in FLAGS.test_suites]

  if FLAGS.run_test_suites:
    # Use bazel test to execute all of the test suites in parallel.
    command = [
        'bazel', 'test', *test_suites, '--color=yes',
        '--test_arg=--get_saved_model'
    ]
    print(f'Running: `{" ".join(command)}`')
    if not FLAGS.dry_run:
      subprocess.check_call(command)
    print()

  written_paths = set()
  paths_to_tests = dict()

  for test_suite in test_suites:
    # Extract all of the artifacts for this test suite.
    test_paths, test_names = get_test_paths_and_names(test_suite)
    for i, (test_path, test_name) in enumerate(zip(test_paths, test_names)):
      print(f'\rTransfering {test_suite} {i + 1}/{len(test_paths)}', end='')
      extract_artifacts(test_path, test_name, written_paths, paths_to_tests)
    print('\n')


if __name__ == '__main__':
  app.run(main)
