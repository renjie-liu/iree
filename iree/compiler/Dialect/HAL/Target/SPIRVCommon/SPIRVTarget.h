// Copyright 2020 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef IREE_COMPILER_DIALECT_HAL_TARGET_SPIRVCOMMON_SPIRVTARGET_H_
#define IREE_COMPILER_DIALECT_HAL_TARGET_SPIRVCOMMON_SPIRVTARGET_H_

#include <string>

#include "iree/compiler/Conversion/LinalgToSPIRV/Passes.h"
#include "iree/compiler/Dialect/HAL/Target/TargetBackend.h"
#include "mlir/Dialect/SPIRV/SPIRVAttributes.h"
#include "mlir/Dialect/SPIRV/SPIRVOps.h"

namespace mlir {
namespace iree_compiler {
namespace IREE {
namespace HAL {

// A SPIR-V target backend that shares common overrides for Vulkan and Metal.
class SPIRVTargetBackend : public TargetBackend {
 public:
  explicit SPIRVTargetBackend(SPIRVCodegenOptions options);

  void declareTargetOpsForEnv(IREE::Flow::ExecutableOp sourceOp,
                              IREE::HAL::ExecutableOp executableOp,
                              spirv::TargetEnvAttr spvTargetEnv);

  void buildTranslationPassPipeline(OpPassManager &passManager) override;

  LogicalResult recordDispatch(Location loc, DispatchState dispatchState,
                               DeviceSwitchRewriter &switchRewriter) override;

  // Finds the spv.ExecutionMode operation to get the workgroup size from.
  std::array<Value, 3> calculateDispatchWorkgroupSize(
      Location loc, IREE::HAL::ExecutableOp executableOp,
      IREE::HAL::ExecutableEntryPointOp entryPointOp, Value workload,
      OpBuilder &builder) override;

 private:
  std::array<Value, 3> calculateDispatchWorkgroupSize(
      Location loc, spirv::ModuleOp spvModuleOp, StringRef entryPointName,
      Value workload, OpBuilder &builder);

  SPIRVCodegenOptions spvCodeGenOptions_;
};

}  // namespace HAL
}  // namespace IREE
}  // namespace iree_compiler
}  // namespace mlir

#endif  // IREE_COMPILER_DIALECT_HAL_TARGET_SPIRVCOMMON_SPIRVTARGET_H_
