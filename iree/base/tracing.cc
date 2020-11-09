// Copyright 2019 Google LLC
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

#include "iree/base/tracing.h"

#include "iree/base/target_platform.h"

// Textually include the Tracy implementation.
// We do this here instead of relying on an external build target so that we can
// ensure our configuration specified in tracing.h is picked up.
#if IREE_TRACING_FEATURES != 0
#include "third_party/tracy/TracyClient.cpp"
#endif  // IREE_TRACING_FEATURES

#ifdef __cplusplus
extern "C" {
#endif  // __cplusplus

#if defined(TRACY_ENABLE) && defined(IREE_PLATFORM_WINDOWS)
static HANDLE iree_dbghelp_mutex;
void IREEDbgHelpInit() { iree_dbghelp_mutex = CreateMutex(NULL, FALSE, NULL); }
void IREEDbgHelpLock() { WaitForSingleObject(iree_dbghelp_mutex, INFINITE); }
void IREEDbgHelpUnlock() { ReleaseMutex(iree_dbghelp_mutex); }
#endif  // TRACY_ENABLE && IREE_PLATFORM_WINDOWS

#if IREE_TRACING_FEATURES != 0

void iree_tracing_set_thread_name_impl(const char* name) {
  tracy::SetThreadName(name);
}

iree_zone_id_t iree_tracing_zone_begin_impl(
    const iree_tracing_location_t* src_loc, const char* name,
    size_t name_length) {
  const iree_zone_id_t zone_id = tracy::GetProfiler().GetNextZoneId();

#ifndef TRACY_NO_VERIFY
  {
    TracyLfqPrepareC(tracy::QueueType::ZoneValidation);
    tracy::MemWrite(&item->zoneValidation.id, zone_id);
    TracyLfqCommitC;
  }
#endif  // TRACY_NO_VERIFY

  {
#if IREE_TRACING_FEATURES & IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS
    TracyLfqPrepareC(tracy::QueueType::ZoneBeginCallstack);
#else
    TracyLfqPrepareC(tracy::QueueType::ZoneBegin);
#endif  // IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS
    tracy::MemWrite(&item->zoneBegin.time, tracy::Profiler::GetTime());
    tracy::MemWrite(&item->zoneBegin.srcloc,
                    reinterpret_cast<uint64_t>(src_loc));
    TracyLfqCommitC;
  }

#if IREE_TRACING_FEATURES & IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS
  tracy::GetProfiler().SendCallstack(IREE_TRACING_MAX_CALLSTACK_DEPTH);
#endif  // IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS

  if (name_length) {
#ifndef TRACY_NO_VERIFY
    {
      TracyLfqPrepareC(tracy::QueueType::ZoneValidation);
      tracy::MemWrite(&item->zoneValidation.id, zone_id);
      TracyLfqCommitC;
    }
#endif  // TRACY_NO_VERIFY
    auto name_ptr = reinterpret_cast<char*>(tracy::tracy_malloc(name_length));
    memcpy(name_ptr, name, name_length);
    TracyLfqPrepareC(tracy::QueueType::ZoneName);
    tracy::MemWrite(&item->zoneTextFat.text,
                    reinterpret_cast<uint64_t>(name_ptr));
    tracy::MemWrite(&item->zoneTextFat.size,
                    static_cast<uint64_t>(name_length));
    TracyLfqCommitC;
  }

  return zone_id;
}

iree_zone_id_t iree_tracing_zone_begin_external_impl(
    const char* file_name, size_t file_name_length, uint32_t line,
    const char* function_name, size_t function_name_length, const char* name,
    size_t name_length) {
  uint64_t src_loc = tracy::Profiler::AllocSourceLocation(
      line, file_name, file_name_length, function_name, function_name_length,
      name, name_length);

  const iree_zone_id_t zone_id = tracy::GetProfiler().GetNextZoneId();

#ifndef TRACY_NO_VERIFY
  {
    TracyLfqPrepareC(tracy::QueueType::ZoneValidation);
    tracy::MemWrite(&item->zoneValidation.id, zone_id);
    TracyLfqCommitC;
  }
#endif  // TRACY_NO_VERIFY

  {
#if IREE_TRACING_FEATURES & IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS
    TracyLfqPrepareC(tracy::QueueType::ZoneBeginAllocSrcLocCallstack);
#else
    TracyLfqPrepareC(tracy::QueueType::ZoneBeginAllocSrcLoc);
#endif  // IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS
    tracy::MemWrite(&item->zoneBegin.time, tracy::Profiler::GetTime());
    tracy::MemWrite(&item->zoneBegin.srcloc, src_loc);
    TracyLfqCommitC;
  }

#if IREE_TRACING_FEATURES & IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS
  tracy::GetProfiler().SendCallstack(IREE_TRACING_MAX_CALLSTACK_DEPTH);
#endif  // IREE_TRACING_FEATURE_INSTRUMENTATION_CALLSTACKS

  return zone_id;
}

void iree_tracing_set_plot_type_impl(const char* name_literal,
                                     uint8_t plot_type) {
  tracy::Profiler::ConfigurePlot(name_literal,
                                 static_cast<tracy::PlotFormatType>(plot_type));
}

void iree_tracing_plot_value_i64_impl(const char* name_literal, int64_t value) {
  tracy::Profiler::PlotData(name_literal, value);
}

void iree_tracing_plot_value_f32_impl(const char* name_literal, float value) {
  tracy::Profiler::PlotData(name_literal, value);
}

void iree_tracing_plot_value_f64_impl(const char* name_literal, double value) {
  tracy::Profiler::PlotData(name_literal, value);
}

void iree_tracing_mutex_announce(const iree_tracing_location_t* src_loc,
                                 uint32_t* out_lock_id) {
  uint32_t lock_id =
      tracy::GetLockCounter().fetch_add(1, std::memory_order_relaxed);
  assert(lock_id != std::numeric_limits<uint32_t>::max());
  *out_lock_id = lock_id;

  auto item = tracy::Profiler::QueueSerial();
  tracy::MemWrite(&item->hdr.type, tracy::QueueType::LockAnnounce);
  tracy::MemWrite(&item->lockAnnounce.id, lock_id);
  tracy::MemWrite(&item->lockAnnounce.time, tracy::Profiler::GetTime());
  tracy::MemWrite(&item->lockAnnounce.lckloc,
                  reinterpret_cast<uint64_t>(src_loc));
  tracy::MemWrite(&item->lockAnnounce.type, tracy::LockType::Lockable);
  tracy::Profiler::QueueSerialFinish();
}

void iree_tracing_mutex_terminate(uint32_t lock_id) {
  auto item = tracy::Profiler::QueueSerial();
  tracy::MemWrite(&item->hdr.type, tracy::QueueType::LockTerminate);
  tracy::MemWrite(&item->lockTerminate.id, lock_id);
  tracy::MemWrite(&item->lockTerminate.time, tracy::Profiler::GetTime());
  tracy::Profiler::QueueSerialFinish();
}

void iree_tracing_mutex_before_lock(uint32_t lock_id) {
  auto item = tracy::Profiler::QueueSerial();
  tracy::MemWrite(&item->hdr.type, tracy::QueueType::LockWait);
  tracy::MemWrite(&item->lockWait.thread, tracy::GetThreadHandle());
  tracy::MemWrite(&item->lockWait.id, lock_id);
  tracy::MemWrite(&item->lockWait.time, tracy::Profiler::GetTime());
  tracy::Profiler::QueueSerialFinish();
}

void iree_tracing_mutex_after_lock(uint32_t lock_id) {
  auto item = tracy::Profiler::QueueSerial();
  tracy::MemWrite(&item->hdr.type, tracy::QueueType::LockObtain);
  tracy::MemWrite(&item->lockObtain.thread, tracy::GetThreadHandle());
  tracy::MemWrite(&item->lockObtain.id, lock_id);
  tracy::MemWrite(&item->lockObtain.time, tracy::Profiler::GetTime());
  tracy::Profiler::QueueSerialFinish();
}

void iree_tracing_mutex_after_unlock(uint32_t lock_id) {
  auto item = tracy::Profiler::QueueSerial();
  tracy::MemWrite(&item->hdr.type, tracy::QueueType::LockRelease);
  tracy::MemWrite(&item->lockRelease.thread, tracy::GetThreadHandle());
  tracy::MemWrite(&item->lockRelease.id, lock_id);
  tracy::MemWrite(&item->lockRelease.time, tracy::Profiler::GetTime());
  tracy::Profiler::QueueSerialFinish();
}

#endif  // IREE_TRACING_FEATURES

#ifdef __cplusplus
}  // extern "C"
#endif  // __cplusplus

#if defined(__cplusplus) && \
    (IREE_TRACING_FEATURES & IREE_TRACING_FEATURE_ALLOCATION_TRACKING)

void* operator new(size_t count) noexcept {
  auto ptr = malloc(count);
  IREE_TRACE_ALLOC(ptr, count);
  return ptr;
}

void operator delete(void* ptr) noexcept {
  IREE_TRACE_FREE(ptr);
  free(ptr);
}

#endif  // __cplusplus && IREE_TRACING_FEATURE_ALLOCATION_TRACKING
