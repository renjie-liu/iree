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

if(NOT ${IREE_TARGET_BACKEND_LLVM-IR})
  return()
endif()

iree_add_all_subdirs()

iree_cc_library(
  NAME
    LLVMAOT
  HDRS
    "LLVMAOTTarget.h"
  SRCS
    "LLVMAOTTarget.cpp"
  DEPS
    ::LLVMAOTTargetLinker
    LLVMAArch64AsmParser
    LLVMAArch64CodeGen
    LLVMARMAsmParser
    LLVMARMCodeGen
    LLVMCore
    LLVMSupport
    LLVMX86AsmParser
    LLVMX86CodeGen
    MLIRTargetLLVMIR
    iree::compiler::Dialect::HAL::Target
    iree::compiler::Dialect::HAL::Target::LLVM::LLVMBaseTarget
    iree::compiler::Dialect::HAL::Target::LLVM::LLVMIRPasses
    iree::compiler::Dialect::HAL::Target::LLVM::LLVMTargetOptions
    iree::schemas::dylib_executable_def_cc_fbs
  PUBLIC
)

iree_cc_library(
  NAME
    LLVMAOTTargetLinker
  HDRS
    "LLVMAOTTargetLinker.h"
  DEPS
    iree::compiler::Dialect::HAL::Target::LLVM::AOT::internal::LLVMAOTTargetLinker_internal
  PUBLIC
)

iree_cc_library(
  NAME
    LLVMAOTTargetLinker_hdrs
  HDRS
    "LLVMAOTTargetLinker.h"
  DEPS
    iree::base::status
    iree::compiler::Dialect::HAL::Target::LLVM::LLVMTargetOptions
  PUBLIC
)
