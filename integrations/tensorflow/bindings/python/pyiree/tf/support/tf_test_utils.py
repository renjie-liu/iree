# Lint as: python3
# Copyright 2019 Google LLC
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
"""Test utilities interop with TensorFlow."""

# pylint: disable=missing-docstring
# pylint: disable=protected-access
# pylint: disable=unsupported-assignment-operation

# This file uses the following abbreviations:
#   ref: reference – for the reference CompiledModule
#   tar: target - for one of the target CompiledModules

import collections
import copy
import glob
import inspect
import os
import pickle
import sys
import tempfile
from typing import Any, Callable, Dict, Sequence, Set, Tuple, Type, Union

from absl import flags
from absl import logging
import numpy as np
from pyiree.tf.support import tf_utils
import tensorflow.compat.v2 as tf

flags.DEFINE_string("reference_backend", "tf",
                    "The backend to treat as a source of truth.")
flags.DEFINE_string("target_backends", None,
                    "Explicit comma-delimited list of target backends.")
flags.DEFINE_string(
    "artifacts_dir", None,
    "Specifies a directory to dump compilation artifacts and traces to. "
    "Defaults to the OS's tempdir.")
flags.DEFINE_bool(
    "summarize", True,
    "Summarize the inputs and outputs of each module trace logged to disk.")
flags.DEFINE_bool("log_all_traces", False,
                  "Log all traces to logging.info, even if comparison passes.")
flags.DEFINE_bool(
    "get_saved_model", False,
    "Creates and stores a SavedModel for the tf.Module class to be tested.")
FLAGS = flags.FLAGS
NUMPY_LINEWIDTH = 120


def _setup_artifacts_dir(module_name: str) -> str:
  parent_dirs = [
      FLAGS.artifacts_dir,
      os.environ.get('TEST_UNDECLARED_OUTPUTS_DIR'),
      os.environ.get('TEST_TMPDIR'),
      os.path.join(tempfile.gettempdir(), "iree", "modules"),
  ]
  # Use the most preferred path in parent_dirs that isn't None.
  parent_dir = next(parent for parent in parent_dirs if parent is not None)

  artifacts_dir = os.path.join(parent_dir, module_name)
  logging.info("Saving compilation artifacts and traces to '%s'", artifacts_dir)
  os.makedirs(artifacts_dir, exist_ok=True)
  return artifacts_dir


def _parse_target_backends() -> Tuple[Sequence[str], Sequence[str]]:
  """Decodes --target_backends and creates unique ids for them."""
  backend_names = FLAGS.target_backends.split(",")
  backend_to_index = {k: 0 for k in backend_names if backend_names.count(k) > 1}
  backend_ids = []

  # If there are multiple copies of the same backend_name, index them. e.g.
  # backend_names = ["tf", "iree_vmla", "tf"]
  # --> backend_ids = ["tf_0", "iree_vmla", "tf_1"]
  for backend_name in backend_names:
    if backend_name in backend_to_index:
      backend_ids.append(f"{backend_name}_{backend_to_index[backend_name]}")
      backend_to_index[backend_name] += 1
    else:
      backend_ids.append(backend_name)

  return backend_names, backend_ids


def get_target_backends() -> Sequence[tf_utils.BackendInfo]:
  """Gets the BackendInfo instances to compare with the reference backend.

  By default all backends in BackendInfo will be used. Specific backends to
  run on can be specified using the `--target_backends` flag.

  Returns:
    Sequence of BackendInfo that should be used.
  """
  if FLAGS.target_backends is not None:
    logging.info("Using backends from command line: %s", FLAGS.target_backends)
    backend_names, backend_ids = _parse_target_backends()
    backends = [
        tf_utils.BackendInfo(backend_name, backend_id)
        for backend_name, backend_id in zip(backend_names, backend_ids)
    ]
  else:
    # If no backends are specified, use them all.
    backends = tf_utils.BackendInfo.get_all_backends()
  return backends


def _indent(input_str: str, indentation: int = 2) -> str:
  """Indents a string by the specified number of spaces, defaulting to 2."""
  spaces = " " * indentation
  lines = input_str.split("\n")
  # Prepend spaces to each non-empty line.
  lines = [f"{spaces}{line}" if len(line) else line for line in lines]
  return "\n".join(lines)


def _zfill_width(length: int) -> Union[int, None]:
  return int(np.ceil(np.log10(length))) if length else None


class ModuleCall:

  def __init__(self,
               method: str,
               inputs: Tuple[Any],
               outputs: Tuple[Any],
               serialized_inputs: Tuple[str],
               serialized_outputs: Tuple[str],
               rtol: float = 1e-6,
               atol: float = 1e-6):
    """Records the details of a call to a CompiledModule."""
    self.method = method

    for value in inputs:
      if isinstance(value, tf.Tensor):
        raise TypeError("Expected inputs to be native python types or numpy "
                        f"arrays, but got {type(value)}")

    # Deepcopy to safegard against mutation.
    self.inputs = copy.deepcopy(inputs)
    if outputs is not None:
      outputs = copy.deepcopy(outputs)
    else:
      outputs = tuple()
    self.outputs = outputs if isinstance(outputs, tuple) else (outputs,)

    self.serialized_inputs = serialized_inputs
    self.serialized_outputs = serialized_outputs

    self.rtol = rtol
    self.atol = atol

  def get_tolerances(self) -> Tuple[float, float]:
    """Gets the floating point tolerances associated with this call."""
    return self.rtol, self.atol

  def _get_shape_and_dtype(self, value: Any) -> str:
    if isinstance(value, np.ndarray):
      return tf_utils.get_shape_and_dtype(value, allow_non_mlir_dtype=True)
    else:
      return str(type(value))

  def __str__(self):
    prior_printoptions = np.get_printoptions()
    np.set_printoptions(linewidth=NUMPY_LINEWIDTH)

    header = f"Method: {self.method}"
    inputs = "\n".join(_indent(str(value)) for value in self.inputs)
    input_shapes = ", ".join(
        self._get_shape_and_dtype(value) for value in self.inputs)

    outputs = "\n".join(_indent(str(value)) for value in self.outputs)
    output_shapes = ", ".join(
        self._get_shape_and_dtype(value) for value in self.outputs)

    tolerances = _indent(f"rtol={self.rtol}, atol={self.atol}")
    body = (f"Inputs: {input_shapes}\n{inputs}\n"
            f"Outputs: {output_shapes}\n{outputs}"
            f"\nTolerances:\n{tolerances}")
    result = f"{header}\n{_indent(body)}"

    np.set_printoptions(**prior_printoptions)
    return result

  def serialize(self, call_dir: str) -> None:
    """Stores a serialized copy of this call.

    Can be loaded via ModuleCall.load(call_dir)

    Args:
      call_dir: str, the path to the directory to serialize this call to.
    """
    os.makedirs(call_dir, exist_ok=True)

    metadata = {
        "method": self.method,
        "serialized_inputs": self.serialized_inputs,
        "serialized_outputs": self.serialized_outputs,
        "rtol": self.rtol,
        "atol": self.atol
    }
    with open(os.path.join(call_dir, "metadata.pkl"), "wb") as f:
      pickle.dump(metadata, f)

    width = _zfill_width(len(self.inputs))
    for i, value in enumerate(self.inputs):
      path = os.path.join(call_dir, f"input_{str(i).zfill(width)}.pkl")
      with open(path, "wb") as f:
        pickle.dump(value, f)

    width = _zfill_width(len(self.outputs))
    for i, value in enumerate(self.outputs):
      path = os.path.join(call_dir, f"output_{str(i).zfill(width)}.pkl")
      with open(path, "wb") as f:
        pickle.dump(value, f)

  @staticmethod
  def load(call_dir: str) -> "ModuleCall":
    """Loads and returns a trace serialized with ModuleCall.serialize."""
    with open(os.path.join(call_dir, "metadata.pkl"), "rb") as f:
      kwargs = pickle.load(f)

    for result_type in ["input", "output"]:
      key = f"{result_type}s"  # inputs or outputs
      kwargs[key] = []

      files = glob.glob(os.path.join(call_dir, f"{result_type}_*.pkl"))
      for filename in sorted(files):
        with open(filename, "rb") as f:
          kwargs[key].append(pickle.load(f))

      # Convert to tuple to match python's return type for multiple results.
      kwargs[key] = tuple(kwargs[key])

    return ModuleCall(**kwargs)


class Trace:
  """Stores the inputs and outputs of a series of calls to a module."""

  def __init__(self,
               module: Union[tf_utils.CompiledModule, None],
               function: Union[Callable[["TracedModule"], None], None],
               _load_dict: Dict[str, Any] = None):
    """Extracts metadata from module and function and initializes.

    Example usage:
      def forward_pass(...):
        ...
      module = IreeCompiledModule(...)
      trace = Trace(module, forward_pass)
      forward_pass(TracedModule(module, trace))

    Args:
      module: the module who's outputs this trace will record.
      function: the function that module will be traced on.
      _load_dict: used internally
    """
    if _load_dict is None:
      # Extract metadata from module and function.
      self.module_name = module.module_name
      self.compiled_paths = module.compiled_paths
      self.backend_name = module.backend_info.backend_name
      self.backend_id = module.backend_info.backend_id
      self.backend_driver = module.backend_info.driver
      self.iree_serializable = module.iree_serializable()
      self.tflite_serializable = module.tflite_serializable()
      self.function_name = function.__name__
      self.function_sourcefile = inspect.getsourcefile(function)
      source, start_line = inspect.getsourcelines(function)
      self.function_line_numbers = (start_line, start_line + len(source))
      self.function_source = "".join(source)

      self.calls = []
    else:
      self.module_name = _load_dict["module_name"]
      self.compiled_paths = _load_dict["compiled_paths"]
      self.backend_name = _load_dict["backend_name"]
      self.backend_id = _load_dict["backend_id"]
      self.backend_driver = _load_dict["backend_driver"]
      self.iree_serializable = _load_dict["iree_serializable"]
      self.tflite_serializable = _load_dict["tflite_serializable"]
      self.function_name = _load_dict["function_name"]
      self.function_sourcefile = _load_dict["function_sourcefile"]
      self.function_line_numbers = _load_dict["function_line_numbers"]
      self.function_source = _load_dict["function_source"]
      self.calls = _load_dict["calls"]

  def __str__(self):
    header = (f"Trace of {self.module_name} compiled to '{self.backend_id}' "
              f"on function '{self.function_name}':")
    # Give each call a number so it's easier to compare between multiple traces.
    calls = [f"{i + 1}. {str(call)}" for i, call in enumerate(self.calls)]
    calls = _indent("\n".join(calls))
    return f"{header}\n{calls}"

  def __iter__(self):
    for call in self.calls:
      yield call

  @staticmethod
  def compare_traces(ref_trace: "Trace",
                     tar_trace: "Trace") -> Tuple[bool, Sequence[str]]:
    traces_match = True
    error_messages = []

    # Check that all method invocations match.
    ref_methods = [(call.method, call.rtol, call.atol) for call in ref_trace]
    tar_methods = [(call.method, call.rtol, call.atol) for call in tar_trace]
    if ref_methods != tar_methods:
      # Raise a ValueError instead of returning False since this is an
      # unexpected error.
      raise ValueError(
          "The reference and target traces have different call structures:\n"
          f"Reference: {ref_methods}\nTarget:    {tar_methods}")

    for ref_call, tar_call in zip(ref_trace, tar_trace):
      logging.info("Comparing calls to '%s'", ref_call.method)
      rtol, atol = ref_call.get_tolerances()

      inputs_match, error_message = Trace._check_same(ref_call.inputs,
                                                      tar_call.inputs, rtol,
                                                      atol)
      if not inputs_match:
        error_messages.append(error_message)
        logging.error("Inputs did not match.")
      outputs_match, error_message = Trace._check_same(ref_call.outputs,
                                                       tar_call.outputs, rtol,
                                                       atol)
      if not outputs_match:
        error_messages.append(error_message)
        logging.error("Outputs did not match.")
      calls_match = inputs_match and outputs_match

      if not calls_match:
        logging.error("Comparision between '%s' and '%s' failed on method '%s'",
                      ref_trace.backend_id, tar_trace.backend_id,
                      ref_call.method)
        logging.error("Reference call '%s':\n%s", ref_trace.backend_id,
                      ref_call)
        logging.error("Target call '%s':\n%s", tar_trace.backend_id, tar_call)

      traces_match = traces_match and calls_match
    return traces_match, error_messages

  @staticmethod
  def _check_same(ref: Any, tar: Any, rtol: float,
                  atol: float) -> Tuple[bool, Union[str, None]]:
    """Checks that ref and tar have identical datastructures and values."""
    # Check for matching types.
    if not isinstance(tar, type(ref)):
      error = ("Expected ref and tar to have the same type but got "
               f"'{type(ref)}' and '{type(tar)}'")
      logging.error(error)
      return False, error

    if ref is None:
      # Nothing to compare (e.g. the called method had no outputs).
      return True, None

    # Recursive check for dicts.
    if isinstance(ref, dict):
      if ref.keys() != tar.keys():
        error = ("Expected ref and tar to have the same keys, but got "
                 f"'{ref.keys()}' and '{tar.keys()}'")
        logging.error(error)
        return False, error
      # Check that all of the dictionaries' values are the same.
      for key in ref:
        same, error = Trace._check_same(ref[key], tar[key], rtol, atol)
        if not same:
          return same, error

    # Recursive check for iterables.
    elif isinstance(ref, list) or isinstance(ref, tuple):
      if len(ref) != len(tar):
        error = ("Expected ref and tar to have the same length, but got "
                 f"{len(ref)} and {len(tar)}")
        logging.error(error)
        return False, error
      # Check that all of the iterables' values are the same.
      for i in range(len(ref)):
        same, error = Trace._check_same(ref[i], tar[i], rtol, atol)
        if not same:
          return same, error

    # Base check for numpy arrays.
    elif isinstance(ref, np.ndarray):
      if ref.dtype != tar.dtype:
        error = ("Expected ref and tar to have the same dtype, but got "
                 f"'{ref.dtype}' and '{tar.dtype}'")
        logging.error(error)
        return False, error
      if ref.size == tar.size == 0:
        return True, None

      if np.issubdtype(ref.dtype, np.floating):
        same = np.allclose(ref, tar, rtol=rtol, atol=atol)
        abs_diff = np.max(np.abs(ref - tar))
        rel_diff = np.max(np.abs(ref - tar) / np.max(np.abs(tar)))
        diff_string = (f"Max abs diff: {abs_diff:.2e}, atol: {atol:.2e}, "
                       f"max relative diff: {rel_diff:.2e}, rtol: {rtol:.2e}")
        if not same:
          error = ("Floating point difference between ref and tar was too "
                   f"large. {diff_string}")
          logging.error(error)
        else:
          error = None
          logging.info(
              "Floating point difference between ref and tar was within "
              "tolerance. %s", diff_string)
        return same, error
      elif np.issubdtype(ref.dtype, np.integer):
        same = np.array_equal(ref, tar)
        if not same:
          abs_diff = np.max(np.abs(ref - tar))
          error = ("Expected array equality between ref and tar, but got "
                   f"a max elementwise difference of {abs_diff}")
          logging.error(error)
        else:
          error = None
        return same, error
      else:
        return np.array_equal(ref, tar), None

    # Base check for native number types.
    elif isinstance(ref, (int, float)):
      return ref == tar, None

    # If outputs end up here then an extra branch for that type should be added.
    else:
      raise TypeError(f"Encountered results with unexpected type {type(ref)}")
    return True, None

  def save_plaintext(self, trace_dir: str, summarize: bool = True) -> None:
    """Saves a human-readable string representation of this trace to disk.

    Args:
      trace_dir: str, path to the directory to save the trace in.
      summarize: a bool controlling whether numpy should summarize the inputs
        and outputs if they're large. Setting this to False is very slow for
        large outputs.
    """
    prior_printoptions = np.get_printoptions()
    np.set_printoptions(
        linewidth=NUMPY_LINEWIDTH,
        threshold=None if summarize else sys.maxsize,
        edgeitems=10)  # Can show more items since they won't clutter the logs.

    path = os.path.join(trace_dir, "log.txt")
    with open(path, "w") as f:
      f.write(str(self))
      f.write("\n")

    np.set_printoptions(**prior_printoptions)

  def serialize(self, trace_dir: str) -> None:
    """Stores a serialized copy of this trace in trace_dir.

    It can be loaded via `Trace.load(trace_dir)`.

    Args:
      trace_dir: str, path to the directory to serialize the trace to.
    """

    compiled_paths = None
    if self.compiled_paths is not None:
      # Convert to a dict to avoid the issues with serializing defaultdicts.
      compiled_paths = dict(self.compiled_paths)

    # Python serialization.
    metadata = {
        "module_name": self.module_name,
        "compiled_paths": compiled_paths,
        "backend_name": self.backend_name,
        "backend_id": self.backend_id,
        "backend_driver": self.backend_driver,
        "iree_serializable": self.iree_serializable,
        "tflite_serializable": self.tflite_serializable,
        "function_name": self.function_name,
        "function_sourcefile": self.function_sourcefile,
        "function_line_numbers": self.function_line_numbers,
        "function_source": self.function_source
    }
    with open(os.path.join(trace_dir, "metadata.pkl"), "wb") as f:
      pickle.dump(metadata, f)

    width = _zfill_width(len(self.calls))
    for i, call in enumerate(self.calls):
      call_dir = os.path.join(trace_dir, f"call_{str(i).zfill(width)}")
      call.serialize(call_dir)

    # C++ benchmark serialization.
    if self.iree_serializable or self.tflite_serializable:
      entry_function = self.calls[0].method
      compiled_path = self.compiled_paths[entry_function]

      if self.iree_serializable:
        serialized_inputs = ", ".join(self.calls[0].serialized_inputs)
        flagfile = [
            f"--module_file={compiled_path}",
            f"--driver={self.backend_driver}",
            f"--function_inputs={serialized_inputs}",
            f"--entry_function={entry_function}",
        ]
        with open(os.path.join(trace_dir, "flagfile"), "w") as f:
          f.writelines(line + "\n" for line in flagfile)
      else:
        with open(os.path.join(trace_dir, "graph_path"), "w") as f:
          f.writelines(compiled_path + "\n")

  @staticmethod
  def load(trace_dir: str) -> "Trace":
    """Loads and returns a trace serialized with Trace.serialize.

    Args:
      trace_dir: str, path to the directory of the serialized trace.

    Returns:
      A Trace deserialized from trace_dir.
    """
    with open(os.path.join(trace_dir, "metadata.pkl"), "rb") as f:
      load_dict = pickle.load(f)
    call_dirs = sorted(glob.glob(os.path.join(trace_dir, "call_*")))
    calls = [ModuleCall.load(call_dir) for call_dir in call_dirs]
    load_dict["calls"] = calls
    return Trace(module=None, function=None, _load_dict=load_dict)


def _get_trace_dir(artifacts_dir: str, trace: Trace) -> str:
  trace_dir = os.path.join(artifacts_dir, trace.backend_id, "traces",
                           trace.function_name)
  os.makedirs(trace_dir, exist_ok=True)
  return trace_dir


class TracedModule:

  def __init__(self, module: tf_utils.CompiledModule, trace: Trace):
    """Wraps a CompiledModule so that all inputs and outputs are traced.

    The TracedModule returned will have an API almost identical to that of the
    passed CompiledModule. The only changes is that if the keywords `rtol` or
    `atol` are passed to one of the CompiledModule's methods, then they will be
    used to set the tolerance for comparing that call to the same call in
    another trace. So for example, calling `traced_module.add(a, b rtol=1e-8)`
    would be the same as calling `module.add(a, b)`.

    Args:
      module: the CompiledModule to trace.
      trace: the Trace to record calls to this module with.
    """
    self._module = module
    self._trace = trace

  def _trace_call(self, method: tf_utils._FunctionWrapper, method_name: str):
    """Decorates a CompiledModule method to capture its inputs and outputs."""

    def call(*args, **kwargs):
      # Pop manually specified tolerances from the kwargs (if any).
      tolerances = {}
      tolerances["rtol"] = kwargs.pop("rtol", None)
      tolerances["atol"] = kwargs.pop("atol", None)
      # Only pass these to ModuleCall if they were specified by the user.
      tolerances = {k: v for k, v in tolerances.items() if v is not None}

      # Run the method and record the details of the call.
      outputs = method(*args, **kwargs)
      serialized_inputs, serialized_outputs = method.get_serialized_values()
      self._trace.calls.append(
          ModuleCall(method_name, args, outputs, serialized_inputs,
                     serialized_outputs, **tolerances))
      return outputs

    return call

  def __getattr__(self, attr):
    # Try to resolve it as an attr on self._module.
    if not hasattr(self._module, attr):
      raise AttributeError(f"The compiled module does not have attr '{attr}'")
    module_attr = getattr(self._module, attr)
    if not hasattr(module_attr, "__call__"):
      # e.g. traced_module.backend
      return module_attr
    else:
      # e.g. traced_module.simple_mul(a, b)
      return self._trace_call(module_attr, method_name=attr)


Modules = collections.namedtuple("Modules",
                                 ["ref_module", "tar_modules", "artifacts_dir"])

# We have to use a global variable to store the compiled modules so that we can
# avoid recompilation. This is because the TestCase class resets it's entire
# state and calls __init__ before each unittest. It also calls __init__ one
# additional time before that for good measure, which means without storing the
# modules somewhere else we would have to compile each of them at least twice.
# We can't store the modules on the class itself via setUpClass because of #2900
global _global_modules
_global_modules = None


def compile_tf_module(
    module_class: Type[tf.Module],
    exported_names: Sequence[str] = ()) -> Modules:
  """Compiles module_class to each backend that we test.

  Args:
    module_class: the tf.Module subclass to compile.
    exported_names: optional iterable of strings representing which of
      module_class's functions to compile. If exported_names is empty all
      functions will be compiled.

  Returns:
    A 'Modules' namedtuple containing the reference module, target modules and
    artifacts directory.
  """
  global _global_modules
  if _global_modules is not None:
    return _global_modules

  # Setup the directory for saving compilation artifacts and traces.
  artifacts_dir = _setup_artifacts_dir(module_class.__name__)

  # Get the backend information for this test.
  ref_backend_info = tf_utils.BackendInfo(FLAGS.reference_backend,
                                          f"{FLAGS.reference_backend}_ref")
  tar_backend_infos = get_target_backends()

  compile_backend = lambda backend_info: backend_info.compile_from_class(
      module_class, exported_names, artifacts_dir)

  ref_module = compile_backend(ref_backend_info)
  tar_modules = [
      compile_backend(backend_info) for backend_info in tar_backend_infos
  ]
  _global_modules = Modules(ref_module, tar_modules, artifacts_dir)
  return _global_modules


def compile_tf_signature_def_saved_model(
    saved_model_dir: str, saved_model_tags: Set[str], module_name: str,
    exported_name: str, input_names: Sequence[str],
    output_names: Sequence[str]) -> Modules:
  """Compiles a SignatureDef SavedModel to each backend that we test.

  Args:
    saved_model_dir: Directory of the saved model.
    saved_model_tags: Optional set of tags to use when loading the model.
    module_name: A name for this compiled module.
    backend_info: BackendInfo with the details for compiling the saved model.
    exported_name: A str representing the signature on the saved model to
      compile.
    input_names: A sequence of kwargs to feed to the saved model.
    output_names: A sequence of named outputs to extract from the saved model.

  Returns:
    A 'Modules' namedtuple containing the reference module, target modules and
    artifacts directory.
  """
  global _global_modules
  if _global_modules is not None:
    return _global_modules

  # Setup the directory for saving compilation artifacts and traces.
  artifacts_dir = _setup_artifacts_dir(module_name)

  # Get the backend information for this test.
  ref_backend_info = tf_utils.BackendInfo(FLAGS.reference_backend,
                                          f"{FLAGS.reference_backend}_ref")
  tar_backend_infos = get_target_backends()

  compile_backend = (
      lambda backend_info: backend_info.compile_signature_def_saved_model(
          saved_model_dir, saved_model_tags, module_name, exported_name,
          input_names, output_names, artifacts_dir))

  ref_module = compile_backend(ref_backend_info)
  tar_modules = [
      compile_backend(backend_info) for backend_info in tar_backend_infos
  ]
  _global_modules = Modules(ref_module, tar_modules, artifacts_dir)
  return _global_modules


class TracedModuleTestCase(tf.test.TestCase):
  """Compiles a tf.Module to multiple backends to test their correctness."""

  def setUp(self) -> None:
    # Runs before each unit test.
    super().setUp()
    self._modules.ref_module.reinitialize()
    for module in self._modules.tar_modules:
      module.reinitialize()

  def compare_backends(self, trace_function: Callable[[TracedModule], None],
                       modules: Modules) -> None:
    """Run the reference and target backends on trace_function and compare them.

    Random seeds for tensorflow, numpy and python are set before each invocation
    of trace_function.

    Args:
      trace_function: a function accepting a TracedModule as its argument.
    """
    # Create Traces for each backend.
    ref_trace = Trace(modules.ref_module, trace_function)
    tar_traces = [
        Trace(module, trace_function) for module in modules.tar_modules
    ]

    # Run the traces through trace_function with their associated modules.
    tf_utils.set_random_seed()
    trace_function(TracedModule(modules.ref_module, ref_trace))
    if FLAGS.log_all_traces:
      logging.info(ref_trace)
    for module, trace in zip(modules.tar_modules, tar_traces):
      tf_utils.set_random_seed()
      trace_function(TracedModule(module, trace))
      if FLAGS.log_all_traces:
        logging.info(trace)

    # Compare each target trace of trace_function with the reference trace.
    failed_backend_indices = []
    error_messages = []
    for i, tar_trace in enumerate(tar_traces):
      logging.info("Comparing the reference backend '%s' with '%s'",
                   ref_trace.backend_id, tar_trace.backend_id)
      traces_match, errors = Trace.compare_traces(ref_trace, tar_trace)
      if not traces_match:
        failed_backend_indices.append(i)
        error_messages.extend(errors)

    # Save the results to disk before validating.
    ref_trace_dir = _get_trace_dir(modules.artifacts_dir, ref_trace)
    ref_trace.save_plaintext(ref_trace_dir, FLAGS.summarize)
    ref_trace.serialize(ref_trace_dir)
    for tar_trace in tar_traces:
      tar_trace_dir = _get_trace_dir(modules.artifacts_dir, tar_trace)
      tar_trace.save_plaintext(tar_trace_dir, FLAGS.summarize)
      tar_trace.serialize(tar_trace_dir)

    # Validate results.
    if failed_backend_indices:
      # Extract info for logging.
      failed_backends = [
          tar_traces[i].backend_id for i in failed_backend_indices
      ]
      error_list = ''.join([f'\n  - {message}' for message in error_messages])
      self.fail(
          "Comparison between the reference backend and the following targets "
          f"failed: {failed_backends}. Errors: {error_list}\n"
          "See the logs above for more details about the non-matching calls.")

  @classmethod
  def tearDownClass(cls) -> None:
    # Runs after all unit tests are completed.
    super().tearDownClass()
