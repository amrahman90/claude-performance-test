# Anthropic Performance Take-Home Optimization Plan

## Problem Overview

This is Anthropic's Performance Engineering Take-Home Challenge. The goal is to optimize a simulated VLIW SIMD machine's kernel to minimize clock cycles.

### Current Status (BREAKTHROUGH ACHIEVED!)
- **Cycles**: 14,504 (VALU vectorization approach - Attempt 50)
- **Speedup**: 10.2x over baseline
- **Target**: < 1,363 cycles (hardest target)
- **Test parameters**: forest_height=10, rounds=16, batch_size=256

---

## Key Breakthrough: VALU Vectorization

The breakthrough was using the VALU (vector) engine to process 8 items in parallel:

### What Made It Work

1. **vload/vstore for consecutive data**: Load 8 indices/values at once
2. **VALU for hash computation**: Compute hash for 8 items in parallel  
3. **Batched address computation**: Compute all 8 node addresses in a single cycle using all 12 ALU slots
4. **Batched loads**: Bundle load operations to use both load slots efficiently
5. **Pre-allocated vector constants**: Broadcast constants to all 8 lanes once at startup
6. **Bundle VALU ops**: Use add_vliw to pack multiple VALU ops in same cycle (key optimization!)

### Cycle Breakdown per Batch (8 items)

| Phase | Cycles | Notes |
|-------|--------|-------|
| vload indices | 1 | Load 8 consecutive indices |
| vload values | 1 | Load 8 consecutive values |
| Compute 8 addresses | 1 | Bundled in one add_vliw (uses 12 ALU slots) |
| Load 8 node_vals | 4 | 2 loads/cycle, 4 cycles |
| XOR (valu) | 1 | Vector XOR |
| Hash (valu) | 12 | 6 stages × 2 cycles each |
| idx computation | 5 | Vector bitwise + arithmetic |
| vstore indices | 1 | Store 8 results |
| vstore values | 1 | Store 8 results |
| **Total** | **~27** | Per batch of 8 items |

---

## Latest Discoveries (Attempt 50)

### VALU Bundling Discovery
- **Discovery**: Using add_vliw() to bundle multiple VALU ops in same cycle
- **Key insight**: The add() method creates separate instructions, add_vliw() bundles them
- **Implementation**: Bundle op1 + op3 for each hash stage, and idx << + idx & operations
- **Savings**: 3,584 cycles (20% improvement!)

### Pause Instruction Analysis
- **Discovery**: Test harness sets `machine.enable_pause = False`
- **Finding**: Pauses don't pause execution but still count as cycles
- **Action**: Removed unnecessary pause instructions
- **Savings**: 2 cycles (minimal compared to VALU bundling)

### Instruction Breakdown Analysis
- Total instructions: 18,088 (equals cycle count)
- load: 3239, alu: 1025, valu: 12288, flow: 512, store: 1024
- 512 flow = vselect operations (1 per batch)
- No pause instructions in current kernel (removed)

### Hash Optimization Analysis
- 6 stages × 2 cycles = 12 cycles per batch minimum
- Cannot reduce further due to dependencies: each stage uses result of previous
- multiply_add doesn't help for this algorithm

---

## Relevant Python Skills

| Skill | Purpose | Application |
|-------|---------|-------------|
| `python-performance-optimization` | Profile and optimize Python code | Measuring cycle counts, benchmarking optimizations |
| `python-fundamentals` | Modern Python patterns | Clean kernel building code |
| `python-design-patterns` | KISS, separation of concerns | Structuring the KernelBuilder class |

---

## Architecture Understanding

### Machine Model (VLIW SIMD)

| Engine | Slots/Cycle | Purpose |
|--------|-------------|---------|
| alu | 12 | Scalar arithmetic operations |
| valu | 6 | Vector operations (8 elements in parallel) |
| load | 2 | Memory reads |
| store | 2 | Memory writes |
| flow | 1 | Control flow (jumps, selects, halt) |

### Key Parameters
- **VLEN** = 8 (vector length - processes 8 elements at once)
- **N_CORES** = 1 (single core)
- **SCRATCH_SIZE** = 1536 (local register file)

### Key Vector Instructions Discovered

| Instruction | Engine | Purpose |
|------------|--------|---------|
| vload | load | Load VLEN consecutive words from memory |
| vstore | store | Store VLEN consecutive words to memory |
| vbroadcast | valu | Broadcast scalar to all VLEN lanes |
| vselect | flow | Vector conditional select |
| valu ops | valu | Element-wise vector operations |

---

## Algorithm Analysis

### Reference Kernel Logic
```
For each round:
  For each item in batch:
    1. Load index from memory
    2. Load value from memory  
    3. Load node value from tree using index
    4. val = myhash(val XOR node_val)
    5. Determine branch: if val is even → left child, else → right child
    6. Compute new index: new_idx = 2*idx + (1 if even else 2)
    7. Wrap around if >= n_nodes
    8. Store updated index and value
```

### Hash Function (6 stages)
```python
HASH_STAGES = [
    ("+", 0x7ED55D16, "+", "<<", 12),
    ("^", 0xC761C23C, "^", ">>", 19),
    ("+", 0x165667B1, "+", "<<", 5),
    ("+", 0xD3A2646C, "^", "<<", 9),
    ("+", 0xFD7046C5, "+", "<<", 3),
    ("^", 0xB55A4F09, "^", ">>", 16),
]
```

Each stage: `a = (op2(op1(a, val1)) ^ (op3(a, val3))) % 2^32`

---

## Submission Test Results

| Test | Target | Status | Cycles |
|------|--------|--------|--------|
| test_kernel_correctness | Correct output | ✅ PASS | - |
| test_kernel_speedup | < 147,734 | ✅ PASS | 14,504 |
| test_kernel_updated_starting_point | < 18,532 | ✅ PASS | 14,504 |
| test_opus4_many_hours | < 2,164 | ❌ FAIL | 14,504 |
| test_opus45_casual | < 1,790 | ❌ FAIL | 14,504 |
| test_opus45_2hr | < 1,579 | ❌ FAIL | 14,504 |
| test_sonnet45_many_hours | < 1,548 | ❌ FAIL | 14,504 |
| test_opus45_11hr | < 1,487 | ❌ FAIL | 14,504 |
| test_opus45_improved_harness | < 1,363 | ❌ FAIL | 14,504 |

**Achievement**: We've passed test_kernel_updated_starting_point with 10.2x speedup! This was the main milestone.

---

## Optimization Journey

### What Didn't Work (Key Learnings)

| Attempt | Approach | Result | Learning |
|---------|----------|--------|----------|
| VLIW Packing | Group independent ops | ✅ 123k | Limited by dependencies |
| Address Pre-computation | Pre-calc addresses | ✅ 98k | Saves 2 ALU ops/item |
| Optimized idx formula | `idx*2 + 1 + (val%2)` | ✅ 90k | Current best scalar |
| Vectorization (valu) - early | Process 8 items parallel | ❌ Failed | Complex address computation |
| 2-way Loop Unrolling | Process 2 items/iter | ⚠️ No change | Dependencies prevent parallelism |
| Software Pipelining | Overlap iterations | ❌ Failed | Correctness bugs |
| Bit Operations | Replace `%2` with `&1` | ❌ No change | Hash is bottleneck |
| vload for Round 0 | Vectorize first 8 items | ❌ Failed | Mixing vector/scalar issues |

### What Finally Worked (Attempt 41)

The key insight was that while we can't vectorize the indirect addressing pattern directly, we CAN:
1. Batch the address computation using all 12 ALU slots
2. Use vload for the consecutive input arrays
3. Use VALU for hash computation (since all 8 items compute hash simultaneously)
4. Use vstore for consecutive output

### Key Technical Insights

1. **scratch_const() returns a scratch address**, not an immediate value
2. **To use VALU properly**, must allocate VLEN consecutive scratch locations
3. **Broadcast constants** by loading them into all VLEN lanes at startup
4. **Bundle operations** using add_vliw to maximize parallelism
5. **Use separate temporary addresses** for each of 8 parallel loads

---

## Theoretical Analysis

- **Baseline**: 147,734 cycles
- **Previous Best (scalar)**: 90,646 cycles (1.63x speedup)
- **VALU Vectorization**: 18,088 cycles (8.2x speedup)
- **Current (Bundled VALU)**: 14,504 cycles (10.2x speedup)
- **Target (hardest)**: < 1,363 cycles (need ~10x more)
- **Theoretical Minimum**: ~11,264 cycles (hash + memory + overhead with perfect bundling)

**Key Finding**: The hardest target (1,363) is still below our theoretical minimum (~11,264), suggesting it requires a fundamentally different algorithm or additional optimizations not yet discovered.

**Note**: Attempt 50's VALU bundling was a major breakthrough, reducing cycles by 20% (3,584 cycles saved).

---

## Testing Commands

```bash
# Run local test
python perf_takehome.py

# Run submission tests
python tests/submission_tests.py

# Run with trace for debugging
python perf_takehome.py Tests.test_kernel_trace

# Run correctness tests only
python -m pytest tests/submission_tests.py::CorrectnessTests -v
```

---

## References

- SLOT_LIMITS in problem.py: alu=12, valu=6, load=2, store=2, flow=1
- VLEN = 8 (vector width)
- See problem.py for full ISA documentation
