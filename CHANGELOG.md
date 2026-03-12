# Changelog - Anthropic Performance Take-Home Optimization

## 2026-03-12

### Current Status
- **Baseline Cycles**: 147,734
- **Current Cycles**: 98,582  
- **Speedup**: 1.5x
- **Target**: < 1,363 cycles

---

## Attempt 1: Original Baseline (No Changes)
**Date**: 2026-03-12
**Cycles**: 147,734
**Status**: ✅ Working

- Started with the original provided kernel
- Each operation in its own instruction (no packing)
- All constants loaded inside the loop via `scratch_const()`
- This is the baseline to beat

---

## Attempt 2: VLIW Packing - Simple Grouping
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (Correctness)

**Approach**:
- Grouped all slots by engine type into single bundles
- Example: `{"alu": [slot1, slot2, ...]}`

**Problem**:
- Operations within the same batch item have dependencies
- Example: Must load before using a value in ALU
- Packing them in the same cycle broke correctness
- Got `AssertionError: 854570113 != 0 for (0, 0, 'idx')`

---

## Attempt 3: Vectorization with valu engine
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (Correctness)

**Approach**:
- Tried to use the `valu` engine to process 8 items in parallel
- Each vector operation processes VLEN=8 elements simultaneously

**Problem**:
- Indirect addressing `mem[forest_values_p + idx]` where idx varies per item
- Can't use `scratch_const(idx)` for different values across vector lanes
- The hash computation depends on `val ^ node_val` where node_val comes from memory
- Could not get correct results

---

## Attempt 4: Simple VLIW with Proper Dependencies
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (Correctness)

**Approach**:
- Tried to add only independent operations in the same bundle
- Used `add_vliw()` method to add bundles

**Problem**:
- Still had dependency issues with address computation
- Reused `tmp_idx` register for computing value address before loading
- Result: `AssertionError: Incorrect result on round 1`

**Fix attempted**:
- Added separate `tmp4` register for second address computation
- Still didn't work

---

## Attempt 5: Working VLIW with Conservative Packing
**Date**: 2026-03-12
**Cycles**: 123,158
**Status**: ✅ Working

**Approach**:
- Keep each operation in its own bundle (like baseline)
- But use `add_vliw()` to pack independent operations within an item
- Carefully sequence operations: compute address → load → compute → store
- Only pack operations that don't depend on each other

**Key insight**:
- The original code already packed some operations implicitly
- Need to be VERY careful about register dependencies between cycles
- Each ALU/load/store needs its own cycle when there's a dependency

**Changes made**:
- Created `add_vliw()` method that groups slots by engine
- Packed hash stage ALU operations: 2 per cycle (op1 + op3)
- Kept everything else as single operations per cycle
- This avoided register dependency bugs

**Result**:
- 147,734 → 123,158 cycles
- **Speedup: 1.2x**
- Still far from target of 1,363

---

## Attempt 6: Software Pipelining
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (No improvement)

**Approach**:
- Tried to use two sets of registers for pipelining
- Start loading next item while computing current item

**Result**:
- Still 123,158 cycles - no improvement
- The dependency chain is too tight to pipeline effectively

---

## Attempt 7: Address Pre-computation (Current Best)
**Date**: 2026-03-12
**Cycles**: 98,582
**Status**: ✅ Working

**Approach**:
- Pre-compute memory addresses for idx and val before loading
- Pack both address computations in one cycle
- Use both load slots to load idx and val in parallel
- Use both store slots to store idx and val in parallel

**Changes made**:
- Added separate `idx_addr` and `val_addr` scratch registers
- Compute both addresses in one cycle with packed ALU ops
- Load both idx and val in parallel using 2 load slots
- Store both idx and val in parallel using 2 store slots

**Result**:
- 123,158 → 98,582 cycles
- **Speedup: 1.5x over baseline**
- Still far from target of 1,363 (need 72x more)

---

## Attempt 8: Full Vectorization with vload/vstore
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (IndexError)

**Approach**:
- Try to use vload for loading 8 indices at once
- Use vstore for storing 8 values at once
- Handle indirect node loads via scalar (since they vary per lane)

**Problem**:
- vload expects memory address in scratch, but had issues with address computation
- Index out of range error when accessing memory

---

## Attempt 9: Hybrid with vload for Round 0
**Date**: 2026-03-12
**Cycles**: 97,910
**Status**: ⚠️ Marginal improvement

**Approach**:
- Use vload for round 0 when indices are consecutive (0,1,2,...)
- Fall back to scalar for subsequent rounds

**Result**:
- 98,582 → 97,910 cycles (very marginal improvement)
- The indirect addressing still bottleneck

---

## Attempt 10: valu for Hash
**Date**: 2026-03-12
**Cycles**: 106,786
**Status**: ❌ Made it worse

**Approach**:
- Use valu engine for hash computation
- Broadcast scalar to vector lanes

**Result**:
- Made it SLOWER (106k) because we're doing 8x work for no benefit
- Hash input is scalar so all 8 lanes compute the same result

---

## Submission Test Results

| Test | Target | Status |
|------|--------|--------|
| test_kernel_speedup | < 147,734 | ✅ PASS |
| test_kernel_updated_starting_point | < 18,532 | ❌ FAIL (98k) |
| test_opus4_many_hours | < 2,164 | ❌ FAIL |
| test_opus45_casual | < 1,790 | ❌ FAIL |
| test_opus45_2hr | < 1,579 | ❌ FAIL |
| test_sonnet45_many_hours | < 1,548 | ❌ FAIL |
| test_opus45_11hr | < 1,487 | ❌ FAIL |
| test_opus45_improved_harness | < 1,363 | ❌ FAIL |

---

## Key Insights

### Why is the target so hard to achieve?

1. **Indirect addressing is the main bottleneck**
   - `mem[forest_values_p + idx]` where idx varies per item
   - Prevents full vectorization with vload/vstore
   - Can't use valu engine effectively for multiple items

2. **The target (1,363) requires 72x speedup**
   - Current: 98,582 cycles
   - Target: 1,363 cycles
   - This seems to require a fundamentally different algorithm

3. **What would 5x speedup (18k) require?**
   - Processing 5 items per cycle somehow
   - Or halving the instructions per item

4. **What would 72x speedup (1,363) require?**
   - Processing 72 items per cycle
   - Or 72x fewer instructions
   - This seems to require a mathematical shortcut or pre-computation

### Next Steps (Unlikely to succeed without major breakthrough)

1. **Pre-compute all possible paths through tree**
   - Tree has 2047 nodes - might be feasible
   - Pre-compute hash(node_val) for all nodes
   - Look up instead of compute

2. **Reorder processing**
   - Keep items in order so vload can be used
   - Maybe process in a way that maintains consecutive indices

3. **Lookup table approach**
   - Pre-compute some function results
   - Trade memory for compute

---

## Testing Commands

```bash
# Run local test
python perf_takehome.py

# Run with trace for debugging
python perf_takehome.py Tests.test_kernel_trace

# Run submission tests
python tests/submission_tests.py
```

---

## Attempt 9: Vectorization with vload (In Progress)
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: 🔄 In Progress

**Approach**:
- Try to use vload to load 8 consecutive indices/values at once
- Use valu engine for hash computation
- Process items 8 at a time

**Problem**:
- vload reads from mem[scratch[addr]] - requires a base address in scratch
- After round 0, indices are no longer consecutive (they get modified)
- Cannot easily compute and store new addresses for vload each iteration
- Ran out of scratch space when trying to allocate vector registers

**Key Insight**:
- The indirect addressing pattern `mem[forest_values_p + idx]` is the fundamental bottleneck
- Each of 8 lanes needs a different address - cannot be vectorized with vload
- Need a different approach to break through
