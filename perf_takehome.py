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
        Attempt 59: Multi-Round Processing (4 rounds at a time)

        Key insight: Process 4 rounds of computation WITHOUT storing intermediate results.
        This eliminates 75% of memory traffic between rounds.

        Previous: 12,620 cycles (2 rounds at a time)
        Current: TBD
        """
        # === Allocate scratch ===
        # Vector registers
        vec_idx = self.alloc_scratch("vec_idx", VLEN)  # current idx
        vec_val = self.alloc_scratch("vec_val", VLEN)  # current val
        vec_node_val = self.alloc_scratch("vec_node_val", VLEN)  # node value

        # Temporary vectors (reuse for all rounds)
        vec_tmp1 = self.alloc_scratch("vec_tmp1", VLEN)
        vec_tmp2 = self.alloc_scratch("vec_tmp2", VLEN)
        vec_tmp3 = self.alloc_scratch("vec_tmp3", VLEN)

        # Scalar temporaries
        tmp1 = self.alloc_scratch("tmp1")
        tmp2 = self.alloc_scratch("tmp2")
        tmp3 = self.alloc_scratch("tmp3")

        # Temporary addresses for node lookups
        tmp_addrs = [self.alloc_scratch(f"tmp_addr_{i}") for i in range(VLEN)]

        # OPTIMIZATION: Only load the 3 pointers we actually need from memory
        # We don't need: rounds, n_nodes, batch_size, forest_height
        # (rounds is used for loop count which we unroll, n_nodes is passed as param)
        init_vars = [
            "forest_values_p",  # mem[4]
            "inp_indices_p",  # mem[5]
            "inp_values_p",  # mem[6]
        ]
        # Allocate space but don't load yet - we'll load directly from memory addresses
        for v in init_vars:
            self.alloc_scratch(v, 1)

        # Load only the 3 pointers we need (6 cycles instead of 14)
        # Memory layout: [rounds, n_nodes, batch_size, forest_height, fv_p, idx_p, val_p, ...]
        # So forest_values_p is at mem[4], etc.
        for i, v in enumerate(init_vars):
            mem_addr = 4 + i  # forest_values_p=4, inp_indices_p=5, inp_values_p=6
            self.add("load", ("const", tmp1, mem_addr))
            self.add("load", ("load", self.scratch[v], tmp1))

        # Pre-load constants
        zero_const = self.scratch_const(0)

        # Pre-compute hash constants using VBROADCAST
        # For stages 0, 2, 4: we can use multiply_add to combine 2 ops into 1
        # Stage 0: (val + c1) + (val << 12) = val * 4097 + c1
        # Stage 2: (val + c1) + (val << 5) = val * 33 + c1
        # Stage 4: (val + c1) + (val << 3) = val * 9 + c1
        hash_consts_valu = []
        mul_constants = {0: 4097, 2: 33, 4: 9}  # Stages where op2 is "+"

        for stage_idx, (_, val1, _, _, val3) in enumerate(HASH_STAGES):
            const1_scalar = self.scratch_const(val1)
            const3_scalar = self.scratch_const(val3)
            const1_base = self.alloc_scratch(f"hc1_{stage_idx}", VLEN)
            const3_base = self.alloc_scratch(f"hc3_{stage_idx}", VLEN)
            self.add("valu", ("vbroadcast", const1_base, const1_scalar))
            self.add("valu", ("vbroadcast", const3_base, const3_scalar))

            # Pre-compute multiply constant if applicable
            if stage_idx in mul_constants:
                mul_val = mul_constants[stage_idx]
                mul_scalar = self.scratch_const(mul_val)
                mul_base = self.alloc_scratch(f"mul_{stage_idx}", VLEN)
                self.add("valu", ("vbroadcast", mul_base, mul_scalar))
                hash_consts_valu.append((const1_base, const3_base, mul_base, True))
            else:
                hash_consts_valu.append((const1_base, const3_base, None, False))

        # Pre-allocate shift constant vector
        shift_const_base = self.alloc_scratch("shift_const", VLEN)
        shift_scalar = self.scratch_const(1)
        self.add("valu", ("vbroadcast", shift_const_base, shift_scalar))

        # Pre-allocate zero vector (reuse zero_const)
        zero_vec_base = self.alloc_scratch("zero_vec", VLEN)
        self.add("valu", ("vbroadcast", zero_vec_base, zero_const))

        # Pre-allocate n_nodes vector
        n_nodes_base = self.alloc_scratch("n_nodes_vec", VLEN)
        n_nodes_scalar = self.scratch_const(n_nodes)
        self.add("valu", ("vbroadcast", n_nodes_base, n_nodes_scalar))

        # Pre-compute batch offsets
        batch_offsets = []
        for batch_start in range(0, batch_size, VLEN):
            batch_offsets.append(self.scratch_const(batch_start))

        # Helper function to process one round
        def add_round_computation(round_num):
            """Add computation for one round, reusing vec_tmp1/2/3 and tmp_addrs"""
            # Phase 1: Compute node addresses
            addr_compute = []
            for i in range(VLEN):
                addr_compute.append(
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
            self.add_vliw(addr_compute)

            # Phase 2: Load node vals
            load_ops = []
            for i in range(VLEN):
                load_ops.append(("load", ("load", vec_node_val + i, tmp_addrs[i])))
            for i in range(0, VLEN, 2):
                self.add_vliw(load_ops[i : i + 2])

            # XOR
            self.add("valu", ("^", vec_val, vec_val, vec_node_val))

            # Hash computation - use multiply_add for stages 0, 2, 4
            for stage_idx, (c1_base, c3_base, mul_base, use_mul) in enumerate(
                hash_consts_valu
            ):
                op1, _, op2, op3, _ = HASH_STAGES[stage_idx]
                if use_mul:
                    # multiply_add: dest = (a * b + c) mod 2^32
                    # For stages 0, 2, 4: (val + c1) + (val << shift) = val * mul_const + c1
                    self.add(
                        "valu", ("multiply_add", vec_val, vec_val, mul_base, c1_base)
                    )
                else:
                    self.add_vliw(
                        [
                            ("valu", (op1, vec_tmp1, vec_val, c1_base)),
                            ("valu", (op3, vec_tmp2, vec_val, c3_base)),
                        ]
                    )
                    self.add("valu", (op2, vec_val, vec_tmp1, vec_tmp2))

            # Compute new idx
            self.add_vliw(
                [
                    ("valu", ("<<", vec_tmp1, vec_idx, shift_const_base)),
                    ("valu", ("&", vec_tmp2, vec_val, shift_const_base)),
                ]
            )
            self.add_vliw(
                [
                    ("valu", ("+", vec_tmp3, vec_tmp2, shift_const_base)),
                ]
            )
            self.add("valu", ("+", vec_idx, vec_tmp1, vec_tmp3))
            self.add("valu", ("<", vec_tmp1, vec_idx, n_nodes_base))
            self.add("flow", ("vselect", vec_idx, vec_tmp1, vec_idx, zero_vec_base))

        # Process all rounds at once (16 rounds) - maximum optimization
        # Each batch processes all 16 rounds without any intermediate stores
        for batch_offset in batch_offsets:
            # Compute base addresses
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

            # Initial load
            self.add_vliw(
                [
                    ("load", ("vload", vec_idx, tmp1)),
                    ("load", ("vload", vec_val, tmp2)),
                ]
            )

            # Process ALL rounds
            for _ in range(rounds):
                add_round_computation(0)

            # Store final results
            self.add_vliw(
                [
                    ("store", ("vstore", tmp1, vec_idx)),
                    ("store", ("vstore", tmp2, vec_val)),
                ]
            )

        # Add halt
        self.add("flow", ("halt",))


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
