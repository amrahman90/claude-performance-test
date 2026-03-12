# Anthropic Performance Take-Home Optimization Plan

## Problem Overview

This is Anthropic's Performance Engineering Take-Home Challenge. The goal is to optimize a simulated VLIW SIMD machine's kernel to minimize clock cycles.

### Current Baseline
- **Cycles**: 147,734 (scalar implementation, no parallelism)
- **Target**: < 1,363 cycles (best human performance)
- **Test parameters**: forest_height=10, rounds=16, batch_size=256

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

### Instruction Format
Instructions are dictionaries with engine names as keys:
```python
{"valu": [("*", 4, 0, 0), ("+", 8, 4, 0)], "load": [("load", 16, 17)]}
```

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

## Experiments & Findings

### Attempt 1: Simple VLIW Packing
- **Approach**: Pack multiple slots into same instruction
- **Result**: Broke correctness - operations have dependencies within same batch item
- **Issue**: Can't pack operations that depend on each other

### Attempt 2: Vectorized Kernel
- **Approach**: Use valu engine to process 8 items in parallel
- **Result**: Incorrect results
- **Issue**: Complex - can't easily compute different addresses for each vector lane using scratch constants

### Key Insights
1. **scratch_const() returns a scratch address**, not an immediate value
2. **ALU operations work on scratch addresses**, not immediate values
3. **To use vectorization properly**, need to rethink address computation
4. **Indirect indexing is hard with vectors** - each of 8 items has different index

---

## Current Status
- Baseline: 147,734 cycles (working)
- Attempted vectorization: Failed due to complexity of address computation
- Next steps needed: Simpler optimizations

---

## Potential Optimization Approaches

### Option 1: Loop Unrolling
Unroll the inner loop to reduce loop overhead and enable better packing.

### Option 2: Constant Pre-loading
Move constant loads outside loops (already partially done).

### Option 3: Smarter VLIW Packing
Only pack operations from DIFFERENT iterations of the inner loop.

### Option 4: Partial Vectorization
Vectorize only the hash computation (which doesn't need indirect addressing).

---

## Testing Commands

```bash
# Run local test
python perf_takehome.py

# Run submission tests
python tests/submission_tests.py

# Run with trace for debugging
python perf_takehome.py Tests.test_kernel_trace
# Then: python watch_trace.py
```

---

## Current Status (Latest)
- **Baseline**: 147,734 cycles
- **Current Best**: 98,582 cycles
- **Speedup**: 1.5x
- **Target**: < 1,363 cycles
- **Gap**: Need 72x more speedup

---

## Key Learnings

1. **VLIW packing helps but limited**: We can pack independent operations, but dependencies limit parallelism
2. **Indirect addressing is the bottleneck**: The pattern `mem[forest_values_p + idx]` prevents full vectorization
3. **Need valu engine to reach target**: Scalar optimization alone can't achieve 72x speedup

---

## References

- SLOT_LIMITS in problem.py: alu=12, valu=6, load=2, store=2, flow=1
- VLEN = 8 (vector width)
- See problem.py for full ISA documentation
