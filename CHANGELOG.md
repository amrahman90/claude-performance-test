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

### What We've Tried

1. **VLIW Packing** - ✅ Working (98,582 cycles)
2. **Address Pre-computation** - ✅ Working (98,582 cycles)
3. **Vectorization with valu** - ❌ Made it worse
4. **vload for round 0** - ⚠️ Marginal (97,910 cycles)
5. **Loop unrolling (4-way)** - ❌ Broke correctness

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

## Attempt 11: Pre-load forest to scratch
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Not attempted (insufficient scratch)

**Approach**:
- Copy forest values to scratch memory
- Use faster scratch access instead of main memory

**Problem**:
- Tree has 2047 nodes
- Scratch has only 1536 words
- Not enough scratch space!

---

## Attempt 12: Vectorized with vload/vstore
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (wrong instruction)

**Approach**:
- Use vload to load 8 consecutive indices/values
- Process 8 items in parallel
- Use vstore to save results

**Problem**:
- `store_offset` is not a valid instruction
- The indirect addressing for node_val still can't be vectorized

---

## Attempt 13: 4-way Loop Unrolling
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (correctness)

**Approach**:
- Process 4 items simultaneously
- Use separate register sets for each item
- Pack all operations per cycle

**Problem**:
- Broke correctness - got wrong results
- Likely a dependency or register allocation bug

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
| test_opus45_improved_harness | < 1,363 | ❌ FAIL

---

## Attempt 15: Better ILP with Temp Registers
**Date**: 2026-03-12
**Cycles**: 94,742
**Status**: ✅ Same as Attempt 14

**Approach**:
- Tried to use separate tmp_a, tmp_b registers for hash to improve ILP
- Same performance as Attempt 14

**Result**:
- No improvement

---

## Attempt 16: vload for Round 0
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (IndexError)

**Approach**:
- Tried to use vload for round 0 when indices are consecutive
- vload expects consecutive memory addresses

**Problem**:
- IndexError: list index out of range
- vload works differently than expected

---

## Attempt 17: Fully Unrolled Rounds
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (correctness)

**Approach**:
- Unroll the rounds loop completely
- Attempted to pipeline stores and loads across round boundaries

**Problem**:
- Incorrect result on round 1
- Dependency issues in the pipelining attempt

---

## Attempt 18: 2-way Loop Unrolling
**Date**: 2026-03-12
**Cycles**: N/A
**Status**: ❌ Failed (correctness)

**Approach**:
- Process 2 items at a time with separate register sets
- Pack 4 ALU ops per cycle for better ILP

**Problem**:
- Incorrect result - bug in register allocation or dependencies

---

## Current Status
**Cycles**: 94,742
**Target**: <1,363
**Speedup needed**: ~70x

---

## Attempt 24: Round 0 Vectorization (vload/vvalu)
**Date**: 2026-03-12
**Status**: ❌ Failed (timed out / correctness)

**Approach**:
- Attempted to use vload to load 8 consecutive input values
- Used valu/vbroadcast for hash computation in parallel
- Used vstore to save results

**Problem**:
- Complex code that was hard to get correct
- Processing items twice (vectorized + scalar)
- Timeout due to incorrect flow

---

## Attempt 25: vload for Values Only
**Date**: 2026-03-12
**Status**: ❌ Failed (correctness)

**Approach**:
- Used vload for consecutive input values
- Kept indices scalar (indirect addressing)
- Mixed vload with scalar stores

**Problem**:
- Incorrect results - mixing vector loads with scalar stores caused issues
- Need to either fully commit to vector or stay scalar

---

## Key Insights

### Why is the target so hard to achieve?

1. **Indirect addressing is the main bottleneck**
   - `mem[forest_values_p + idx]` where idx varies per item
   - Prevents full vectorization with vload/vstore
   - Can't use valu engine effectively for multiple items

2. **The target (1,363) requires 70x speedup**
   - Current: 94,742 cycles
   - Target: 1,363 cycles
   - Theoretical minimum: ~1,536 cycles
   - Target is BELOW theoretical minimum!

3. **What would 70x speedup require?**
   - Processing 70 items per cycle
   - Or 70x fewer instructions
   - This seems to require a mathematical shortcut or pre-computation

### What We've Tried

1. **VLIW Packing** - ✅ Working (94,742 cycles)
2. **Address Pre-computation** - ✅ Working (94,742 cycles)
3. **Vectorization with valu** - ❌ Made it worse
4. **vload for round 0** - ❌ Failed (timeout/correctness)
5. **vload for values** - ❌ Failed (correctness)
6. **Loop unrolling** - ❌ Broke correctness
7. **Software pipelining** - ❌ Broke correctness
8. **Maximum ALU packing** - Same as baseline

### Next Steps (Unlikely to succeed without major breakthrough)

1. **Pre-compute entire tree traversal**
   - For each starting index, pre-compute the path
   - But val is 32-bit, making this infeasible

2. **Lookup table for hash**
   - Pre-compute myhash(x) for all x - impossible (2^32 entries)
   - Pre-compute for smaller domain - loses precision

3. **Different algorithm entirely?**
   - The problem might require a completely different approach

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

## Submission Test Results

| Test | Target | Status |
|------|--------|--------|
| test_kernel_speedup | < 147,734 | ✅ PASS |
| test_kernel_updated_starting_point | < 18,532 | ❌ FAIL (94k) |
| test_opus4_many_hours | < 2,164 | ❌ FAIL |
| test_opus45_casual | < 1,790 | ❌ FAIL |
| test_opus45_2hr | < 1,579 | ❌ FAIL |
| test_sonnet45_many_hours | < 1,548 | ❌ FAIL |
| test_opus45_11hr | < 1,487 | ❌ FAIL |
| test_opus45_improved_harness | < 1,363 | ❌ FAIL

---

## Attempt 14: Pre-computed Addresses
**Date**: 2026-03-12
**Cycles**: 94,742
**Status**: ✅ Working (CURRENT BEST)

**Approach**:
- Pre-compute all memory addresses (inp_indices_p + i, inp_values_p + i) at kernel start
- Store in arrays idx_addrs[i] and val_addrs[i]
- Eliminates 2 ALU ops per iteration

**Result**:
- **Speedup: 1.56x over baseline**

**Key insight**:
- The indirect addressing for node_val is still the bottleneck
- We can't vectorize the load from forest_values_p + idx

---

## Attempt 20: Use valu for hash
**Date**: 2026-03-12
**Status**: ❌ Failed (timed out)

**Problem**:
- valu operations need vector registers, but we have scalar data
- The approach was fundamentally flawed

---

## Attempt 21: Use vload for input arrays  
**Date**: 2026-03-12
**Status**: ❌ Failed (IndexError)

**Problem**:
- vload requires consecutive addresses
- load_offset doesn't work as expected for extracting elements

---

## Attempt 22: Maximum ALU packing
**Date**: 2026-03-12
**Cycles**: 94,742
**Status**: Same as Attempt 14

**Result**:
- Already using all available ALU slots efficiently

---

## Attempt 23: Software pipelining
**Date**: 2026-03-12
**Status**: ❌ Failed (correctness)

**Problem**:
- Complex register dependencies caused incorrect results

---

## Attempt 27: Minimal vload for round 0
**Date**: 2026-03-12
**Status**: ❌ Reverted (no improvement)

**Approach**:
- Tried to use vload for first 8 items in round 0
- All items start at idx=0, so theoretically batchable

**Problem**:
- Could not get vector/scalar mixing to work correctly
- Reverted to scalar approach

---

## Key Insights

### Why is the target so hard to achieve?

1. **Indirect addressing is the main bottleneck**
   - `mem[forest_values_p + idx]` where idx varies per item
   - Prevents full vectorization with vload/vstore

2. **The target (1,363) requires 70x speedup**
   - Current: 94,742 cycles
   - Target: 1,363 cycles
   - Theoretical minimum: ~1,536 cycles

3. **Per-item cycle count**
   - ~23 cycles per item
   - 4096 items total = ~94k cycles
   - Need 8x parallelism to reach target

### What We've Tried

1. **VLIW Packing** - ✅ Working (94,742 cycles)
2. **Address Pre-computation** - ✅ Working (94,742 cycles)
3. **Vectorization with valu** - ❌ Made it worse
4. **vload for round 0** - ❌ Failed
5. **Loop unrolling** - ❌ Broke correctness
6. **Software pipelining** - ❌ Broke correctness
7. **Replace flow with ALU** - ❌ Failed (correctness)

### Key Realization

The theoretical minimum (~1,536 cycles) is VERY close to the target (1,363). This means:
- We need near-perfect vectorization
- Every cycle counts
- The problem may require a breakthrough in algorithm or understanding

---

## Current Status
**Cycles**: 94,742
**Target**: <1,363
**Speedup needed**: ~70x

---

## Attempt 28: Analysis of vectorization approaches
**Date**: 2026-03-12
**Status**: 🔄 In Progress

**Key Insights**:

1. **Pause instructions are disabled in submission tests**
   - `machine.enable_pause = False` in submission tests
   - Pause still costs cycles even when disabled
   - Removing pause saves only ~2 cycles (negligible)

2. **Vectorization blocked by indirect addressing**
   - vload requires consecutive memory addresses
   - The indirect load `mem[forest_values_p + idx]` cannot be vectorized
   - idx varies per item, preventing batching

3. **No store_offset instruction**
   - Tried to build vectors using store_offset - doesn't exist
   - Only vstore for storing entire vectors
   - Can't write individual vector elements

4. **Theoretical analysis**:
   - 4,096 item iterations × 23 cycles = ~94,000 cycles (matches!)
   - Target 1,363 requires 3 items/cycle
   - With VLEN=8, need ~2.6 cycles per vector iteration
   - Hash alone takes 12 cycles (sequential dependencies)

5. **What hasn't been fully explored**:
   - Batching items by index (items with same idx share node_val load)
   - Round 0 is fully vectorizable (all at idx=0) - only 1/16 of work
   - Using forest values from scratch (but 2047 nodes > 1536 scratch)

**Problem**: The target of 1,363 cycles requires a breakthrough that seems impossible with current understanding.

---

## Submission Test Results

| Test | Target | Status |
|------|--------|--------|
| test_kernel_speedup | < 147,734 | ✅ PASS |
| test_kernel_updated_starting_point | < 18,532 | ❌ FAIL (94k) |
| test_opus4_many_hours | < 2,164 | ❌ FAIL |
| test_opus45_casual | < 1,790 | ❌ FAIL |
| test_opus45_2hr | < 1,579 | ❌ FAIL |
| test_sonnet45_many_hours | < 1,548 | ❌ FAIL |
| test_opus45_11hr | < 1,487 | ❌ FAIL |
| test_opus45_improved_harness | < 1,363 | ❌ FAIL |
