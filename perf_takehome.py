"""
# Anthropic's Original Performance Engineering Take-home (Release version)

Copyright Anthropic PBC 2026. Permission is granted to modify and use, but not
to publish or redistribute your solutions so it's hard to find spoilers.

# Task

- Optimize the kernel (in KernelBuilder.build_kernel) as much as possible in the
  available time, as measured by test_kernel_cycles on a frozen separate copy
  of the simulator.

Validate your results using `python tests/submission_tests.py` without modifying
anything in the tests/ folder.

We recommend you look through problem.py next.
"""

from collections import defaultdict
import random
import unittest

from problem import (
    Engine,
    DebugInfo,
    SLOT_LIMITS,
    VLEN,
    N_CORES,
    SCRATCH_SIZE,
    Machine,
    Tree,
    Input,
    HASH_STAGES,
    reference_kernel,
    build_mem_image,
    reference_kernel2,
)


class KernelBuilder:
    def __init__(self):
        self.instrs = []
        self.scratch = {}
        self.scratch_debug = {}
        self.scratch_ptr = 0
        self.const_map = {}

    def debug_info(self):
        return DebugInfo(scratch_map=self.scratch_debug)

    def add(self, engine, slot):
        """Add a single instruction"""
        self.instrs.append({engine: [slot]})

    def add_vliw(self, slots):
        """Add multiple slots in one bundle, grouped by engine"""
        if not slots:
            return

        by_engine = defaultdict(list)
        for engine, slot in slots:
            by_engine[engine].append(slot)

        bundle = {}
        for eng, slots_list in by_engine.items():
            limit = SLOT_LIMITS.get(eng, 1)
            bundle[eng] = slots_list[:limit]

        if bundle:
            self.instrs.append(bundle)

    def alloc_scratch(self, name=None, length=1):
        addr = self.scratch_ptr
        if name is not None:
            self.scratch[name] = addr
            self.scratch_debug[addr] = (name, length)
        self.scratch_ptr += length
        assert self.scratch_ptr <= SCRATCH_SIZE, "Out of scratch space"
        return addr

    def scratch_const(self, val, name=None):
        if val not in self.const_map:
            addr = self.alloc_scratch(name)
            self.add("load", ("const", addr, val))
            self.const_map[val] = addr
        return self.const_map[val]

    def build_kernel(
        self, forest_height: int, n_nodes: int, batch_size: int, rounds: int
    ):
        """
        Attempt 49: Remove pauses (enable_pause=False in test harness)

        Key insight: Test harness sets enable_pause=False, so pauses don't pause
        the core but still count as cycles. Removing them saves 514 cycles!

        Previous: 18,090 cycles
        Expected: ~17,576 cycles (18,090 - 514)
        """
        # === Allocate scratch ===
        # Vector registers (VLEN=8 consecutive locations each)
        vec_idx = self.alloc_scratch("vec_idx", VLEN)  # 8 idx values
        vec_val = self.alloc_scratch("vec_val", VLEN)  # 8 val values
        vec_node_val = self.alloc_scratch("vec_node_val", VLEN)  # 8 node_val values
        vec_tmp1 = self.alloc_scratch("vec_tmp1", VLEN)
        vec_tmp2 = self.alloc_scratch("vec_tmp2", VLEN)
        vec_tmp3 = self.alloc_scratch("vec_tmp3", VLEN)

        # Scalar temporaries
        tmp1 = self.alloc_scratch("tmp1")
        tmp2 = self.alloc_scratch("tmp2")
        tmp3 = self.alloc_scratch("tmp3")

        # Temporary addresses for node lookups (8 separate addresses)
        tmp_addrs = [self.alloc_scratch(f"tmp_addr_{i}") for i in range(VLEN)]

        # Pre-allocate addresses for base
        base_idx_addr = self.alloc_scratch("base_idx_addr")
        base_val_addr = self.alloc_scratch("base_val_addr")

        # Scratch space for init vars
        init_vars = [
            "rounds",
            "n_nodes",
            "batch_size",
            "forest_height",
            "forest_values_p",
            "inp_indices_p",
            "inp_values_p",
        ]
        for v in init_vars:
            self.alloc_scratch(v, 1)
        for i, v in enumerate(init_vars):
            self.add("load", ("const", tmp1, i))
            self.add("load", ("load", self.scratch[v], tmp1))

        # Pre-load constants
        zero_const = self.scratch_const(0)
        one_const = self.scratch_const(1)

        # Pre-compute hash constants as vector registers (broadcast to all 8 lanes)
        hash_consts_valu = []
        for stage_idx, (_, val1, _, _, val3) in enumerate(HASH_STAGES):
            # Broadcast const1 to all 8 lanes
            const1_base = self.alloc_scratch(f"hc1_{stage_idx}", VLEN)
            for i in range(VLEN):
                self.add("load", ("const", const1_base + i, val1))
            # Broadcast const3 to all 8 lanes
            const3_base = self.alloc_scratch(f"hc3_{stage_idx}", VLEN)
            for i in range(VLEN):
                self.add("load", ("const", const3_base + i, val3))
            hash_consts_valu.append((const1_base, const3_base))

        # Pre-allocate shift constant vector (value = 1)
        shift_const_base = self.alloc_scratch("shift_const", VLEN)
        for i in range(VLEN):
            self.add("load", ("const", shift_const_base + i, 1))

        # Pre-allocate zero vector
        zero_vec_base = self.alloc_scratch("zero_vec", VLEN)
        for i in range(VLEN):
            self.add("load", ("const", zero_vec_base + i, 0))

        # Pre-allocate n_nodes vector (fixed constant, not loaded from memory)
        n_nodes_base = self.alloc_scratch("n_nodes_vec", VLEN)
        for i in range(VLEN):
            self.add("load", ("const", n_nodes_base + i, n_nodes))

        # Pre-compute ALL batch offsets once at startup (KEY OPTIMIZATION)
        n_batches = batch_size // VLEN
        batch_offsets = []
        for batch_start in range(0, batch_size, VLEN):
            batch_offsets.append(self.scratch_const(batch_start))

        # Pre-compute addresses for batch 0
        self.add_vliw(
            [
                (
                    "alu",
                    ("+", base_idx_addr, self.scratch["inp_indices_p"], zero_const),
                ),
                ("alu", ("+", base_val_addr, self.scratch["inp_values_p"], zero_const)),
            ]
        )

        # REMOVED: pause instruction (saves 1 cycle)
        # self.add("flow", ("pause",))

        # Process all rounds - fully unrolled
        # This eliminates loop overhead and allows better instruction scheduling
        for _ in range(rounds):
            # Process items in batches of VLEN=8
            for batch_offset in batch_offsets:
                # Compute base addresses for this batch (use pre-computed offset)
                self.add_vliw(
                    [
                        (
                            "alu",
                            ("+", tmp1, self.scratch["inp_indices_p"], batch_offset),
                        ),
                        (
                            "alu",
                            ("+", tmp2, self.scratch["inp_values_p"], batch_offset),
                        ),
                    ]
                )

                # vload 8 consecutive indices
                self.add("load", ("vload", vec_idx, tmp1))

                # vload 8 consecutive values
                self.add("load", ("vload", vec_val, tmp2))

                # Phase 1: Compute all 8 node addresses in PARALLEL
                # With 12 ALU slots, we can do all 8 address computations in one cycle!
                addr_compute_ops = []
                for i in range(VLEN):
                    addr_compute_ops.append(
                        (
                            "alu",
                            (
                                "+",
                                tmp_addrs[i],
                                self.scratch["forest_values_p"],
                                vec_idx + i,
                            ),
                        )
                    )
                self.add_vliw(addr_compute_ops)

                # Phase 2: Load all 8 node_vals in parallel
                load_ops = []
                for i in range(VLEN):
                    load_ops.append(("load", ("load", vec_node_val + i, tmp_addrs[i])))
                # Bundle as many loads as possible per cycle
                for i in range(0, VLEN, 2):
                    self.add_vliw(load_ops[i : i + 2])

                # XOR: val[i] = val[i] ^ node_val[i] for all 8 items
                self.add("valu", ("^", vec_val, vec_val, vec_node_val))

                # Hash computation using VALU (parallel for all 8 items)
                # Use add_vliw to bundle independent ops in same cycle
                for stage_idx, (c1_base, c3_base) in enumerate(hash_consts_valu):
                    op1, _, op2, op3, _ = HASH_STAGES[stage_idx]

                    # Cycle 1: tmp1 = val + c1, tmp2 = val ^ c3 (parallel in same cycle!)
                    self.add_vliw(
                        [
                            ("valu", (op1, vec_tmp1, vec_val, c1_base)),
                            ("valu", (op3, vec_tmp2, vec_val, c3_base)),
                        ]
                    )

                    # Cycle 2: val = tmp1 op2 tmp2
                    self.add("valu", (op2, vec_val, vec_tmp1, vec_tmp2))

                # Compute new idx: idx = idx*2 + 1 + (val%2)
                # Bundle independent ops for better VALU utilization

                # Cycle 1: idx << 1 and val & 1 (parallel)
                self.add_vliw(
                    [
                        ("valu", ("<<", vec_tmp1, vec_idx, shift_const_base)),
                        ("valu", ("&", vec_tmp2, vec_val, shift_const_base)),
                    ]
                )

                # Cycle 2: 1 + (val & 1) and idx << 1 ready for next
                self.add_vliw(
                    [
                        ("valu", ("+", vec_tmp3, vec_tmp2, shift_const_base)),
                    ]
                )

                # Cycle 3: idx = (idx << 1) + (1 + (val & 1))
                self.add("valu", ("+", vec_idx, vec_tmp1, vec_tmp3))

                # Cycle 4: bounds check
                self.add("valu", ("<", vec_tmp1, vec_idx, n_nodes_base))

                # Cycle 5: vselect wrap
                self.add("flow", ("vselect", vec_idx, vec_tmp1, vec_idx, zero_vec_base))

                # vstore 8 consecutive indices
                self.add("store", ("vstore", tmp1, vec_idx))

                # vstore 8 consecutive values
                self.add("store", ("vstore", tmp2, vec_val))

        # REMOVED: final pause (saves 1 cycle)
        # Also removed per-batch pauses (saves 512 cycles)
        # Total savings: 514 cycles!


BASELINE = 147734


def do_kernel_test(
    forest_height: int,
    rounds: int,
    batch_size: int,
    seed: int = 123,
    trace: bool = False,
    prints: bool = False,
):
    print(f"{forest_height=}, {rounds=}, {batch_size=}")
    random.seed(seed)
    forest = Tree.generate(forest_height)
    inp = Input.generate(forest, batch_size, rounds)
    mem = build_mem_image(forest, inp)

    kb = KernelBuilder()
    kb.build_kernel(forest.height, len(forest.values), len(inp.indices), rounds)

    value_trace = {}
    machine = Machine(
        mem,
        kb.instrs,
        kb.debug_info(),
        n_cores=N_CORES,
        value_trace=value_trace,
        trace=trace,
    )
    machine.prints = prints
    for i, ref_mem in enumerate(reference_kernel2(mem, value_trace)):
        machine.run()
        inp_values_p = ref_mem[6]
        if prints:
            print(machine.mem[inp_values_p : inp_values_p + len(inp.values)])
            print(ref_mem[inp_values_p : inp_values_p + len(inp.values)])
        assert (
            machine.mem[inp_values_p : inp_values_p + len(inp.values)]
            == ref_mem[inp_values_p : inp_values_p + len(inp.values)]
        ), f"Incorrect result on round {i}"
        inp_indices_p = ref_mem[5]
        if prints:
            print(machine.mem[inp_indices_p : inp_indices_p + len(inp.indices)])
            print(ref_mem[inp_indices_p : inp_indices_p + len(inp.indices)])

    print("CYCLES: ", machine.cycle)
    print("Speedup over baseline: ", BASELINE / machine.cycle)
    return machine.cycle


class Tests(unittest.TestCase):
    def test_ref_kernels(self):
        random.seed(123)
        for i in range(10):
            f = Tree.generate(4)
            inp = Input.generate(f, 10, 6)
            mem = build_mem_image(f, inp)
            reference_kernel(f, inp)
            for _ in reference_kernel2(mem, {}):
                pass
            assert inp.indices == mem[mem[5] : mem[5] + len(inp.indices)]
            assert inp.values == mem[mem[6] : mem[6] + len(inp.values)]

    def test_kernel_trace(self):
        do_kernel_test(10, 16, 256, trace=True, prints=False)

    def test_kernel_cycles(self):
        do_kernel_test(10, 16, 256)


if __name__ == "__main__":
    unittest.main()
